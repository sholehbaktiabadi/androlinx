"""Connecting to a phone over Wi-Fi, without three terminal commands.

Android's wireless debugging has two different ports and people mix them up
constantly: the *pairing* port is shown only while the "Pair device with pairing
code" dialog is open on the phone, and it is not the port you connect to
afterwards. This dialog keeps the two steps visibly separate, and offers
whatever mDNS has discovered so that in the common case neither number has to be
typed at all.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk  # noqa: E402

from . import adb  # noqa: E402
from .const import DEFAULT_ADB_PORT  # noqa: E402

#: The mDNS service Android advertises while its pairing dialog is open.
PAIRING_SERVICE = "_adb-tls-pairing._tcp"
#: The one it advertises when it is already paired and ready to be connected to.
CONNECT_SERVICE = "_adb-tls-connect._tcp"


class ConnectDialog(Adw.Dialog):
    """Discover, connect to, or pair with a device over the network."""

    __gtype_name__ = "AndroLinxConnectDialog"

    def __init__(self, worker, on_done, recent: list[str]) -> None:
        super().__init__(title="Connect over Wi-Fi", content_width=460)
        self._worker = worker
        #: Called with a human-readable message once something succeeded, so the
        #: window can toast it and refresh the device list.
        self._on_done = on_done
        self._rows: list[Gtk.Widget] = []

        page = Adw.PreferencesPage()
        self._discovered = Adw.PreferencesGroup(
            title="Found on this network",
            description="Phones advertising wireless debugging nearby",
        )
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", valign=Gtk.Align.CENTER)
        refresh.add_css_class("flat")
        refresh.set_tooltip_text("Search again")
        refresh.connect("clicked", lambda *_: self.discover())
        self._discovered.set_header_suffix(refresh)
        page.add(self._discovered)

        page.add(self._build_connect(recent))
        page.add(self._build_pair())

        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        view.set_content(page)
        self.set_child(view)

        self.discover()

    # -- construction ------------------------------------------------------ #

    def _build_connect(self, recent: list[str]) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(
            title="Connect to an address",
            description="For a phone that has already been paired with this computer",
        )
        self.entry_address = Adw.EntryRow(title="Address")
        self.entry_address.set_text(recent[0] if recent else "")
        self.entry_address.connect("entry-activated", lambda *_: self._connect())
        group.add(self.entry_address)

        button = Adw.ButtonRow(title="Connect")
        button.add_css_class("suggested-action")
        button.connect("activated", lambda *_: self._connect())
        group.add(button)

        for address in recent[1:5]:
            row = Adw.ActionRow(title=address, activatable=True)
            row.add_prefix(Gtk.Image.new_from_icon_name("document-open-recent-symbolic"))
            row.connect("activated", self._use_recent, address)
            group.add(row)
        return group

    def _build_pair(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(
            title="Pair a new device",
            description=(
                "On the phone: Developer options → Wireless debugging → "
                "Pair device with pairing code. Use the address and port shown "
                "there — they differ from the ones above."
            ),
        )
        self.entry_pair_address = Adw.EntryRow(title="Pairing address")
        self.entry_pair_code = Adw.EntryRow(title="Pairing code")
        self.entry_pair_code.connect("entry-activated", lambda *_: self._pair())
        group.add(self.entry_pair_address)
        group.add(self.entry_pair_code)

        button = Adw.ButtonRow(title="Pair")
        button.connect("activated", lambda *_: self._pair())
        group.add(button)
        return group

    # -- discovery --------------------------------------------------------- #

    def discover(self) -> None:
        for row in self._rows:
            self._discovered.remove(row)
        self._rows.clear()

        placeholder = Adw.ActionRow(title="Searching…")
        placeholder.add_prefix(Adw.Spinner())
        self._discovered.add(placeholder)
        self._rows.append(placeholder)

        self._worker.submit(adb.mdns_services, self._show_discovered, self._show_no_mdns)

    def _show_discovered(self, services) -> None:
        for row in self._rows:
            self._discovered.remove(row)
        self._rows.clear()

        if not services:
            self._show_empty(
                "Nothing found",
                "Open Wireless debugging on the phone and search again. Some "
                "networks block the discovery this relies on — the address "
                "can always be typed in below.",
            )
            return

        for instance, service, address in services:
            pairing = service == PAIRING_SERVICE
            row = Adw.ActionRow(
                title=address,
                subtitle="Ready to pair" if pairing else "Ready to connect",
                activatable=True,
            )
            row.add_prefix(
                Gtk.Image.new_from_icon_name(
                    "channel-secure-symbolic" if pairing else "network-wireless-symbolic"
                )
            )
            row.connect("activated", self._use_discovered, address, pairing)
            self._discovered.add(row)
            self._rows.append(row)

    def _show_no_mdns(self, _exc: Exception) -> None:
        self._show_empty(
            "Discovery is unavailable",
            "This build of adb cannot search the network. Type the address below "
            "instead.",
        )

    def _show_empty(self, title: str, subtitle: str) -> None:
        row = Adw.ActionRow(title=title, subtitle=subtitle)
        row.set_subtitle_lines(4)
        self._discovered.add(row)
        self._rows.append(row)

    # -- actions ----------------------------------------------------------- #

    def _use_recent(self, _row, address: str) -> None:
        self.entry_address.set_text(address)
        self._connect()

    def _use_discovered(self, _row, address: str, pairing: bool) -> None:
        if pairing:
            self.entry_pair_address.set_text(address)
            self.entry_pair_code.grab_focus()
        else:
            self.entry_address.set_text(address)
            self._connect()

    def _connect(self) -> None:
        address = self.entry_address.get_text().strip()
        if not address:
            return
        if ":" not in address:
            address = f"{address}:{DEFAULT_ADB_PORT}"
        self._worker.submit(
            lambda: adb.connect(address),
            lambda _out: self._succeeded(f"Connected to {address}", address),
            self._failed,
        )

    def _pair(self) -> None:
        address = self.entry_pair_address.get_text().strip()
        code = self.entry_pair_code.get_text().strip()
        if not address or not code:
            return
        self._worker.submit(
            lambda: adb.pair(address, code),
            lambda _out: self._paired(address),
            self._failed,
        )

    def _paired(self, address: str) -> None:
        # Pairing does not connect. The connect port is a different one, so the
        # host is carried over and the user is left one button from finishing.
        host = address.rsplit(":", 1)[0]
        self.entry_pair_code.set_text("")
        self.entry_address.set_text(f"{host}:{DEFAULT_ADB_PORT}")
        self._on_done(f"Paired with {host}. Now connect to it.", None)

    def _succeeded(self, message: str, address: str) -> None:
        self._on_done(message, address)
        self.close()

    def _failed(self, exc: Exception) -> None:
        self._on_done(str(exc), None)
