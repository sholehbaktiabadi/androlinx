"""GTK entry point: the application object, its actions, and the about dialog."""

from __future__ import annotations

import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, Gtk  # noqa: E402

from . import config as config_store  # noqa: E402
from .const import APP_ID, APP_NAME, HOMEPAGE, ISSUES_URL, VERSION  # noqa: E402
from .prefs import PreferencesDialog  # noqa: E402
from .window import AndroLinxWindow  # noqa: E402


class AndroLinxApplication(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self._config = config_store.load()

        self._add_action("preferences", self._on_preferences, ["<Control>comma"])
        self._add_action("about", self._on_about)
        self._add_action("quit", self._on_quit, ["<Control>q"])

    def _add_action(self, name: str, handler, accels: list[str] | None = None) -> None:
        action = Gio.SimpleAction.new(name, None)
        action.connect("activate", handler)
        self.add_action(action)
        if accels:
            self.set_accels_for_action(f"app.{name}", accels)

    def do_activate(self) -> None:
        window = self.props.active_window
        if window is None:
            window = AndroLinxWindow(self, self._config)
            self._install_window_actions(window)
        window.present()

    def _install_window_actions(self, window: AndroLinxWindow) -> None:
        group = Gio.SimpleActionGroup()
        for name, handler in (
            ("restart-adb", lambda *_: window.restart_adb()),
            ("enable-wireless", lambda *_: window.enable_wireless()),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", handler)
            group.add_action(action)
        window.insert_action_group("win", group)

    def _on_preferences(self, *_args) -> None:
        window = self.props.active_window
        if window is None:
            return
        dialog = PreferencesDialog(self._config, window.apply_settings)
        dialog.present(window)

    def _on_about(self, *_args) -> None:
        about = Adw.AboutDialog(
            application_name=APP_NAME,
            application_icon=APP_ID,
            developer_name="sholehbaktiabadi",
            version=VERSION,
            website=HOMEPAGE,
            issue_url=ISSUES_URL,
            license_type=Gtk.License.GPL_3_0,
            copyright="© 2026 sholehbaktiabadi",
            comments=(
                "A desktop front-end for adb and scrcpy.\n\n"
                "Mirroring itself is done by scrcpy, which opens its own window."
            ),
        )
        about.set_developers(["sholehbaktiabadi"])
        about.present(self.props.active_window)

    def _on_quit(self, *_args) -> None:
        window = self.props.active_window
        if window is not None:
            window.close()
        else:
            self.quit()


def run(argv: list[str] | None = None) -> int:
    return AndroLinxApplication().run(argv if argv is not None else sys.argv[1:])
