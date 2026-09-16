"""Application identity, file locations, and the handful of tuneable defaults.

Everything here is pure data. Keeping it in one module means the packaging, the
command line and the user interface all agree on the same names without importing
each other.
"""

from __future__ import annotations

import os
from pathlib import Path

APP_ID = "io.github.sholehbaktiabadi.AndroLinx"
APP_NAME = "AndroLinx"
VERSION = "0.1.0"

#: Where the project lives. Used by the about dialog and the error messages that
#: ask people to report something.
HOMEPAGE = "https://github.com/sholehbaktiabadi/androlinx"
ISSUES_URL = f"{HOMEPAGE}/issues"

#: scrcpy reworked several option names during 2.x. AndroLinx emits the 3.x
#: spelling only, so anything older is refused with a clear message rather than
#: failing later with "unknown option".
MIN_SCRCPY_VERSION = (3, 0)

#: Default TCP port used by ``adb tcpip`` and plain ``adb connect``. Wireless
#: debugging pairing uses its own random port, which the user reads off the phone.
DEFAULT_ADB_PORT = 5555

#: How long to wait on an adb call that talks to a device. Phones that are asleep
#: or busy can take a couple of seconds to answer, so this is deliberately gentle.
ADB_TIMEOUT = 15.0

#: Screen captures are bigger and slower than ordinary adb calls, especially over
#: Wi-Fi, so they get their own budget.
SCREENCAP_TIMEOUT = 20.0

#: Fallback poll interval, used only when the ``adb track-devices`` stream is
#: unavailable or has died. The stream is the normal path and needs no polling.
POLL_SECONDS = 3.0

#: Offered in Preferences as the live preview refresh rate. 0 turns the preview
#: off entirely, which is the right choice on a metered or slow Wi-Fi link.
PREVIEW_INTERVALS = (0.0, 1.0, 2.0, 5.0)

#: Every capture costs the phone a PNG encode, so the default is unhurried.
DEFAULT_PREVIEW_INTERVAL = 2.0


def _xdg(env: str, default: str) -> Path:
    value = os.environ.get(env)
    return Path(value) if value else Path.home() / default


def config_home() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config")


def app_config_dir() -> Path:
    return config_home() / "androlinx"


def config_path() -> Path:
    return app_config_dir() / "config.json"


def profiles_path() -> Path:
    return app_config_dir() / "profiles.json"


def default_recording_dir() -> Path:
    """Where recordings land unless Preferences says otherwise.

    ``XDG_VIDEOS_DIR`` is set by xdg-user-dirs on a normal desktop session; the
    fallback matters for minimal installs and for people who run from a tty.
    """
    value = os.environ.get("XDG_VIDEOS_DIR")
    if value:
        return Path(value)
    videos = Path.home() / "Videos"
    return videos if videos.is_dir() else Path.home()


def is_wayland() -> bool:
    """True when the session is Wayland, where scrcpy cannot place its window.

    ``--always-on-top`` and ``--window-x``/``--window-y`` are silently ignored by
    the compositor, so the user interface disables them rather than pretending.
    """
    return os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
