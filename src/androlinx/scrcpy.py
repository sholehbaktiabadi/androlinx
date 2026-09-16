"""Working out what scrcpy can do, and building the command line to run it.

``build_command`` is deliberately pure -- it touches no environment, no clock and
no filesystem. Everything that varies is passed in, including the recording
path. That is what makes it worth testing properly, and it is the same function
that produces the command preview shown in the window, so what the user sees is
exactly what gets run.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .const import MIN_SCRCPY_VERSION, is_wayland
from .errors import MissingToolError, ScrcpyError
from .models import Device, Profile

#: The Ubuntu package that provides /usr/bin/scrcpy.
SCRCPY_PACKAGE = "scrcpy"

_VERSION = re.compile(r"^scrcpy\s+(\d+)\.(\d+)(?:\.(\d+))?")


@dataclass(frozen=True)
class Capabilities:
    """What the installed scrcpy and the current session between them allow."""

    #: Parsed version, or None when it could not be read.
    version: tuple[int, int, int] | None
    #: The whole first line of ``scrcpy --version``, for the about dialog.
    raw_version: str = ""
    #: False under Wayland, where the compositor ignores requests to position or
    #: raise another application's window. ``--always-on-top`` and
    #: ``--window-x``/``--window-y`` are therefore dropped rather than emitted
    #: to no effect. Window *size* is a different matter and does work.
    window_placement: bool = True
    #: True when scrcpy will be asked to use XWayland, which restores placement.
    forced_x11: bool = False

    @property
    def supported(self) -> bool:
        return self.version is not None and self.version >= (
            MIN_SCRCPY_VERSION[0],
            MIN_SCRCPY_VERSION[1],
            0,
        )

    @property
    def version_text(self) -> str:
        return ".".join(str(n) for n in self.version) if self.version else "unknown"


def parse_version(text: str) -> tuple[int, int, int] | None:
    """Pull the version out of ``scrcpy --version``.

    The first line looks like ``scrcpy 3.3.4 <https://github.com/...>``. Git
    builds add a suffix (``3.3.4-1-gabcdef``) which the regex simply ignores.
    """
    for line in (text or "").splitlines():
        match = _VERSION.match(line.strip())
        if match:
            major, minor, patch = match.groups()
            return (int(major), int(minor), int(patch or 0))
    return None


def available() -> bool:
    return shutil.which("scrcpy") is not None


def detect(*, force_x11: bool = False) -> Capabilities:
    """Ask scrcpy its version. Runs once at startup, on the worker thread."""
    if not available():
        raise MissingToolError("scrcpy", SCRCPY_PACKAGE)
    try:
        proc = subprocess.run(
            ["scrcpy", "--version"], capture_output=True, text=True, timeout=10.0
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ScrcpyError("scrcpy did not report its version") from exc

    raw = (proc.stdout or proc.stderr or "").strip()
    first = raw.splitlines()[0].strip() if raw else ""
    return Capabilities(
        version=parse_version(raw),
        raw_version=first,
        window_placement=(not is_wayland()) or force_x11,
        forced_x11=force_x11,
    )


def child_env(*, force_x11: bool) -> dict[str, str]:
    """Environment overrides for the scrcpy child process.

    Under Wayland, SDL talks to the compositor directly and window placement
    simply does not exist. Pointing SDL at XWayland instead brings back
    ``--always-on-top`` and window positioning, at the cost of slightly
    different input handling and blurrier output on HiDPI screens -- so it is an
    option the user turns on, never something done behind their back.

    Nothing is set unless asked, so a user who has exported SDL_VIDEODRIVER
    themselves keeps their own setting.
    """
    if not force_x11:
        return {}
    if not os.environ.get("DISPLAY"):
        # No X server and no XWayland: forcing x11 would just fail to start.
        return {}
    return {"SDL_VIDEODRIVER": "x11"}


def build_command(
    profile: Profile,
    device: Device | None,
    *,
    caps: Capabilities | None = None,
    record_to: Path | None = None,
    window_title: str | None = None,
    executable: str = "scrcpy",
) -> list[str]:
    """Translate a profile into a scrcpy command line.

    Pure. Options at their scrcpy default are left out entirely, which keeps the
    command short enough to read at a glance in the UI.
    """
    placement = caps.window_placement if caps is not None else True
    argv: list[str] = [executable]

    if device is not None:
        argv += ["-s", device.serial]

    # -- video ------------------------------------------------------------- #
    if profile.max_size:
        argv.append(f"--max-size={profile.max_size}")
    if profile.video_bit_rate:
        argv.append(f"--video-bit-rate={profile.video_bit_rate}M")
    if profile.max_fps:
        argv.append(f"--max-fps={profile.max_fps}")
    if profile.video_codec and profile.video_codec != "h264":
        argv.append(f"--video-codec={profile.video_codec}")

    # -- audio ------------------------------------------------------------- #
    if not profile.audio:
        argv.append("--no-audio")
    elif profile.audio_codec and profile.audio_codec != "opus":
        argv.append(f"--audio-codec={profile.audio_codec}")

    # -- control ----------------------------------------------------------- #
    if not profile.control:
        argv.append("--no-control")

    # -- window ------------------------------------------------------------ #
    if profile.fullscreen:
        argv.append("--fullscreen")
    if profile.borderless:
        argv.append("--window-borderless")
    if profile.always_on_top and placement:
        # Silently dropped under Wayland; the UI greys the switch out and says
        # why, so this is a safety net rather than the explanation.
        argv.append("--always-on-top")
    if window_title:
        argv.append(f"--window-title={window_title}")

    # -- device ------------------------------------------------------------ #
    if profile.turn_screen_off:
        argv.append("--turn-screen-off")
    if profile.stay_awake:
        argv.append("--stay-awake")
    if profile.show_touches:
        argv.append("--show-touches")
    if profile.power_off_on_close:
        argv.append("--power-off-on-close")
    if profile.orientation:
        argv.append(f"--orientation={profile.orientation}")

    # -- recording --------------------------------------------------------- #
    if record_to is not None:
        argv.append(f"--record={record_to}")

    return argv
