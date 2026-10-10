"""macOS backend: an IOKit power-management assertion via ctypes.

This is what ``caffeinate -d`` does internally, minus the child process. The
assertion lives in our own process, so the kernel drops it automatically if we
crash. Works on Apple Silicon and Intel alike, needs no privileges, and never
touches Energy Saver settings.
"""

from __future__ import annotations

import logging

from ..backend import BackendError

log = logging.getLogger(__name__)

IOKIT_PATH = "/System/Library/Frameworks/IOKit.framework/IOKit"
CORE_FOUNDATION_PATH = "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"

#: Prevents display dimming/sleep due to idleness, which also implies that the
#: system will not idle-sleep. Equivalent to ``caffeinate -d``.
ASSERTION_TYPE = "PreventUserIdleDisplaySleep"
DEFAULT_REASON = "Wakeful: Keep Awake is enabled"

K_IOPM_ASSERTION_LEVEL_ON = 255
K_CF_STRING_ENCODING_UTF8 = 0x08000100
K_IO_RETURN_SUCCESS = 0


class IOKitPowerApi:
    """Thin ctypes wrapper around ``IOPMAssertionCreateWithName``/``Release``.

    This is the mockable seam: tests substitute an object with the same two
    methods, so the native libraries are only loaded on a real Mac.
    """

    def __init__(self) -> None:
        import ctypes

        self._ctypes = ctypes
        self._iokit = ctypes.CDLL(IOKIT_PATH)
        self._cf = ctypes.CDLL(CORE_FOUNDATION_PATH)

        self._cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
        self._cf.CFStringCreateWithCString.restype = ctypes.c_void_p
        self._cf.CFRelease.argtypes = [ctypes.c_void_p]
        self._cf.CFRelease.restype = None

        self._iokit.IOPMAssertionCreateWithName.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint32),
        ]
        self._iokit.IOPMAssertionCreateWithName.restype = ctypes.c_int32
        self._iokit.IOPMAssertionRelease.argtypes = [ctypes.c_uint32]
        self._iokit.IOPMAssertionRelease.restype = ctypes.c_int32

    def _cfstring(self, text: str) -> int:
        ref = self._cf.CFStringCreateWithCString(None, text.encode("utf-8"), K_CF_STRING_ENCODING_UTF8)
        if not ref:
            raise BackendError("CFStringCreateWithCString returned NULL.")
        return ref

    def create_assertion(self, assertion_type: str, reason: str) -> int:
        type_ref = self._cfstring(assertion_type)
        try:
            name_ref = self._cfstring(reason)
            try:
                assertion_id = self._ctypes.c_uint32(0)
                ret = self._iokit.IOPMAssertionCreateWithName(
                    type_ref, K_IOPM_ASSERTION_LEVEL_ON, name_ref, self._ctypes.byref(assertion_id)
                )
                if ret != K_IO_RETURN_SUCCESS:
                    raise BackendError(f"IOPMAssertionCreateWithName failed (IOReturn 0x{ret & 0xFFFFFFFF:08x}).")
                return assertion_id.value
            finally:
                self._cf.CFRelease(name_ref)
        finally:
            self._cf.CFRelease(type_ref)

    def release_assertion(self, assertion_id: int) -> None:
        ret = self._iokit.IOPMAssertionRelease(assertion_id)
        if ret != K_IO_RETURN_SUCCESS:
            raise BackendError(f"IOPMAssertionRelease failed (IOReturn 0x{ret & 0xFFFFFFFF:08x}).")


class MacOSBackend:
    name = "macos-native"

    def __init__(self, api: IOKitPowerApi | None = None, reason: str = DEFAULT_REASON) -> None:
        self._api = api  # injectable for tests; loaded lazily otherwise
        self._reason = reason
        self._assertion_id: int | None = None

    def acquire(self) -> None:
        if self._assertion_id is not None:
            return
        if self._api is None:
            try:
                self._api = IOKitPowerApi()
            except (OSError, AttributeError) as exc:
                raise BackendError(f"IOKit power-management API is unavailable: {exc}") from exc
        self._assertion_id = self._api.create_assertion(ASSERTION_TYPE, self._reason)
        log.debug("IOPM assertion %s created (id %d)", ASSERTION_TYPE, self._assertion_id)

    def release(self) -> None:
        if self._assertion_id is None:
            return
        assertion_id, self._assertion_id = self._assertion_id, None
        assert self._api is not None
        self._api.release_assertion(assertion_id)
        log.debug("IOPM assertion %d released", assertion_id)


# -- simulated activity -------------------------------------------------------

import ctypes  # noqa: E402

APPLICATION_SERVICES_PATH = "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
K_CG_EVENT_MOUSE_MOVED = 5
K_CG_HID_EVENT_TAP = 0
K_CG_MOUSE_BUTTON_LEFT = 0

ACCESSIBILITY_HELP = (
    "Simulated activity needs Accessibility permission. Open System Settings → Privacy & Security → "
    "Accessibility and enable Wakeful (or the terminal/IDE it runs from). If Wakeful is already listed "
    "there, remove it with the − button and add it again: an updated unsigned build counts as a new app."
)


class CGPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


class QuartzApi:
    """ctypes wrapper around the handful of Quartz / HIServices calls we need (mockable seam)."""

    def __init__(self) -> None:
        self._app = ctypes.CDLL(APPLICATION_SERVICES_PATH)
        self._cf = ctypes.CDLL(CORE_FOUNDATION_PATH)
        app, cf = self._app, self._cf

        app.CGEventCreate.argtypes = [ctypes.c_void_p]
        app.CGEventCreate.restype = ctypes.c_void_p
        app.CGEventGetLocation.argtypes = [ctypes.c_void_p]
        app.CGEventGetLocation.restype = CGPoint
        app.CGEventCreateMouseEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint32, CGPoint, ctypes.c_uint32]
        app.CGEventCreateMouseEvent.restype = ctypes.c_void_p
        app.CGEventPost.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
        app.CGEventPost.restype = None
        app.AXIsProcessTrustedWithOptions.argtypes = [ctypes.c_void_p]
        app.AXIsProcessTrustedWithOptions.restype = ctypes.c_bool

        cf.CFRelease.argtypes = [ctypes.c_void_p]
        cf.CFRelease.restype = None
        cf.CFDictionaryCreate.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.c_long,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        cf.CFDictionaryCreate.restype = ctypes.c_void_p

    def is_trusted(self, prompt: bool) -> bool:
        """``AXIsProcessTrustedWithOptions``; with ``prompt`` macOS shows its permission dialog."""
        key = ctypes.c_void_p.in_dll(self._app, "kAXTrustedCheckOptionPrompt")
        value = ctypes.c_void_p.in_dll(self._cf, "kCFBooleanTrue" if prompt else "kCFBooleanFalse")
        keys = (ctypes.c_void_p * 1)(key.value)
        values = (ctypes.c_void_p * 1)(value.value)
        key_callbacks = ctypes.addressof(ctypes.c_char.in_dll(self._cf, "kCFTypeDictionaryKeyCallBacks"))
        value_callbacks = ctypes.addressof(ctypes.c_char.in_dll(self._cf, "kCFTypeDictionaryValueCallBacks"))
        options = self._cf.CFDictionaryCreate(None, keys, values, 1, key_callbacks, value_callbacks)
        try:
            return bool(self._app.AXIsProcessTrustedWithOptions(options))
        finally:
            if options:
                self._cf.CFRelease(options)

    def cursor_position(self) -> tuple[float, float]:
        event = self._app.CGEventCreate(None)
        if not event:
            raise BackendError("CGEventCreate failed.")
        try:
            point = self._app.CGEventGetLocation(event)
            return point.x, point.y
        finally:
            self._cf.CFRelease(event)

    def post_move(self, x: float, y: float) -> None:
        event = self._app.CGEventCreateMouseEvent(None, K_CG_EVENT_MOUSE_MOVED, CGPoint(x, y), K_CG_MOUSE_BUTTON_LEFT)
        if not event:
            raise BackendError("CGEventCreateMouseEvent failed.")
        try:
            self._app.CGEventPost(K_CG_HID_EVENT_TAP, event)
        finally:
            self._cf.CFRelease(event)


class MacOSCursorMover:
    """Posts a 1 px move and back to the HID event tap; needs Accessibility permission."""

    def __init__(self, api: QuartzApi | None = None) -> None:
        self._api = api
        self._prompted = False  # show the system permission dialog at most once per process

    def open(self) -> None:
        if self._api is None:
            try:
                self._api = QuartzApi()
            except (OSError, AttributeError, ValueError) as exc:
                raise BackendError(f"Quartz event API is unavailable: {exc}") from exc
        prompt, self._prompted = not self._prompted, True
        if not self._api.is_trusted(prompt=prompt):
            raise BackendError(ACCESSIBILITY_HELP)

    def nudge(self) -> None:
        assert self._api is not None
        x, y = self._api.cursor_position()
        self._api.post_move(x + 1, y)
        self._api.post_move(x, y)

    def close(self) -> None:
        pass
