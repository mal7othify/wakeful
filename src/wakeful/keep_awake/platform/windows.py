"""Windows backend: ``SetThreadExecutionState`` via ctypes (no extra deps).

The execution state is *per thread* and is cleared automatically when the
calling thread (and therefore the process) exits, so a crash can never leave
the machine stuck awake. ``acquire()`` and ``release()`` must be called from
the same long-lived thread; the UI calls both from the main thread.

Nothing here touches the registry, the power plan, or system-wide settings,
and no elevated privileges are required.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from ..backend import BackendError

log = logging.getLogger(__name__)

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002

#: Keep both the system and the display awake until we say otherwise.
AWAKE_FLAGS = ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
#: Restore the normal idle behaviour.
RESTORE_FLAGS = ES_CONTINUOUS

SetExecutionState = Callable[[int], int]


def load_set_thread_execution_state() -> SetExecutionState:
    """Resolve ``kernel32.SetThreadExecutionState``. Raises OSError/AttributeError off-Windows."""
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    fn = kernel32.SetThreadExecutionState
    fn.argtypes = [ctypes.c_uint32]
    fn.restype = ctypes.c_uint32
    return fn


class WindowsBackend:
    name = "windows-native"

    def __init__(self, set_thread_execution_state: SetExecutionState | None = None) -> None:
        # Injectable for tests; resolved lazily on first use otherwise.
        self._set_state = set_thread_execution_state
        self._held = False
        self._thread_id: int | None = None

    def _call(self, flags: int) -> int:
        if self._set_state is None:
            try:
                self._set_state = load_set_thread_execution_state()
            except (OSError, AttributeError) as exc:
                raise BackendError(f"SetThreadExecutionState is unavailable: {exc}") from exc
        previous = self._set_state(flags)
        if not previous:
            # The API returns the previous state, or 0 (NULL) on failure.
            raise BackendError("SetThreadExecutionState failed (returned NULL).")
        return previous

    def acquire(self) -> None:
        if self._held:
            return
        previous = self._call(AWAKE_FLAGS)
        self._held = True
        self._thread_id = threading.get_ident()
        log.debug("SetThreadExecutionState(0x%08x) ok, previous state 0x%08x", AWAKE_FLAGS, previous)

    def release(self) -> None:
        if not self._held:
            return
        if self._thread_id != threading.get_ident():
            log.warning("release() called from a different thread than acquire(); the OS may ignore it")
        self._held = False
        self._thread_id = None
        self._call(RESTORE_FLAGS)
        log.debug("SetThreadExecutionState(0x%08x) restored normal power behaviour", RESTORE_FLAGS)


# -- simulated activity -------------------------------------------------------

import ctypes  # noqa: E402

INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_int32),  # LONG is always 32-bit on Windows
        ("dy", ctypes.c_int32),
        ("mouseData", ctypes.c_uint32),
        ("dwFlags", ctypes.c_uint32),
        ("time", ctypes.c_uint32),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint32), ("mi", MOUSEINPUT)]


SendInput = Callable[[int, "ctypes.Array[INPUT]", int], int]


def load_send_input() -> SendInput:
    user32 = ctypes.WinDLL("user32", use_last_error=True)  # type: ignore[attr-defined]
    fn = user32.SendInput
    fn.argtypes = [ctypes.c_uint, ctypes.POINTER(INPUT), ctypes.c_int]
    fn.restype = ctypes.c_uint
    return fn


def build_nudge() -> ctypes.Array[INPUT]:
    """Two relative moves, +1 px and -1 px, submitted as one atomic batch."""
    batch = (INPUT * 2)()
    for item, dx in ((batch[0], 1), (batch[1], -1)):
        item.type = INPUT_MOUSE
        item.mi.dx = dx
        item.mi.dy = 0
        item.mi.dwFlags = MOUSEEVENTF_MOVE
    return batch


class WindowsCursorMover:
    """Injects the nudge with ``SendInput``; resets the system idle timer."""

    def __init__(self, send_input: SendInput | None = None) -> None:
        self._send_input = send_input

    def open(self) -> None:
        if self._send_input is None:
            try:
                self._send_input = load_send_input()
            except (OSError, AttributeError) as exc:
                raise BackendError(f"SendInput is unavailable: {exc}") from exc

    def nudge(self) -> None:
        assert self._send_input is not None
        batch = build_nudge()
        sent = self._send_input(len(batch), batch, ctypes.sizeof(INPUT))
        if sent != len(batch):
            get_last_error = getattr(ctypes, "get_last_error", lambda: 0)
            raise BackendError(f"SendInput injected {sent} of {len(batch)} events (error {get_last_error()}).")

    def close(self) -> None:
        pass
