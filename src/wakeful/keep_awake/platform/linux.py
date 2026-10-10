"""Linux backend: hold idle inhibitors through the standard command-line helpers.

Two layers are used because they cover different things:

* ``systemd-inhibit --what=idle`` (logind) blocks logind's own idle action and
  is desktop-agnostic. It works on every systemd distribution (Ubuntu, Debian,
  Fedora, Arch, openSUSE, ...).
* GNOME and KDE ignore logind idle inhibitors for *their* screen blanking and
  automatic suspend; they only honour session inhibitors. When one of those
  desktops is detected, ``gnome-session-inhibit`` / ``kde-inhibit`` is used as
  well. Nothing is assumed about X11 vs Wayland; these helpers work on both.

Each helper wraps the command ``sh -c 'printf ok; exec cat'``. The inhibitor
is held for exactly as long as that ``cat`` lives, and ``cat`` lives for
exactly as long as the pipe we hold open. Closing the pipe (or dying, however
abruptly) releases the inhibitor, so there is no way to leave the machine
stuck awake. The ``ok`` byte is a readiness handshake: the helper only runs
the command after the inhibitor has been taken, so once we read it we know
the inhibitor is in place, without any polling.

Manual suspend, lock, logout, and shutdown are never blocked: only ``idle``
is inhibited.
"""

from __future__ import annotations

import logging
import os
import select
import shutil
import subprocess
from collections.abc import Callable, Mapping, Sequence

from ..backend import BackendError

log = logging.getLogger(__name__)

WHO = "Wakeful"
WHY = "Keep Awake is enabled"
READY = b"ok"
HOLD_COMMAND: tuple[str, ...] = ("sh", "-c", "printf ok; exec cat")

SYSTEMD_INHIBIT: tuple[str, ...] = (
    "systemd-inhibit",
    "--what=idle",
    f"--who={WHO}",
    f"--why={WHY}",
    "--mode=block",
)

#: Desktop-specific session inhibitors, keyed by a token of XDG_CURRENT_DESKTOP.
DESKTOP_INHIBITORS: dict[str, tuple[str, ...]] = {
    "gnome": ("gnome-session-inhibit", "--app-id", "wakeful", "--inhibit", "idle", "--reason", WHY),
    "kde": ("kde-inhibit", "--power", "--screenSaver"),
}

Popen = Callable[..., "subprocess.Popen[bytes]"]
Which = Callable[[str], "str | None"]


def desktop_tokens(environ: Mapping[str, str]) -> set[str]:
    """Lower-cased tokens of XDG_CURRENT_DESKTOP, e.g. ``ubuntu:GNOME`` -> {ubuntu, gnome}."""
    return {token.strip().lower() for token in environ.get("XDG_CURRENT_DESKTOP", "").split(":") if token.strip()}


def candidate_commands(environ: Mapping[str, str], which: Which) -> list[tuple[str, tuple[str, ...]]]:
    """Inhibitor commands worth trying on this system, in order."""
    candidates: list[tuple[str, tuple[str, ...]]] = []
    if which(SYSTEMD_INHIBIT[0]):
        candidates.append(("systemd-inhibit", SYSTEMD_INHIBIT + HOLD_COMMAND))
    tokens = desktop_tokens(environ)
    for token, argv in DESKTOP_INHIBITORS.items():
        if token in tokens and which(argv[0]):
            candidates.append((argv[0], argv + HOLD_COMMAND))
    return candidates


class LinuxBackend:
    name = "linux-native"

    def __init__(
        self,
        popen: Popen = subprocess.Popen,
        which: Which = shutil.which,
        environ: Mapping[str, str] | None = None,
        ready_timeout: float = 5.0,
    ) -> None:
        # All three collaborators are injectable so tests never spawn processes.
        self._popen = popen
        self._which = which
        self._environ = os.environ if environ is None else environ
        self._ready_timeout = ready_timeout
        self._procs: list[tuple[str, subprocess.Popen[bytes]]] = []

    # -- acquire ------------------------------------------------------------

    def acquire(self) -> None:
        if self._procs:
            return
        candidates = candidate_commands(self._environ, self._which)
        if not candidates:
            raise BackendError(
                "No idle inhibitor found. Install systemd (systemd-inhibit) or run under GNOME or KDE."
            )
        failures: list[str] = []
        for label, argv in candidates:
            try:
                proc = self._spawn(argv)
            except BackendError as exc:
                log.warning("inhibitor %s failed: %s", label, exc)
                failures.append(f"{label}: {exc}")
                continue
            self._procs.append((label, proc))
            log.debug("inhibitor %s active (pid %s)", label, proc.pid)
        if not self._procs:
            raise BackendError("Could not start any idle inhibitor. " + "; ".join(failures))

    def _spawn(self, argv: Sequence[str]) -> subprocess.Popen[bytes]:
        try:
            proc = self._popen(
                list(argv), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
        except OSError as exc:
            raise BackendError(str(exc)) from exc
        try:
            self._await_ready(proc)
        except BackendError:
            self._stop_proc(argv[0], proc)
            raise
        return proc

    def _await_ready(self, proc: subprocess.Popen[bytes]) -> None:
        assert proc.stdout is not None
        readable, _, _ = select.select([proc.stdout], [], [], self._ready_timeout)
        if not readable:
            raise BackendError(f"timed out after {self._ready_timeout:g}s waiting for the inhibitor to start")
        data = proc.stdout.read(len(READY))
        if data != READY:
            raise BackendError(self._exit_reason(proc))

    def _exit_reason(self, proc: subprocess.Popen[bytes]) -> str:
        try:
            proc.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            return "inhibitor produced unexpected output"
        stderr = b""
        if proc.stderr is not None:
            try:
                stderr = proc.stderr.read() or b""
            except OSError:
                pass
        detail = stderr.decode("utf-8", "replace").strip().splitlines()
        suffix = f": {detail[-1]}" if detail else ""
        return f"exited with status {proc.returncode}{suffix}"

    # -- release ------------------------------------------------------------

    def release(self) -> None:
        procs, self._procs = self._procs, []
        for label, proc in procs:
            self._stop_proc(label, proc)
            log.debug("inhibitor %s released", label)

    @staticmethod
    def _stop_proc(label: str, proc: subprocess.Popen[bytes]) -> None:
        """Close the pipe (ending ``cat`` and thus the inhibitor), escalating only if needed."""
        for stream in (proc.stdin,):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
        try:
            proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            log.warning("inhibitor %s did not exit after pipe close; terminating", label)
            proc.terminate()
            try:
                proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        for stream in (proc.stdout, proc.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass


# -- simulated activity -------------------------------------------------------

import ctypes  # noqa: E402
from ctypes.util import find_library  # noqa: E402

X11_HELP = (
    "No X11 display is available (DISPLAY is unset). Simulated activity needs X11 or XWayland; "
    "on a Wayland session XWayland is normally started automatically."
)
XTEST_HELP = "The X11 XTest extension is unavailable; install libxtst6 (Debian/Ubuntu) or libXtst (Fedora/Arch)."


class XTestApi:
    """ctypes wrapper around libX11 + libXtst (mockable seam)."""

    def __init__(self) -> None:
        self._x11 = ctypes.CDLL(find_library("X11") or "libX11.so.6")
        self._xtst = ctypes.CDLL(find_library("Xtst") or "libXtst.so.6")
        x11, xtst = self._x11, self._xtst
        x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x11.XOpenDisplay.restype = ctypes.c_void_p
        x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
        x11.XCloseDisplay.restype = ctypes.c_int
        x11.XFlush.argtypes = [ctypes.c_void_p]
        x11.XFlush.restype = ctypes.c_int
        xtst.XTestQueryExtension.argtypes = [ctypes.c_void_p] + [ctypes.POINTER(ctypes.c_int)] * 4
        xtst.XTestQueryExtension.restype = ctypes.c_int
        xtst.XTestFakeRelativeMotionEvent.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_ulong]
        xtst.XTestFakeRelativeMotionEvent.restype = ctypes.c_int
        self._display: int | None = None

    def open_display(self, name: str) -> None:
        display = self._x11.XOpenDisplay(name.encode())
        if not display:
            raise BackendError(f"Cannot connect to X display {name!r}.")
        ints = [ctypes.c_int() for _ in range(4)]
        if not self._xtst.XTestQueryExtension(display, *(ctypes.byref(i) for i in ints)):
            self._x11.XCloseDisplay(display)
            raise BackendError(XTEST_HELP)
        self._display = display

    def fake_relative_motion(self, dx: int, dy: int) -> None:
        assert self._display is not None
        if not self._xtst.XTestFakeRelativeMotionEvent(self._display, dx, dy, 0):
            raise BackendError("XTestFakeRelativeMotionEvent failed.")
        self._x11.XFlush(self._display)

    def close_display(self) -> None:
        display, self._display = self._display, None
        if display:
            self._x11.XCloseDisplay(display)


class X11CursorMover:
    """Fakes a 1 px relative motion and back through XTest (works on X11 and XWayland)."""

    def __init__(self, api: XTestApi | None = None, environ: Mapping[str, str] | None = None) -> None:
        self._api = api
        self._environ = os.environ if environ is None else environ
        self._open = False

    def open(self) -> None:
        if self._open:
            return
        display = self._environ.get("DISPLAY")
        if not display:
            raise BackendError(X11_HELP)
        if self._api is None:
            try:
                self._api = XTestApi()
            except (OSError, AttributeError) as exc:
                raise BackendError(f"{XTEST_HELP} ({exc})") from exc
        self._api.open_display(display)
        self._open = True

    def nudge(self) -> None:
        assert self._api is not None
        self._api.fake_relative_motion(1, 0)
        self._api.fake_relative_motion(-1, 0)

    def close(self) -> None:
        if not self._open:
            return
        self._open = False
        assert self._api is not None
        try:
            self._api.close_display()
        except Exception:  # noqa: BLE001
            log.debug("closing X display failed", exc_info=True)
