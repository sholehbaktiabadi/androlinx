"""The main window: devices on the left, the selected phone on the right.

The shape of the window follows the shape of the job. Picking a device and
picking how to mirror it are two different decisions, so they get two panes; and
because scrcpy opens its own window, anything already running is listed under
the devices rather than hidden behind them.
"""

from __future__ import annotations

import shlex
from dataclasses import replace

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, Gtk, Pango  # noqa: E402

from . import adb, profiles as profile_store, scrcpy  # noqa: E402
from .config import ON_QUIT_ASK, ON_QUIT_STOP, Config  # noqa: E402
from .config import save as save_config  # noqa: E402
from .connect import ConnectDialog  # noqa: E402
from .const import APP_NAME, DEFAULT_ADB_PORT, is_wayland  # noqa: E402
from .errors import AndroLinxError, MissingToolError, map_error  # noqa: E402
from .frame import BODY_ASPECT, DeviceFrame  # noqa: E402
from .mirror import MirrorForm  # noqa: E402
from .models import Device, Session  # noqa: E402
from .preview import PreviewFeed  # noqa: E402
from .sessions import SessionManager, recording_path  # noqa: E402
from .tracker import DeviceMonitor  # noqa: E402
from .worker import Worker  # noqa: E402

#: How wide the phone frame is drawn. Large enough to be the centrepiece,
#: small enough that the mirroring options stay visible underneath it.
FRAME_WIDTH = 240

#: Guidance shown inside the phone frame, keyed by adb's device state.
STATE_HINTS = {
    "unauthorized": (
        "dialog-password-symbolic",
        "Waiting for permission",
        "Unlock the phone and tap Allow on the USB debugging prompt.",
    ),
    "offline": (
        "network-offline-symbolic",
        "Not responding",
        "Unplug and replug the cable, or turn USB debugging off and on.",
    ),
    "no permissions": (
        "channel-insecure-symbolic",
        "Blocked by the system",
        "Install android-sdk-platform-tools-common, then replug the cable.",
    ),
    "authorizing": ("content-loading-symbolic", "Connecting…", ""),
    "connecting": ("content-loading-symbolic", "Connecting…", ""),
}


class AndroLinxWindow(Adw.ApplicationWindow):
    __gtype_name__ = "AndroLinxWindow"

    def __init__(self, application, config: Config) -> None:
        super().__init__(application=application, title=APP_NAME)
        self.set_default_size(1000, 760)
        self.set_size_request(420, 520)

        self._config = config
        self._profiles, self._profiles_read_only = profile_store.load()
        self._devices: list[Device] = []
        self._selected: Device | None = None
        self._caps: scrcpy.Capabilities | None = None
        self._device_rows: dict[str, Gtk.Widget] = {}
        self._session_rows: list[Gtk.Widget] = []

        self._worker = Worker()
        self._worker.start()
        self._sessions = SessionManager(self._refresh_sessions, self._session_failed)
        self._monitor = DeviceMonitor(self._devices_changed, self._monitor_status)
        self._preview = PreviewFeed(self._preview_frame, self._preview_unavailable)

        self._build()
        self._check_tools()

        self.connect("close-request", self._on_close)
        self.connect("notify::is-active", self._on_active_changed)

    # ------------------------------------------------------------------ #
    # Construction
    # ------------------------------------------------------------------ #

    def _build(self) -> None:
        self._toasts = Adw.ToastOverlay()
        self._split = Adw.NavigationSplitView(min_sidebar_width=260, max_sidebar_width=340)
        self._split.set_sidebar(self._build_sidebar())
        self._split.set_content(self._build_content())
        self._toasts.set_child(self._split)
        self.set_content(self._toasts)

    def _build_sidebar(self) -> Adw.NavigationPage:
        header = Adw.HeaderBar()

        connect = Gtk.Button(icon_name="network-wireless-symbolic")
        connect.set_tooltip_text("Connect a device over Wi-Fi")
        connect.connect("clicked", lambda *_: self._open_connect())
        header.pack_start(connect)

        menu = Gio.Menu()
        menu.append("Restart adb", "win.restart-adb")
        menu.append("Preferences", "app.preferences")
        menu.append(f"About {APP_NAME}", "app.about")
        button = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu)
        button.set_tooltip_text("Main menu")
        header.pack_end(button)

        self._device_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self._device_list.add_css_class("navigation-sidebar")
        self._device_list.connect("row-selected", self._on_row_selected)

        self._device_placeholder = Adw.StatusPage(
            icon_name="phone-symbolic",
            title="Looking for devices",
            description="Connect a phone with USB debugging turned on.",
        )
        self._device_placeholder.add_css_class("compact")

        self._sessions_group = Adw.PreferencesGroup(
            title="Mirroring now", margin_top=6, visible=False
        )
        self._sessions_group.set_margin_start(6)
        self._sessions_group.set_margin_end(6)
        self._sessions_group.set_margin_bottom(6)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(self._device_list)
        box.append(self._device_placeholder)
        box.append(self._sessions_group)

        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.set_content(Gtk.ScrolledWindow(child=box, vexpand=True))
        return Adw.NavigationPage(child=view, title="Devices")

    def _build_content(self) -> Adw.NavigationPage:
        self._header = Adw.HeaderBar()
        self._banner = Adw.Banner(revealed=False)
        self._banner.connect("button-clicked", self._on_banner_action)

        self._stack = Gtk.Stack(vexpand=True)
        self._stack.add_named(self._build_device_page(), "device")
        self._stack.add_named(self._build_empty_page(), "empty")
        self._stack.add_named(self._build_missing_page(), "missing")
        self._stack.set_visible_child_name("empty")

        view = Adw.ToolbarView()
        view.add_top_bar(self._header)
        view.add_top_bar(self._banner)
        view.set_content(self._stack)
        view.add_bottom_bar(self._build_actions())
        self._content_page = Adw.NavigationPage(child=view, title=APP_NAME)
        return self._content_page

    def _build_empty_page(self) -> Gtk.Widget:
        return Adw.StatusPage(
            icon_name="phone-symbolic",
            title="No device selected",
            description=(
                "Connect a phone over USB with USB debugging turned on, or use "
                "the Wi-Fi button to connect one over the network."
            ),
        )

    def _build_missing_page(self) -> Gtk.Widget:
        self._missing_page = Adw.StatusPage(
            icon_name="dialog-warning-symbolic", title="Missing programs"
        )
        button = Gtk.Button(label="Copy install command", halign=Gtk.Align.CENTER)
        button.add_css_class("pill")
        button.add_css_class("suggested-action")
        button.connect("clicked", self._copy_install_command)
        self._missing_page.set_child(button)
        return self._missing_page

    def _build_device_page(self) -> Gtk.Widget:
        # What shows inside the phone's screen when there is no picture.
        self._screen_icon = Gtk.Image(pixel_size=48)
        self._screen_spinner = Adw.Spinner(width_request=32, height_request=32)
        self._screen_title = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER)
        self._screen_title.add_css_class("heading")
        self._screen_body = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER)
        self._screen_body.add_css_class("caption")

        self._screen_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=8,
            valign=Gtk.Align.CENTER,
            halign=Gtk.Align.CENTER,
            margin_start=18,
            margin_end=18,
        )
        for widget in (
            self._screen_icon,
            self._screen_spinner,
            self._screen_title,
            self._screen_body,
        ):
            widget.add_css_class("dim-label")
            self._screen_box.append(widget)

        self._frame = DeviceFrame(self._screen_box)
        self._frame.set_halign(Gtk.Align.CENTER)
        # Both dimensions, not just width: the frame scales itself to whichever
        # of the two constrains it, so asking only for a width lets a short
        # allocation quietly shrink the phone to nothing.
        self._frame.set_size_request(FRAME_WIDTH, int(FRAME_WIDTH * BODY_ASPECT))
        self._frame.connect("activated", lambda *_: self._start(record=False))

        self._info = Gtk.Label(justify=Gtk.Justification.CENTER)
        self._info.add_css_class("dim-label")
        self._info.add_css_class("caption")

        self._profile_row = Adw.ComboRow(title="Profile")
        self._profile_row.connect("notify::selected", self._on_profile_selected)
        save = Gtk.Button(icon_name="document-save-symbolic", valign=Gtk.Align.CENTER)
        save.add_css_class("flat")
        save.set_tooltip_text("Save these options as a new profile")
        save.connect("clicked", lambda *_: self._save_profile())
        self._profile_row.add_suffix(save)

        profile_group = Adw.PreferencesGroup()
        profile_group.add(self._profile_row)

        self._form = MirrorForm()
        self._form.connect("changed", lambda *_: self._refresh_command())

        self._command = Gtk.Label(
            wrap=True,
            wrap_mode=Pango.WrapMode.WORD_CHAR,
            selectable=True,
            xalign=0.0,
        )
        self._command.add_css_class("monospace")
        self._command.add_css_class("caption")
        self._command.add_css_class("dim-label")

        command_group = Adw.PreferencesGroup(
            title="Command", description="What AndroLinx will run"
        )
        box = Gtk.Box(margin_top=6, margin_bottom=6, margin_start=12, margin_end=12)
        box.append(self._command)
        command_group.add(box)

        page = Adw.PreferencesPage()
        holder = Adw.PreferencesGroup()
        stage = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        stage.append(self._frame)
        stage.append(self._info)
        holder.add(stage)
        page.add(holder)
        page.add(profile_group)
        page.add(self._wrap_form())
        page.add(command_group)
        return page

    def _wrap_form(self) -> Adw.PreferencesGroup:
        """Put the form's groups on the page.

        MirrorForm is a Box of PreferencesGroups; AdwPreferencesPage wants them
        added individually, so it is carried in a plain group instead.
        """
        holder = Adw.PreferencesGroup()
        holder.add(self._form)
        return holder

    def _build_actions(self) -> Gtk.Widget:
        self._start_button = Gtk.Button(label="Start mirroring", hexpand=True)
        self._start_button.add_css_class("suggested-action")
        self._start_button.add_css_class("pill")
        self._start_button.connect("clicked", lambda *_: self._start(record=False))

        self._record_button = Gtk.Button(
            icon_name="media-record-symbolic", tooltip_text="Mirror and record to a file"
        )
        self._record_button.add_css_class("pill")
        self._record_button.connect("clicked", lambda *_: self._start(record=True))

        box = Gtk.Box(
            spacing=10, margin_top=10, margin_bottom=10, margin_start=12, margin_end=12
        )
        box.append(self._start_button)
        box.append(self._record_button)
        # Hidden until a device is selected; the stack starts on the empty page.
        box.set_visible(False)
        self._actions = box
        return box

    # ------------------------------------------------------------------ #
    # Start-up
    # ------------------------------------------------------------------ #

    def _check_tools(self) -> None:
        """Confirm adb and scrcpy are usable before doing anything with them."""

        def probe():
            adb.require()
            return scrcpy.detect(force_x11=self._config.force_x11)

        self._worker.submit(probe, self._tools_ready, self._tools_missing)

    def _tools_ready(self, caps: scrcpy.Capabilities) -> None:
        self._caps = caps
        if not caps.supported:
            self._banner.set_title(
                f"scrcpy {caps.version_text} is too old; AndroLinx needs 3.0 or newer."
            )
            self._banner.set_revealed(True)
        self._reload_profiles()
        self._preview.set_interval(self._config.preview_interval)
        self._preview.start()
        self._monitor.start()

    def _tools_missing(self, exc: Exception) -> None:
        packages = "adb scrcpy"
        if isinstance(exc, MissingToolError):
            self._missing_page.set_title(f"{exc.binary} is not installed")
            self._missing_page.set_description(
                f"AndroLinx needs both adb and scrcpy.\n\nsudo apt install {packages}"
            )
        else:
            self._missing_page.set_description(str(exc))
        self._stack.set_visible_child_name("missing")
        self._actions.set_visible(False)

    def _copy_install_command(self, _button) -> None:
        self.get_clipboard().set("sudo apt install adb scrcpy")
        self._toast("Install command copied")

    # ------------------------------------------------------------------ #
    # Devices
    # ------------------------------------------------------------------ #

    def _devices_changed(self, devices: list[Device]) -> bool:
        known = {d.serial: d for d in self._devices}
        self._devices = [known.get(d.serial, d).merged_with(d) for d in devices]
        self._rebuild_device_list()

        serial = self._selected.serial if self._selected else self._config.last_serial
        match = next((d for d in self._devices if d.serial == serial), None)
        self._select(match or (self._devices[0] if self._devices else None))
        return GLib.SOURCE_REMOVE

    def _rebuild_device_list(self) -> None:
        self._device_list.remove_all()
        self._device_rows.clear()

        for device in self._devices:
            row = Adw.ActionRow(title=device.display_name, subtitle=device.summary())
            icon = "phone-symbolic"
            if device.over_tcpip:
                icon = "network-wireless-symbolic"
            elif device.is_emulator:
                icon = "computer-symbolic"
            row.add_prefix(Gtk.Image.new_from_icon_name(icon))
            if not device.ready:
                warning = Gtk.Image.new_from_icon_name("dialog-warning-symbolic")
                warning.set_tooltip_text(device.state)
                row.add_suffix(warning)
            self._device_list.append(row)
            self._device_rows[device.serial] = row

        has_devices = bool(self._devices)
        self._device_list.set_visible(has_devices)
        self._device_placeholder.set_visible(not has_devices)
        if not has_devices:
            self._select(None)

    def _on_row_selected(self, _list, row) -> None:
        if row is None:
            return
        index = row.get_index()
        if 0 <= index < len(self._devices):
            self._select(self._devices[index])

    def _select(self, device: Device | None) -> None:
        self._selected = device
        if device is None:
            self._stack.set_visible_child_name("empty")
            self._actions.set_visible(False)
            self._content_page.set_title(APP_NAME)
            self._preview.watch(None)
            self._frame.set_texture(None)
            return

        row = self._device_rows.get(device.serial)
        if row is not None and self._device_list.get_selected_row() is not row:
            self._device_list.select_row(row)

        self._stack.set_visible_child_name("device")
        self._actions.set_visible(True)
        self._content_page.set_title(device.display_name)
        self._config.last_serial = device.serial

        self._update_device_page(device)
        if not device.enriched and device.ready:
            self._worker.submit(
                lambda: adb.enrich(device), self._enriched, lambda _exc: None
            )

    def _enriched(self, device: Device) -> None:
        self._devices = [device if d.serial == device.serial else d for d in self._devices]
        row = self._device_rows.get(device.serial)
        if row is not None:
            row.set_title(device.display_name)
            row.set_subtitle(device.summary())
        if self._selected and self._selected.serial == device.serial:
            self._selected = device
            self._update_device_page(device)

    def _update_device_page(self, device: Device) -> None:
        bits = [device.transport_label]
        if device.android_release:
            bits.append(f"Android {device.android_release}")
        if device.resolution:
            bits.append(device.resolution)
        if device.battery is not None:
            bits.append(f"Battery {device.battery}%")
        bits.append(device.serial)
        self._info.set_label("  ·  ".join(bits))

        can_mirror = device.ready and (self._caps is not None and self._caps.supported)
        self._start_button.set_sensitive(can_mirror)
        self._record_button.set_sensitive(can_mirror)

        self._update_screen(device)
        self._refresh_command()

    def _update_screen(self, device: Device) -> None:
        """Decide what the phone's screen area should be showing."""
        hint = STATE_HINTS.get(device.state)
        if hint is not None:
            icon, title, body = hint
            self._show_screen_message(icon, title, body, busy=not body)
            self._preview.watch(None)
            self._frame.set_texture(None)
            return

        if self._config.preview_interval <= 0:
            self._show_screen_message(
                "view-conceal-symbolic",
                "Preview is off",
                "Turn it on in Preferences.",
            )
            self._preview.watch(None)
            self._frame.set_texture(None)
            return

        if self._sessions.is_mirroring(device.serial):
            # scrcpy is already showing this screen live; a second capture path
            # would only fight it for the adb connection.
            self._show_screen_message(
                "video-display-symbolic",
                "Mirroring in its own window",
                "The preview pauses while scrcpy is running.",
            )
            self._preview.set_paused(True)
            self._frame.set_dim(True)
            return

        self._frame.set_dim(False)
        self._preview.set_paused(not self.is_active())
        self._preview.watch(device.serial)
        if not self._frame.has_texture:
            self._show_screen_message("", "", "", busy=True)
        else:
            self._screen_box.set_visible(False)

    def _show_screen_message(
        self, icon: str, title: str, body: str, *, busy: bool = False
    ) -> None:
        self._screen_box.set_visible(True)
        self._screen_spinner.set_visible(busy)
        self._screen_icon.set_visible(bool(icon) and not busy)
        if icon:
            self._screen_icon.set_from_icon_name(icon)
        self._screen_title.set_visible(bool(title))
        self._screen_title.set_label(title)
        self._screen_body.set_visible(bool(body))
        self._screen_body.set_label(body)

    # ------------------------------------------------------------------ #
    # Live preview
    # ------------------------------------------------------------------ #

    def _preview_frame(self, serial: str, texture) -> bool:
        if self._selected and self._selected.serial == serial:
            self._frame.set_texture(texture)
            self._screen_box.set_visible(False)
        return GLib.SOURCE_REMOVE

    def _preview_unavailable(self, serial: str, message: str) -> bool:
        if self._selected and self._selected.serial == serial:
            self._show_screen_message(
                "camera-disabled-symbolic", "No preview available", message
            )
        return GLib.SOURCE_REMOVE

    def apply_settings(self) -> None:
        """Take up changed preferences at once, without needing a restart."""
        self._preview.set_interval(self._config.preview_interval)

        # Whether scrcpy can place its window depends on the X11 compatibility
        # setting, so the capabilities have to be recomputed rather than left at
        # whatever was detected at start-up.
        if self._caps is not None:
            self._caps = replace(
                self._caps,
                forced_x11=self._config.force_x11,
                window_placement=(not is_wayland()) or self._config.force_x11,
            )

        self._form.set_placement_available(
            self._caps.window_placement if self._caps is not None else True
        )

        self._on_active_changed()
        if self._selected is not None:
            self._update_device_page(self._selected)

    def _on_active_changed(self, *_args) -> None:
        # Capturing costs the phone a screenshot every time. There is no point
        # paying for it while the window is behind something else.
        active = self.is_active()
        busy = bool(self._selected) and self._sessions.is_mirroring(self._selected.serial)
        self._preview.set_paused(not active or busy)

    # ------------------------------------------------------------------ #
    # Profiles and the command preview
    # ------------------------------------------------------------------ #

    def _reload_profiles(self) -> None:
        names = [p.name for p in self._profiles]
        self._profile_row.set_model(Gtk.StringList.new(names))
        wanted = self._config.last_profile
        index = names.index(wanted) if wanted in names else 0
        self._profile_row.set_selected(index)
        self._form.load(self._profiles[index])
        self._refresh_command()

    def _on_profile_selected(self, *_args) -> None:
        index = self._profile_row.get_selected()
        if 0 <= index < len(self._profiles):
            profile = self._profiles[index]
            self._config.last_profile = profile.name
            self._form.load(profile)
            self._refresh_command()

    def _refresh_command(self) -> None:
        if self._selected is None:
            return
        argv = scrcpy.build_command(
            self._form.profile,
            self._selected,
            caps=self._caps,
            window_title=f"{self._selected.display_name} — {APP_NAME}",
        )
        # shlex.quote rather than GLib.shell_quote: it only adds quotes where
        # they are actually needed, so the preview reads like something you
        # could paste into a terminal rather than a wall of apostrophes.
        self._command.set_label(" ".join(shlex.quote(a) for a in argv))

    def _save_profile(self) -> None:
        dialog = Adw.AlertDialog(
            heading="Save profile", body="Give these options a name."
        )
        entry = Gtk.Entry(
            text=profile_store.unique_name(self._profiles, self._form.profile.name),
            activates_default=True,
        )
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("save", "Save")
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("save")
        dialog.connect("response", self._save_profile_response, entry)
        dialog.present(self)

    def _save_profile_response(self, _dialog, response: str, entry: Gtk.Entry) -> None:
        if response != "save":
            return
        name = profile_store.unique_name(self._profiles, entry.get_text())
        profile = self._form.profile.copy_as(name)
        self._profiles.append(profile)
        profile_store.save(self._profiles, read_only=self._profiles_read_only)
        self._config.last_profile = name
        self._reload_profiles()
        self._toast(f"Saved “{name}”")

    # ------------------------------------------------------------------ #
    # Sessions
    # ------------------------------------------------------------------ #

    def _start(self, *, record: bool) -> None:
        device = self._selected
        if device is None or self._caps is None:
            return
        if not device.ready:
            self._toast("That device is not ready to be mirrored")
            return

        target = None
        if record:
            folder = self._config.resolved_recording_dir()
            try:
                folder.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                self._toast(f"Cannot write to {folder}: {exc.strerror}")
                return
            target = recording_path(folder, device)

        try:
            self._sessions.start(
                self._form.profile, device, caps=self._caps, record_to=target
            )
        except AndroLinxError as exc:
            self._show_error(map_error(str(exc)))
            return

        save_config(self._config)
        self._toast(
            f"Recording to {target.name}" if target else f"Mirroring {device.display_name}"
        )
        self._update_screen(device)

    def _refresh_sessions(self) -> None:
        for row in self._session_rows:
            self._sessions_group.remove(row)
        self._session_rows.clear()

        sessions = self._sessions.sessions()
        self._sessions_group.set_visible(bool(sessions))
        for session in sessions:
            row = Adw.ActionRow(
                title=session.device_name or session.serial,
                subtitle=(
                    f"Recording · {session.record_path.name}"
                    if session.recording
                    else f"{session.profile_name} · {self._sessions.status_of(session)}"
                ),
            )
            row.add_prefix(
                Gtk.Image.new_from_icon_name(
                    "media-record-symbolic"
                    if session.recording
                    else "video-display-symbolic"
                )
            )
            stop = Gtk.Button(
                icon_name="media-playback-stop-symbolic", valign=Gtk.Align.CENTER
            )
            stop.add_css_class("flat")
            stop.set_tooltip_text("Stop")
            stop.connect("clicked", self._stop_session, session)
            row.add_suffix(stop)
            self._sessions_group.add(row)
            self._session_rows.append(row)

        if self._selected is not None:
            self._update_screen(self._selected)

    def _stop_session(self, _button, session: Session) -> None:
        self._sessions.stop(session)

    def _session_failed(self, session: Session, diagnosis) -> None:
        self._show_error(diagnosis)

    # ------------------------------------------------------------------ #
    # Wireless, adb, and messages
    # ------------------------------------------------------------------ #

    def _open_connect(self) -> None:
        dialog = ConnectDialog(
            self._worker, self._connect_finished, list(self._config.recent_addresses)
        )
        dialog.present(self)

    def _connect_finished(self, message: str, address: str | None) -> None:
        if address:
            self._config.remember_address(address)
            save_config(self._config)
            self._monitor.wake()
        self._toast(message)

    def enable_wireless(self) -> None:
        """Switch the selected USB device to TCP/IP and connect to it."""
        device = self._selected
        if device is None or device.over_tcpip:
            return

        def work():
            adb.tcpip(device.serial, DEFAULT_ADB_PORT)
            address = adb.device_ip(device.serial)
            if not address:
                raise AndroLinxError(
                    "the phone did not report a Wi-Fi address; connect it by hand"
                )
            return adb.connect(f"{address}:{DEFAULT_ADB_PORT}")

        self._toast("Switching to Wi-Fi…")
        self._worker.submit(
            work,
            lambda out: self._connect_finished(out.strip(), None),
            lambda exc: self._show_error(map_error(str(exc))),
        )

    def restart_adb(self) -> None:
        """Reconnect offline devices.

        Note this never kills the adb server: Android Studio, Waydroid and
        Genymotion share it, and pulling it out from under them mid-debug would
        be a genuinely hostile thing for a mirroring app to do.
        """
        self._worker.submit(
            adb.reconnect_offline,
            lambda _out: (self._monitor.wake(), self._toast("Asked adb to reconnect")),
            lambda exc: self._show_error(map_error(str(exc))),
        )

    def _monitor_status(self, state: str, message: str) -> bool:
        if state in ("error", "polling") and message:
            self._banner.set_title(message)
            self._banner.set_button_label("Retry" if state == "error" else "")
            self._banner.set_revealed(True)
        elif state == "ready":
            self._banner.set_revealed(False)
        return GLib.SOURCE_REMOVE

    def _on_banner_action(self, _banner) -> None:
        self._banner.set_revealed(False)
        self.restart_adb()

    def _toast(self, message: str) -> None:
        self._toasts.add_toast(Adw.Toast(title=message, timeout=4))

    def _show_error(self, diagnosis) -> None:
        if diagnosis.ok:
            self._toast(diagnosis.title)
            return
        dialog = Adw.AlertDialog(heading=diagnosis.title, body=diagnosis.detail or "")
        dialog.add_response("close", "Close")
        if diagnosis.raw:
            dialog.add_response("copy", "Copy details")
        dialog.set_default_response("close")
        dialog.connect("response", self._error_response, diagnosis)
        dialog.present(self)

    def _error_response(self, _dialog, response: str, diagnosis) -> None:
        if response == "copy":
            self.get_clipboard().set(diagnosis.raw)
            self._toast("Details copied")

    # ------------------------------------------------------------------ #
    # Shutting down
    # ------------------------------------------------------------------ #

    def _on_close(self, *_args) -> bool:
        sessions = self._sessions.sessions()
        if not sessions:
            self._shutdown()
            return False

        if self._config.on_quit == ON_QUIT_STOP or self._sessions.any_recording():
            # A recording left running would be cut off by the session ending,
            # producing a file no player can open. Never leave one behind.
            self._sessions.stop_all()
            self._shutdown()
            return False
        if self._config.on_quit != ON_QUIT_ASK:
            self._shutdown()
            return False

        dialog = Adw.AlertDialog(
            heading=f"{len(sessions)} mirror{'s' if len(sessions) > 1 else ''} still running",
            body=(
                "scrcpy windows keep running on their own. Leave them open, or "
                "close them along with AndroLinx?"
            ),
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("keep", "Leave running")
        dialog.add_response("stop", "Stop them")
        dialog.set_response_appearance("stop", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("keep")
        dialog.connect("response", self._close_response)
        dialog.present(self)
        return True

    def _close_response(self, _dialog, response: str) -> None:
        if response == "cancel":
            return
        if response == "stop":
            self._sessions.stop_all()
        self._shutdown()
        self.destroy()

    def _shutdown(self) -> None:
        save_config(self._config)
        self._preview.stop()
        self._monitor.stop()
        self._worker.stop()
