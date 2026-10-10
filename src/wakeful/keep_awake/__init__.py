"""Keep Awake feature.

The rest of the application only needs :class:`KeepAwakeService` and
:func:`create_backend`; everything platform-specific lives under ``platform``.
"""

from .backend import BackendError, BackendUnavailable, KeepAwakeBackend, KeepAwakeError
from .platform import MODES, MOUSE_MODE, NATIVE_MODE, create_backend
from .service import KeepAwakeService

__all__ = [
    "BackendError",
    "BackendUnavailable",
    "KeepAwakeBackend",
    "KeepAwakeError",
    "KeepAwakeService",
    "MODES",
    "MOUSE_MODE",
    "NATIVE_MODE",
    "create_backend",
]
