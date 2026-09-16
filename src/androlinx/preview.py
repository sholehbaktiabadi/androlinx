"""The live screen preview shown inside the device frame.

Each frame costs the phone a full PNG encode, so this is deliberately unhurried:
a couple of frames a second at most, paused whenever nobody is looking at it.
It runs on its own thread rather than sharing the device-tracking one, because a
sleeping phone can take several seconds to answer a screencap and device updates
must not queue up behind it.

Decoding happens on this thread too. ``Gdk.Texture.new_from_bytes`` produces a
plain memory texture with no GPU context attached, which is safe to build off
the main loop and hand over -- and at roughly 30 ms for a 1080p frame, doing it
here keeps that cost away from the UI entirely.
"""

from __future__ import annotations

import threading

import gi

gi.require_version("Gdk", "4.0")

from gi.repository import Gdk, GLib  # noqa: E402

from .adb import screencap  # noqa: E402
from .errors import AndroLinxError  # noqa: E402

#: Give up on previewing a device after this many failures in a row. Something
#: is wrong that retrying will not fix -- a device with no screencap, or one
#: that keeps going to sleep -- and hammering it is worse than stopping.
MAX_FAILURES = 3


class PreviewFeed:
    """Captures a device's screen on a loop and hands over decoded frames."""

    def __init__(self, on_frame, on_unavailable) -> None:
        #: Called with (serial, Gdk.Texture) for each captured frame.
        self._on_frame = on_frame
        #: Called with (serial, message) when previewing this device gives up.
        self._on_unavailable = on_unavailable
        self._cond = threading.Condition()
        self._stopped = False
        self._serial: str | None = None
        self._interval = 0.0
        self._paused = True
        self._failures = 0
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        with self._cond:
            self._stopped = True
            self._cond.notify_all()

    def watch(self, serial: str | None) -> None:
        """Preview this device, or nothing when serial is None."""
        with self._cond:
            if serial != self._serial:
                self._serial = serial
                self._failures = 0
            self._cond.notify_all()

    def set_interval(self, seconds: float) -> None:
        with self._cond:
            self._interval = seconds
            self._cond.notify_all()

    def set_paused(self, paused: bool) -> None:
        """Stop capturing without forgetting which device we were watching.

        Used when the window loses focus, and while scrcpy is mirroring the same
        device -- it is already showing the screen, and a second capture path
        only competes for the adb connection.
        """
        with self._cond:
            if paused != self._paused:
                self._paused = paused
                self._cond.notify_all()

    @property
    def active(self) -> bool:
        return bool(self._serial) and not self._paused and self._interval > 0

    def _loop(self) -> None:
        while True:
            with self._cond:
                while not self._stopped and not self.active:
                    self._cond.wait()
                if self._stopped:
                    return
                serial = self._serial
                interval = self._interval

            if serial:
                self._capture(serial)

            with self._cond:
                if self._stopped:
                    return
                self._cond.wait(timeout=interval)

    def _capture(self, serial: str) -> None:
        try:
            png = screencap(serial)
        except AndroLinxError as exc:
            self._failed(serial, str(exc))
            return

        try:
            texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(png))
        except GLib.Error as exc:
            self._failed(serial, exc.message)
            return

        with self._cond:
            # The user may have switched devices while this was in flight.
            if serial != self._serial:
                return
            self._failures = 0
        GLib.idle_add(self._on_frame, serial, texture, priority=GLib.PRIORITY_DEFAULT_IDLE)

    def _failed(self, serial: str, message: str) -> None:
        with self._cond:
            if serial != self._serial:
                return
            self._failures += 1
            give_up = self._failures >= MAX_FAILURES
            if give_up:
                self._serial = None
        if give_up:
            GLib.idle_add(self._on_unavailable, serial, message)
