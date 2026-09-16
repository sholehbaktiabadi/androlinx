"""A single background thread for anything that shells out.

Every adb call spawns a process and costs tens of milliseconds at best; probing
a sleeping phone can take several seconds. On the main thread that is a frozen
window, so all of it goes here.

Tasks are run one at a time, in the order submitted. That is deliberate: adb
serialises most of this internally anyway, and a queue of one keeps the failure
modes easy to reason about. Results come back through ``GLib.idle_add``, which
is the only thing that ever crosses the thread boundary.
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from typing import Any

from gi.repository import GLib

#: Put on the queue to make the thread exit.
_QUIT = object()


class Worker:
    """Runs callables off the main loop and reports back on it."""

    def __init__(self) -> None:
        self._queue: queue.SimpleQueue = queue.SimpleQueue()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._busy = threading.Event()

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._queue.put(_QUIT)

    @property
    def busy(self) -> bool:
        return self._busy.is_set()

    def submit(
        self,
        work: Callable[[], Any],
        on_done: Callable[[Any], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        """Run ``work`` on the worker thread.

        ``on_done`` and ``on_error`` are called on the main loop, so they may
        touch widgets directly. Exactly one of them runs for each task.
        """
        self._queue.put((work, on_done, on_error))

    def _loop(self) -> None:
        while True:
            item = self._queue.get()
            if item is _QUIT:
                return
            work, on_done, on_error = item
            self._busy.set()
            try:
                result = work()
            except Exception as exc:  # noqa: BLE001 - reported to the user as a toast
                if on_error is not None:
                    GLib.idle_add(on_error, exc)
            else:
                if on_done is not None:
                    GLib.idle_add(on_done, result)
            finally:
                self._busy.clear()
