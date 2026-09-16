"""Starting scrcpy, watching it, and stopping it politely.

``Gio.Subprocess`` is used rather than ``subprocess`` or ``GLib.spawn_async``
because it is the only one of the three that gives, in a single object: exit
notification delivered on the GLib main loop, an asynchronous stderr stream,
the pid, signal delivery, and automatic reaping. No extra thread is involved and
there are no zombies to collect.

The stopping sequence deserves its own note. scrcpy finishes writing an MP4 when
it shuts down cleanly; killed outright, the file never gets its index written
and no player will open it. So a stop is always SIGINT first, and SIGKILL is
only ever a last resort several seconds later.
"""

from __future__ import annotations

import signal
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from gi.repository import Gio, GLib

from . import scrcpy
from .errors import SOURCE_SCRCPY, Diagnosis, map_error
from .models import Device, Profile, Session

#: Seconds to wait after SIGINT before escalating to SIGTERM, and again before
#: SIGKILL. Generous, because the wait exists to let a recording finish.
GRACE_INTERRUPT = 5
GRACE_TERMINATE = 3

#: scrcpy is chatty; keep only enough stderr to diagnose a failure.
LOG_LINES = 200

STATUS_STARTING = "starting"
STATUS_RUNNING = "running"
STATUS_STOPPING = "stopping"
STATUS_ENDED = "ended"
STATUS_FAILED = "failed"

#: How long scrcpy must survive before it is considered to have started
#: properly. Keyed on time rather than on log text, which changes between
#: scrcpy releases.
SETTLE_SECONDS = 3


@dataclass
class _Running:
    """Bookkeeping for one live scrcpy process."""

    session: Session
    process: Gio.Subprocess
    status: str = STATUS_STARTING
    #: True once the user has asked it to stop, so a non-zero exit caused by our
    #: own signal is not reported as a crash.
    stopping: bool = False
    log: deque = field(default_factory=lambda: deque(maxlen=LOG_LINES))
    timers: list = field(default_factory=list)


class SessionManager:
    """Owns every scrcpy process AndroLinx has started."""

    def __init__(self, on_changed, on_failed) -> None:
        #: Called with no arguments whenever the session list or a status changes.
        self._on_changed = on_changed
        #: Called with (Session, Diagnosis) when a session ends badly.
        self._on_failed = on_failed
        self._running: dict[int, _Running] = {}
        self._next_id = 1

    # -- queries ----------------------------------------------------------- #

    def sessions(self) -> list[Session]:
        return [entry.session for entry in self._running.values()]

    def status_of(self, session: Session) -> str:
        entry = self._running.get(session.pid)
        return entry.status if entry else STATUS_ENDED

    def for_serial(self, serial: str) -> list[Session]:
        return [s for s in self.sessions() if s.serial == serial]

    def is_mirroring(self, serial: str) -> bool:
        return any(not s.recording for s in self.for_serial(serial))

    def any_recording(self) -> bool:
        return any(s.recording for s in self.sessions())

    # -- launching --------------------------------------------------------- #

    def start(
        self,
        profile: Profile,
        device: Device,
        *,
        caps: scrcpy.Capabilities,
        record_to: Path | None = None,
    ) -> Session:
        """Launch scrcpy. Returns immediately; the process runs on its own."""
        argv = scrcpy.build_command(
            profile,
            device,
            caps=caps,
            record_to=record_to,
            window_title=f"{device.display_name} — AndroLinx",
        )

        launcher = Gio.SubprocessLauncher.new(
            Gio.SubprocessFlags.STDERR_PIPE | Gio.SubprocessFlags.STDOUT_SILENCE
        )
        for key, value in scrcpy.child_env(force_x11=caps.forced_x11).items():
            launcher.setenv(key, value, True)

        try:
            process = launcher.spawnv(argv)
        except GLib.Error as exc:
            raise scrcpy.ScrcpyError(exc.message) from exc

        identifier = process.get_identifier()
        pid = int(identifier) if identifier else self._next_id
        self._next_id += 1

        session = Session(
            serial=device.serial,
            profile_name=profile.name,
            pid=pid,
            started_at=time.monotonic(),
            record_path=record_to,
            device_name=device.display_name,
        )
        entry = _Running(session=session, process=process)
        self._running[pid] = entry

        process.wait_check_async(None, self._on_exit, pid)
        self._read_stderr(entry)
        entry.timers.append(
            GLib.timeout_add_seconds(SETTLE_SECONDS, self._settle, pid)
        )
        self._on_changed()
        return session

    def _settle(self, pid: int) -> bool:
        entry = self._running.get(pid)
        if entry and entry.status == STATUS_STARTING:
            entry.status = STATUS_RUNNING
            self._on_changed()
        return GLib.SOURCE_REMOVE

    # -- stderr ------------------------------------------------------------ #

    def _read_stderr(self, entry: _Running) -> None:
        pipe = entry.process.get_stderr_pipe()
        if pipe is None:
            return
        stream = Gio.DataInputStream.new(pipe)
        self._read_line(stream, entry)

    def _read_line(self, stream: Gio.DataInputStream, entry: _Running) -> None:
        stream.read_line_async(GLib.PRIORITY_DEFAULT, None, self._on_line, entry)

    def _on_line(self, stream, result, entry: _Running) -> None:
        try:
            line, _ = stream.read_line_finish_utf8(result)
        except GLib.Error:
            return
        if line is None:
            return
        # scrcpy logs INFO and WARN here too, so a line on stderr is not a
        # failure by itself. Only the exit status decides that.
        entry.log.append(line)
        self._read_line(stream, entry)

    # -- stopping ---------------------------------------------------------- #

    def stop(self, session: Session) -> None:
        entry = self._running.get(session.pid)
        if entry is None or entry.stopping:
            return
        entry.stopping = True
        entry.status = STATUS_STOPPING
        self._on_changed()

        entry.process.send_signal(signal.SIGINT)
        entry.timers.append(
            GLib.timeout_add_seconds(GRACE_INTERRUPT, self._escalate, session.pid)
        )

    def _escalate(self, pid: int) -> bool:
        """SIGINT went unanswered; try SIGTERM, then give up and kill."""
        entry = self._running.get(pid)
        if entry is None:
            return GLib.SOURCE_REMOVE
        entry.process.send_signal(signal.SIGTERM)
        entry.timers.append(GLib.timeout_add_seconds(GRACE_TERMINATE, self._force, pid))
        return GLib.SOURCE_REMOVE

    def _force(self, pid: int) -> bool:
        entry = self._running.get(pid)
        if entry is not None:
            # Last resort. If this session was recording, the file is probably
            # unplayable -- which is exactly why we waited eight seconds first.
            entry.process.force_exit()
        return GLib.SOURCE_REMOVE

    def stop_all(self) -> None:
        for session in self.sessions():
            self.stop(session)

    # -- exit -------------------------------------------------------------- #

    def _on_exit(self, process, result, pid: int) -> None:
        entry = self._running.pop(pid, None)
        if entry is None:
            return
        for timer in entry.timers:
            GLib.source_remove(timer)
        entry.timers.clear()

        failed = False
        try:
            process.wait_check_finish(result)
        except GLib.Error:
            # A non-zero status after we signalled it is the expected outcome,
            # not a crash.
            failed = not entry.stopping

        diagnosis: Diagnosis | None = None
        if failed:
            diagnosis = map_error("\n".join(entry.log), source=SOURCE_SCRCPY)
        elif entry.session.record_path is not None:
            diagnosis = self._check_recording(entry)

        self._on_changed()
        if diagnosis is not None:
            self._on_failed(entry.session, diagnosis)

    def _check_recording(self, entry: _Running) -> Diagnosis | None:
        """A recording that produced nothing is a failure, however it exited."""
        path = entry.session.record_path
        assert path is not None
        try:
            size = path.stat().st_size
        except OSError:
            return map_error("Recording failed", source=SOURCE_SCRCPY)
        if size == 0:
            try:
                path.unlink()
            except OSError:
                pass
            return map_error("Recording failed", source=SOURCE_SCRCPY)
        return None


def recording_path(directory: Path, device: Device) -> Path:
    """Build a recording filename that sorts by time and names the device."""
    slug = "".join(
        ch if ch.isalnum() or ch in "-_" else "-" for ch in device.display_name
    ).strip("-")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return directory / f"AndroLinx-{slug or 'device'}-{stamp}.mp4"
