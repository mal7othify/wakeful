"""Backend contract shared by every platform implementation."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


class KeepAwakeError(Exception):
    """Base class for all keep-awake failures. The message is safe to show to users."""


class BackendError(KeepAwakeError):
    """A backend failed to acquire or release its native keep-awake request."""


class BackendUnavailable(KeepAwakeError):
    """No backend can be used on this platform or with this configuration."""


@runtime_checkable
class KeepAwakeBackend(Protocol):
    """One native keep-awake mechanism.

    Contract:
    * ``acquire()`` asks the OS to stay awake. It raises :class:`BackendError`
      on failure and must be idempotent (a second call while held is a no-op).
    * ``release()`` undoes ``acquire()``. It must be idempotent and safe to call
      even if ``acquire()`` was never called or failed half-way.
    """

    name: str

    def acquire(self) -> None: ...

    def release(self) -> None: ...
