"""Command line front-end.

AndroLinx exists to replace typing commands, so this is intentionally thin: it
exists to report the version, to list devices without opening a window, and to
launch the application. Everything else belongs in the interface.
"""

from __future__ import annotations

import argparse
import sys

from .const import APP_NAME, VERSION


def _list_devices() -> int:
    from . import adb
    from .errors import AndroLinxError

    try:
        devices = adb.devices()
    except AndroLinxError as exc:
        print(f"{APP_NAME}: {exc}", file=sys.stderr)
        return 1

    if not devices:
        print("No devices connected.")
        return 0
    width = max(len(d.serial) for d in devices)
    for device in devices:
        print(f"{device.serial:<{width}}  {device.state:<14}  {device.display_name}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="androlinx",
        description=f"{APP_NAME} — a desktop front-end for adb and scrcpy.",
    )
    parser.add_argument(
        "-v", "--version", action="version", version=f"{APP_NAME} {VERSION}"
    )
    parser.add_argument(
        "-l",
        "--list",
        action="store_true",
        help="list connected devices and exit, without opening the window",
    )
    args, rest = parser.parse_known_args(argv if argv is not None else sys.argv[1:])

    if args.list:
        return _list_devices()

    from .application import run

    return run(rest)
