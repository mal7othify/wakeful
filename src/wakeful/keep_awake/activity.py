"""Simulated-activity backend (opt-in) and the composite that pairs it with the native one.

Chat apps such as Slack and Teams decide you are "away" from the OS idle
timer, which only real (or synthetic) input resets. Native power assertions do
not touch it. When the user opts in, :class:`MouseActivityBackend` nudges the
cursor one pixel and immediately back every ``interval_seconds`` from a single
sleeping thread. It never clicks, never types, never changes focus, and stops
the moment it is released.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Sequence
from typing import Protocol

from .backend import BackendError, KeepAwakeBackend

log = logging.getLogger(__name__)

DEFAULT_INTERVAL_SECONDS = 60.0
MIN_INTERVAL_SECONDS = 1.0


class CursorMover(Protocol):
    """Platform seam: the only thing that knows how to inject a cursor move.

    ``open()`` acquires any resources and may raise :class:`BackendError`;
    ``nudge()`` moves the cursor by the smallest possible amount and back;
    ``close()`` releases resources and never raises.
    """

    def open(self) -> None: ...

    def nudge(self) -> None: ...

    def close(self) -> None: ...


class MouseActivityBackend:
    name = "mouse-activity"

    def __init__(self, mover: CursorMover, interval_seconds: float = DEFAULT_INTERVAL_SECONDS) -> None:
        self._mover = mover
        self._interval = max(float(interval_seconds), MIN_INTERVAL_SECONDS)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def interval_seconds(self) -> float:
        return self._interval

    def acquire(self) -> None:
        if self._thread is not None:
            return
        self._mover.open()
        try:
            self._mover.nudge()  # fail fast if injection is not permitted
        except BackendError:
            self._mover.close()
            raise
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="wakeful-activity", daemon=True)
        self._thread.start()
        log.debug("mouse activity started (every %gs)", self._interval)

    def release(self) -> None:
        thread, self._thread = self._thread, None
        if thread is None:
            return
        self._stop.set()
        thread.join(timeout=5.0)
        if thread.is_alive():
            log.warning("activity thread did not stop within 5s")
        self._mover.close()
        log.debug("mouse activity stopped")

    def _run(self) -> None:
        failures = 0
        while not self._stop.wait(self._interval):
            try:
                self._mover.nudge()
                failures = 0
            except Exception:  # noqa: BLE001 - keep the loop alive, log once per streak
                failures += 1
                if failures == 1:
                    log.warning("cursor nudge failed; will keep retrying", exc_info=True)


class CompositeBackend:
    """Acquire several backends as one; roll back on partial failure."""

    def __init__(self, backends: Sequence[KeepAwakeBackend]) -> None:
        self._backends = list(backends)
        self.name = "+".join(b.name for b in self._backends)

    def acquire(self) -> None:
        acquired: list[KeepAwakeBackend] = []
        for backend in self._backends:
            try:
                backend.acquire()
            except Exception:
                for done in reversed(acquired):
                    try:
                        done.release()
                    except Exception:  # noqa: BLE001
                        log.exception("rollback of %s failed", done.name)
                raise
            acquired.append(backend)

    def release(self) -> None:
        errors: list[str] = []
        for backend in reversed(self._backends):
            try:
                backend.release()
            except Exception as exc:  # noqa: BLE001 - release every backend regardless
                log.exception("release of %s failed", backend.name)
                errors.append(f"{backend.name}: {exc}")
        if errors:
            raise BackendError("; ".join(errors))
