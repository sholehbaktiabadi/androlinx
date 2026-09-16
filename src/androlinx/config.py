"""Application settings, and the JSON helpers the profile store shares.

Two rules shape the loader, both learned the hard way by every program that
keeps a config file:

* A file written by a *newer* AndroLinx is loaded as best we can and then marked
  read-only, so an older version cannot quietly delete fields it does not
  understand the next time it saves.
* A file we cannot parse at all is left exactly where it is. Overwriting it with
  defaults would throw away settings the user may be able to repair by hand.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .const import (
    DEFAULT_PREVIEW_INTERVAL,
    PREVIEW_INTERVALS,
    app_config_dir,
    config_path,
    default_recording_dir,
)

#: Bumped only when the on-disk shape changes in a way older versions cannot
#: read. Both config.json and profiles.json carry it.
SCHEMA_VERSION = 1

#: What to do about running mirror sessions when the window is closed.
ON_QUIT_ASK = "ask"
ON_QUIT_KEEP = "keep"
ON_QUIT_STOP = "stop"
ON_QUIT_CHOICES = (ON_QUIT_ASK, ON_QUIT_KEEP, ON_QUIT_STOP)


def read_json(path: Path) -> tuple[dict | None, bool]:
    """Read a JSON object. Returns (data, damaged).

    ``damaged`` is True when the file exists but could not be used, which the
    caller reports rather than silently replacing the file.
    """
    if not path.is_file():
        return None, False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, True
    if not isinstance(data, dict):
        return None, True
    return data, False


def write_json(path: Path, data: dict) -> None:
    """Write JSON atomically, so an interrupted write cannot truncate the file.

    The fsync matters more here than in most applications: AndroLinx is often
    running when a machine is shut down abruptly, and rename-without-fsync can
    leave an empty file behind on some filesystems.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    tmp.replace(path)


def as_choice(value, allowed: tuple, default):
    return value if value in allowed else default


def as_int(value, default: int | None, low: int, high: int) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


@dataclass
class Config:
    """Everything in Preferences, plus a little remembered state."""

    #: Profile to select when the window opens.
    last_profile: str = "Balanced"
    #: Device to select when the window opens, by serial.
    last_serial: str | None = None
    #: Where recordings are written.
    recording_dir: str = ""
    #: One of ON_QUIT_CHOICES.
    on_quit: str = ON_QUIT_ASK
    #: Seconds between live preview frames; 0 turns the preview off.
    preview_interval: float = DEFAULT_PREVIEW_INTERVAL
    #: Run scrcpy through XWayland so window placement works under Wayland.
    #: Off by default because it changes input handling and HiDPI sharpness.
    force_x11: bool = False
    #: Addresses used before, newest first, offered in the connect dialog.
    recent_addresses: list[str] = field(default_factory=list)
    #: Keys we did not recognise, preserved across a save.
    extra: dict = field(default_factory=dict, repr=False)
    #: True when the file came from a newer AndroLinx and must not be written.
    read_only: bool = field(default=False, repr=False)

    def resolved_recording_dir(self) -> Path:
        return Path(self.recording_dir) if self.recording_dir else default_recording_dir()

    def remember_address(self, address: str, limit: int = 8) -> None:
        addresses = [a for a in self.recent_addresses if a != address]
        self.recent_addresses = [address, *addresses][:limit]


#: Field names Config owns. Anything else in the file is somebody else's and is
#: carried through untouched.
_KNOWN = {
    "version",
    "last_profile",
    "last_serial",
    "recording_dir",
    "on_quit",
    "preview_interval",
    "force_x11",
    "recent_addresses",
}


def load() -> Config:
    data, damaged = read_json(config_path())
    if data is None:
        config = Config()
        config.read_only = damaged
        return config

    version = data.get("version", SCHEMA_VERSION)
    interval = data.get("preview_interval", DEFAULT_PREVIEW_INTERVAL)
    try:
        interval = float(interval)
    except (TypeError, ValueError):
        interval = DEFAULT_PREVIEW_INTERVAL

    recent = data.get("recent_addresses", [])
    return Config(
        last_profile=str(data.get("last_profile") or "Balanced"),
        last_serial=data.get("last_serial") or None,
        recording_dir=str(data.get("recording_dir") or ""),
        on_quit=as_choice(data.get("on_quit"), ON_QUIT_CHOICES, ON_QUIT_ASK),
        preview_interval=(
            interval if interval in PREVIEW_INTERVALS else DEFAULT_PREVIEW_INTERVAL
        ),
        force_x11=bool(data.get("force_x11", False)),
        recent_addresses=[str(a) for a in recent if isinstance(a, str)][:8],
        extra={k: v for k, v in data.items() if k not in _KNOWN},
        read_only=isinstance(version, int) and version > SCHEMA_VERSION,
    )


def save(config: Config) -> None:
    if config.read_only:
        return
    data = {k: v for k, v in asdict(config).items() if k not in ("extra", "read_only")}
    data["version"] = SCHEMA_VERSION
    data.update(config.extra)
    write_json(config_path(), data)


def config_dir() -> Path:
    return app_config_dir()
