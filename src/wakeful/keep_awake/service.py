"""Platform-independent Keep Awake state machine."""

from __future__ import annotations

import logging
import threading
import weakref

from .backend import BackendError, BackendUnavailable, KeepAwakeBackend, KeepAwakeError

log = logging.getLogger(__name__)


def _release_quietly(backend: KeepAwakeBackend, context: str) -> None:
    """Release a backend without ever raising; used for cleanup paths."""
    try:
        backend.release()
    except Exception:  # noqa: BLE001 - cleanup must never propagate
        log.exception("keep-awake cleanup failed (%s, backend=%s)", context, backend.name)


class KeepAwakeService:
    """Owns the enabled/disabled state and drives a single backend.

    The service is safe to use from one thread at a time; a lock guards the
    state transitions so calls from a signal handler or an exit hook cannot
    interleave with a UI toggle.

    Cleanup is guaranteed via :meth:`close` (also triggered by the context
    manager protocol) and via a :mod:`weakref` finalizer that runs when the
    service is garbage-collected or when the interpreter exits.
    """

    def __init__(self, backend: KeepAwakeBackend | None, unavailable_reason: str | None = None) -> None:
        self._backend = backend
        self._unavailable_reason = unavailable_reason
        self._active = False
        self._lock = threading.RLock()
        self._finalizer = (
            weakref.finalize(self, _release_quietly, backend, "finalizer")
            if backend is not None
            else None
        )
        if backend is None:
            log.warning("keep-awake service created without a backend; feature disabled")
        else:
            log.debug("keep-awake backend selected: %s", backend.name)

    # -- status -------------------------------------------------------------

    @property
    def available(self) -> bool:
        """True when a backend exists (the feature can be turned on)."""
        return self._backend is not None

    @property
    def unavailable_reason(self) -> str | None:
        """Why the feature cannot be enabled, or None when a backend exists."""
        if self._backend is not None:
            return None
        return self._unavailable_reason or "Keep Awake is not available on this system."

    @property
    def backend_name(self) -> str:
        return self._backend.name if self._backend is not None else "none"

    def is_active(self) -> bool:
        return self._active

    # -- transitions --------------------------------------------------------

    def start(self) -> None:
        """Enable Keep Awake. Idempotent. Raises :class:`KeepAwakeError` on failure."""
        with self._lock:
            if self._active:
                log.debug("keep-awake start requested but already active")
                return
            if self._backend is None:
                raise BackendUnavailable(self.unavailable_reason)
            log.debug("keep-awake acquiring (backend=%s)", self._backend.name)
            try:
                self._backend.acquire()
            except KeepAwakeError as exc:
                log.error("keep-awake backend %s failed: %s", self._backend.name, exc)
                _release_quietly(self._backend, "partial acquire")
                raise
            except Exception as exc:  # noqa: BLE001 - normalise unexpected backend errors
                log.exception("keep-awake backend %s raised unexpectedly", self._backend.name)
                _release_quietly(self._backend, "partial acquire")
                raise BackendError(f"Could not enable Keep Awake: {exc}") from exc
            self._active = True
            log.info("keep-awake started (backend=%s)", self._backend.name)

    def stop(self) -> None:
        """Disable Keep Awake. Idempotent. Raises :class:`BackendError` if release fails."""
        with self._lock:
            if not self._active:
                log.debug("keep-awake stop requested but already inactive")
                return
            assert self._backend is not None
            self._active = False
            try:
                self._backend.release()
            except KeepAwakeError as exc:
                log.error("keep-awake backend %s failed to release: %s", self._backend.name, exc)
                raise
            except Exception as exc:  # noqa: BLE001
                log.exception("keep-awake backend %s raised unexpectedly on release", self._backend.name)
                raise BackendError(f"Could not disable Keep Awake cleanly: {exc}") from exc
            log.info("keep-awake stopped (backend=%s)", self._backend.name)

    def close(self) -> None:
        """Stop if needed and detach the finalizer. Never raises."""
        with self._lock:
            if self._active and self._backend is not None:
                self._active = False
                _release_quietly(self._backend, "close")
                log.info("keep-awake stopped during cleanup (backend=%s)", self._backend.name)
            if self._finalizer is not None:
                self._finalizer.detach()
                self._finalizer = None

    # -- context manager ----------------------------------------------------

    def __enter__(self) -> KeepAwakeService:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
