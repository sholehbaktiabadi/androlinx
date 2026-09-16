"""Watching devices come and go, without polling for them.

``adb track-devices -l`` is a long-lived command that writes the whole device
list every time anything about it changes, so plugging a cable updates the
sidebar immediately rather than up to a poll interval later. Passing ``-l``
means each snapshot already carries the model and transport id, so no follow-up
``adb devices -l`` is needed.

The stream is the raw ADB host-service protocol: a four-character hexadecimal
length, then exactly that many bytes of payload, repeated forever. An empty
device list is the frame ``0000`` with no payload at all.

``track-devices`` is not in ``adb --help`` and could change or disappear, so
nothing here is allowed to depend on it: any failure falls back to polling
``adb devices -l``, and the application is fully usable that way.
"""

from __future__ import annotations

import subprocess
import threading

from gi.repository import GLib

from . import adb
from .adb import TrackStream
from .const import POLL_SECONDS
from .errors import AndroLinxError, TrackFormatError
from .models import Device

class DeviceMonitor:
    """Keeps a current list of devices on a background thread.

    Everything adb-related happens off the main loop; results reach the UI
    through ``GLib.idle_add`` only.
    """

    def __init__(self, on_devices, on_status) -> None:
        #: Called with list[Device] whenever the set of devices changes.
        self._on_devices = on_devices
        #: Called with (str, str) -- a state id and a human message -- so the UI
        #: can say "Starting adb...", or explain why it fell back to polling.
        self._on_status = on_status
        self._cond = threading.Condition()
        self._stopped = False
        self._streaming = True
        self._proc: subprocess.Popen | None = None
        self._last_key: list[tuple[str, str]] | None = None
        self._thread = threading.Thread(target=self._loop, daemon=True)

    # -- lifecycle --------------------------------------------------------- #

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        with self._cond:
            self._stopped = True
            self._cond.notify_all()
        self._kill_stream()

    def wake(self) -> None:
        """Ask for an immediate re-read, e.g. after connecting a device."""
        with self._cond:
            self._cond.notify_all()

    # -- internals --------------------------------------------------------- #

    def _kill_stream(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except (subprocess.TimeoutExpired, OSError):
            try:
                proc.kill()
            except OSError:
                pass

    def _emit(self, devices: list[Device]) -> None:
        """Report a snapshot, but only when it actually differs."""
        key = [(d.serial, d.state) for d in devices]
        if key == self._last_key:
            return
        self._last_key = key
        GLib.idle_add(self._on_devices, devices, priority=GLib.PRIORITY_DEFAULT)

    def _status(self, state: str, message: str) -> None:
        GLib.idle_add(self._on_status, state, message, priority=GLib.PRIORITY_DEFAULT)

    def _loop(self) -> None:
        self._status("starting", "Starting adb…")
        try:
            adb.start_server()
        except AndroLinxError as exc:
            self._status("error", str(exc))
        else:
            self._status("ready", "")

        failures = 0
        while not self._stopped:
            if self._streaming:
                lived = self._run_stream()
                if self._stopped:
                    return
                # A stream that ran for a while then died (an adb restart, say)
                # should not count towards giving up on streaming for good.
                failures = 0 if lived > 60.0 else failures + 1
                if failures > len(RETRY_DELAYS):
                    self._streaming = False
                    self._status(
                        "polling",
                        "Watching for devices by polling; live updates are unavailable.",
                    )
                else:
                    self._sleep(RETRY_DELAYS[min(failures - 1, len(RETRY_DELAYS) - 1)])
            else:
                self._poll_once()
                self._sleep(POLL_SECONDS)

    def _sleep(self, seconds: float) -> None:
        with self._cond:
            if not self._stopped:
                self._cond.wait(timeout=seconds)

    def _poll_once(self) -> None:
        try:
            self._emit(adb.devices())
        except AndroLinxError as exc:
            self._status("error", str(exc))

    def _run_stream(self) -> float:
        """Read the stream until it ends. Returns how long it lasted, in seconds."""
        import time

        started = time.monotonic()
        stream = TrackStream()
        try:
            # bufsize=0 makes stdout a raw FileIO, so read() returns as soon as
            # anything arrives instead of waiting for a full buffer.
            self._proc = subprocess.Popen(
                ["adb", "track-devices", "-l"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=0,
            )
        except (OSError, ValueError) as exc:
            self._status("error", f"could not watch for devices: {exc}")
            return 0.0

        assert self._proc.stdout is not None
        try:
            while not self._stopped:
                chunk = self._proc.stdout.read(4096)
                if not chunk:
                    break
                for snapshot in stream.feed(chunk):
                    self._emit(snapshot)
        except TrackFormatError:
            # Give up on streaming entirely rather than guess at the framing.
            self._streaming = False
            self._status(
                "polling",
                "adb reported devices in an unexpected format; falling back to polling.",
            )
        except OSError:
            pass
        finally:
            self._kill_stream()
        return time.monotonic() - started
