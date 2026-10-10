"""Backend selection. Importing this package never touches native APIs."""

from __future__ import annotations

import logging
import sys

from ..activity import DEFAULT_INTERVAL_SECONDS, CompositeBackend, CursorMover, MouseActivityBackend
from ..backend import BackendUnavailable, KeepAwakeBackend

log = logging.getLogger(__name__)

NATIVE_MODE = "native"
MOUSE_MODE = "mouse"
MODES = (NATIVE_MODE, MOUSE_MODE)


def _native_backend(platform: str) -> KeepAwakeBackend:
    if platform.startswith("win"):
        from .windows import WindowsBackend

        return WindowsBackend()
    if platform == "darwin":
        from .macos import MacOSBackend

        return MacOSBackend()
    if platform.startswith("linux"):
        from .linux import LinuxBackend

        return LinuxBackend()
    raise BackendUnavailable(f"Keep Awake is not supported on platform {platform!r}.")


def _cursor_mover(platform: str) -> CursorMover:
    if platform.startswith("win"):
        from .windows import WindowsCursorMover

        return WindowsCursorMover()
    if platform == "darwin":
        from .macos import MacOSCursorMover

        return MacOSCursorMover()
    if platform.startswith("linux"):
        from .linux import X11CursorMover

        return X11CursorMover()
    raise BackendUnavailable(f"Simulated activity is not supported on platform {platform!r}.")


def create_backend(
    mode: str = NATIVE_MODE,
    platform: str | None = None,
    interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
) -> KeepAwakeBackend:
    """Return the backend for ``mode`` on ``platform`` (defaults to :data:`sys.platform`).

    ``native`` keeps the machine awake through the OS power API. ``mouse`` does
    that too and additionally simulates activity so chat apps see you as
    present. Backends are lazy: nothing native runs until ``acquire()``.
    """
    if mode not in MODES:
        raise BackendUnavailable(f"Unsupported keep-awake mode {mode!r}; expected one of {MODES}.")
    platform = sys.platform if platform is None else platform
    backend: KeepAwakeBackend = _native_backend(platform)
    if mode == MOUSE_MODE:
        backend = CompositeBackend([backend, MouseActivityBackend(_cursor_mover(platform), interval_seconds)])
    log.debug("selected keep-awake backend %s for platform %s", backend.name, platform)
    return backend
