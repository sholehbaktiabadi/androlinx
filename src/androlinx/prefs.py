"""Preferences.

Deliberately short. Anything that belongs to a particular way of mirroring lives
in a profile, not here; what is left is genuinely global -- where recordings go,
how hard the live preview works the phone, and what to do with running mirrors
when the window closes.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, Gtk  # noqa: E402

from .config import (  # noqa: E402
    ON_QUIT_ASK,
    ON_QUIT_CHOICES,
    ON_QUIT_KEEP,
    ON_QUIT_STOP,
    Config,
    save,
)
from .const import PREVIEW_INTERVALS, is_wayland  # noqa: E402

INTERVAL_LABELS = ("Off", "Every second", "Every 2 seconds", "Every 5 seconds")
ON_QUIT_LABELS = {
    ON_QUIT_ASK: "Ask me",
    ON_QUIT_KEEP: "Leave them running",
    ON_QUIT_STOP: "Stop them",
}


class PreferencesDialog(Adw.PreferencesDialog):
    __gtype_name__ = "AndroLinxPreferences"

    def __init__(self, config: Config, on_changed) -> None:
        super().__init__()
        self._config = config
        #: Called after any setting changes, so the window can apply it at once.
        self._on_changed = on_changed
        self._loading = True

        page = Adw.PreferencesPage(title="General", icon_name="preferences-system-symbolic")
        page.add(self._build_preview())
        page.add(self._build_recording())
        page.add(self._build_sessions())
        page.add(self._build_display())
        self.add(page)
        self._loading = False

    # -- groups ------------------------------------------------------------ #

    def _build_preview(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(
            title="Live preview",
            description=(
                "The picture inside the phone frame. Each refresh asks the phone "
                "for a screenshot, so a slower setting is kinder to its battery "
                "and to a Wi-Fi connection."
            ),
        )
        self.row_interval = Adw.ComboRow(
            title="Refresh", model=Gtk.StringList.new(list(INTERVAL_LABELS))
        )
        index = (
            PREVIEW_INTERVALS.index(self._config.preview_interval)
            if self._config.preview_interval in PREVIEW_INTERVALS
            else 2
        )
        self.row_interval.set_selected(index)
        self.row_interval.connect("notify::selected", self._changed)
        group.add(self.row_interval)
        return group

    def _build_recording(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Recording")
        self.row_folder = Adw.ActionRow(
            title="Save recordings to",
            subtitle=str(self._config.resolved_recording_dir()),
            activatable=True,
        )
        self.row_folder.add_suffix(
            Gtk.Image.new_from_icon_name("folder-open-symbolic")
        )
        self.row_folder.connect("activated", self._choose_folder)
        group.add(self.row_folder)
        return group

    def _build_sessions(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(
            title="When AndroLinx closes",
            description=(
                "Mirror windows belong to scrcpy and keep running on their own. "
                "A recording in progress is always stopped properly first, so its "
                "file stays playable."
            ),
        )
        self.row_on_quit = Adw.ComboRow(
            title="Running mirrors",
            model=Gtk.StringList.new([ON_QUIT_LABELS[c] for c in ON_QUIT_CHOICES]),
        )
        self.row_on_quit.set_selected(ON_QUIT_CHOICES.index(self._config.on_quit))
        self.row_on_quit.connect("notify::selected", self._changed)
        group.add(self.row_on_quit)
        return group

    def _build_display(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Display server")
        self.row_x11 = Adw.SwitchRow(
            title="X11 compatibility",
            subtitle=(
                "Run scrcpy through XWayland so it can place its window and stay "
                "on top. Text may look softer on a HiDPI screen, and mouse "
                "capture behaves a little differently."
                if is_wayland()
                else "Only useful on a Wayland session; this one is X11 already."
            ),
            active=self._config.force_x11,
            sensitive=is_wayland(),
        )
        self.row_x11.set_subtitle_lines(4)
        self.row_x11.connect("notify::active", self._changed)
        group.add(self.row_x11)
        return group

    # -- actions ----------------------------------------------------------- #

    def _choose_folder(self, _row) -> None:
        dialog = Gtk.FileDialog(title="Choose where recordings are saved")
        dialog.set_initial_folder(
            Gio.File.new_for_path(str(self._config.resolved_recording_dir()))
        )
        dialog.select_folder(self.get_root(), None, self._folder_chosen)

    def _folder_chosen(self, dialog, result) -> None:
        try:
            folder = dialog.select_folder_finish(result)
        except Exception:  # noqa: BLE001 - the user simply cancelled
            return
        if folder is None:
            return
        self._config.recording_dir = folder.get_path() or ""
        self.row_folder.set_subtitle(str(self._config.resolved_recording_dir()))
        self._apply()

    def _changed(self, *_args) -> None:
        if self._loading:
            return
        self._config.preview_interval = PREVIEW_INTERVALS[
            self.row_interval.get_selected()
        ]
        self._config.on_quit = ON_QUIT_CHOICES[self.row_on_quit.get_selected()]
        self._config.force_x11 = self.row_x11.get_active()
        self._apply()

    def _apply(self) -> None:
        save(self._config)
        self._on_changed()
