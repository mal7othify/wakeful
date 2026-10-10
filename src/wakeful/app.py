"""Entry point: the small tkinter window, or ``--headless`` for terminal use.

Lifecycle guarantees: the keep-awake request is released when the window is
closed, when SIGINT/SIGTERM/SIGHUP arrives, when an exception escapes the
main loop, and (as a last resort) by the service's own finalizer at exit.
"""

from __future__ import annotations

import argparse
import logging
import signal
import socket
import sys
import threading
from collections.abc import Callable
from pathlib import Path

from .config import KeepAwakeConfig, default_config_path, load_config, save_config
from .icon import ICON_PNG_BASE64
from .keep_awake import MODES, MOUSE_MODE, NATIVE_MODE, KeepAwakeError, KeepAwakeService, create_backend

log = logging.getLogger(__name__)

ACTIVE_HINT = "The computer will remain awake while this is enabled."
ACTIVE_MOUSE_HINT = "The computer will remain awake and appear active while this is enabled."
INACTIVE_HINT = "Normal power management is in effect."
RETRY_HINT = "Keep Awake will turn on by itself once this is resolved and you return to this window."
MOUSE_OPTION_LABEL = "Simulate activity (keeps Slack / Teams showing you as active)"

ServiceFactory = Callable[[str], KeepAwakeService]


def build_service(mode: str, interval_seconds: float) -> KeepAwakeService:
    """Never raises: an unusable mode yields a service that explains why on start()."""
    try:
        return KeepAwakeService(create_backend(mode, interval_seconds=interval_seconds))
    except KeepAwakeError as exc:
        log.error("keep-awake backend unavailable: %s", exc)
        return KeepAwakeService(None, unavailable_reason=str(exc))


class KeepAwakeWindow:
    def __init__(self, make_service: ServiceFactory, cfg: KeepAwakeConfig, cfg_path: Path) -> None:
        import tkinter as tk
        from tkinter import ttk

        self._make_service = make_service
        self._service = make_service(cfg.mode)
        self._cfg = cfg
        self._cfg_path = cfg_path
        self._closed = False
        self._retry_on_focus = False  # set when enabling failed; retried when the window regains focus

        self.root = tk.Tk()
        self.root.title("Wakeful")
        self.root.resizable(False, False)
        self._set_icon()
        self.root.protocol("WM_DELETE_WINDOW", self.shutdown)
        self.root.bind("<FocusIn>", self._on_focus_in)
        self.root.bind("<Activate>", self._on_focus_in)  # macOS/Windows app activation

        frame = ttk.Frame(self.root, padding=16)
        frame.grid(sticky="nsew")

        ttk.Label(frame, text="Keep Awake", font=("TkDefaultFont", 14, "bold")).grid(row=0, column=0, sticky="w")
        self._enabled = tk.BooleanVar(value=False)
        self._toggle = ttk.Checkbutton(
            frame, text="OFF", width=6, variable=self._enabled, command=self._on_toggle, style="Toolbutton"
        )
        self._toggle.grid(row=1, column=0, sticky="w", pady=(10, 10))
        self._status = ttk.Label(frame, text="Status: Inactive")
        self._status.grid(row=2, column=0, sticky="w")
        self._hint = ttk.Label(frame, text=INACTIVE_HINT, wraplength=300, foreground="gray40")
        self._hint.grid(row=3, column=0, sticky="w", pady=(4, 12))
        self._mouse_mode = tk.BooleanVar(value=cfg.mode == MOUSE_MODE)
        ttk.Checkbutton(frame, text=MOUSE_OPTION_LABEL, variable=self._mouse_mode, command=self._on_mode).grid(
            row=4, column=0, sticky="w"
        )

        if not self._service.available:
            self._show_error(self._service.unavailable_reason or "Keep Awake is unavailable.")
        elif cfg.enabled:
            self._apply(True)  # restore the previous state before the first paint

    def _set_icon(self) -> None:
        """Window / dock icon. Cosmetic, so any failure is logged and ignored."""
        import tkinter as tk

        try:
            self._icon = tk.PhotoImage(data=ICON_PNG_BASE64)  # keep a reference or Tk drops the image
            self.root.iconphoto(True, self._icon)
        except Exception:  # noqa: BLE001 - e.g. a Tk build without PNG support
            log.debug("could not set the window icon", exc_info=True)

    # -- behaviour ----------------------------------------------------------

    def _on_toggle(self) -> None:
        self._retry_on_focus = False
        self._apply(self._enabled.get())

    def _on_focus_in(self, event) -> None:
        """Retry a failed enable when the user comes back (e.g. after granting a permission)."""
        if event.widget is self.root and self._retry_on_focus and not self._closed:
            self._retry_on_focus = False
            log.debug("window regained focus; retrying keep-awake start")
            self._apply(True)

    def _on_mode(self) -> None:
        mode = MOUSE_MODE if self._mouse_mode.get() else NATIVE_MODE
        was_active = self._service.is_active()
        self._service.close()
        self._service = self._make_service(mode)
        self._cfg.mode = mode
        log.info("keep-awake mode changed to %s", mode)
        if was_active:
            self._apply(True)
        else:
            self._render()
            self._save()

    def _apply(self, enabled: bool) -> None:
        try:
            if enabled:
                self._service.start()
            else:
                self._service.stop()
        except KeepAwakeError as exc:
            self._retry_on_focus = enabled and self._service.available
            self._show_error(str(exc), retrying=self._retry_on_focus)
            self._cfg.enabled = False
            self._save()
            return
        self._retry_on_focus = False
        self._render()
        self._cfg.enabled = self._service.is_active()
        self._save()

    def _save(self) -> None:
        try:
            save_config(self._cfg_path, self._cfg)
        except OSError as exc:
            log.warning("could not save config to %s: %s", self._cfg_path, exc)

    def _render(self) -> None:
        active = self._service.is_active()
        self._enabled.set(active)
        self._toggle.configure(text="ON" if active else "OFF")
        self._status.configure(text="Status: Active" if active else "Status: Inactive")
        hint = (ACTIVE_MOUSE_HINT if self._cfg.mode == MOUSE_MODE else ACTIVE_HINT) if active else INACTIVE_HINT
        self._hint.configure(text=hint, foreground="gray40")

    def _show_error(self, message: str, retrying: bool = False) -> None:
        self._enabled.set(False)
        self._toggle.configure(text="OFF")
        self._status.configure(text="Status: Error")
        self._hint.configure(text=f"{message}\n{RETRY_HINT}" if retrying else message, foreground="red3")

    def shutdown(self) -> None:
        """Idempotent; safe from the close button, a signal, or an exception path."""
        if self._closed:
            return
        self._closed = True
        log.debug("window shutting down")
        self._service.close()
        try:
            self.root.quit()
            self.root.destroy()
        except Exception:  # noqa: BLE001 - Tk may already be torn down
            log.debug("tk teardown raised", exc_info=True)

    def run(self) -> None:
        try:
            self.root.mainloop()
        finally:
            self.shutdown()


def install_signal_handlers(root, callback) -> tuple[socket.socket, socket.socket] | None:
    """Route SIGINT/SIGTERM/SIGHUP to ``callback`` on the Tk thread.

    Python only runs signal handlers in between bytecodes, and the Tk main
    loop blocks in C. ``signal.set_wakeup_fd`` plus a Tcl file handler wakes
    the loop the instant a signal arrives, so no polling timer is needed. On
    Windows (no Tcl file handlers) the window's close button is the exit path.
    """

    def _handler(signum, _frame):
        log.info("received signal %s; shutting down", signal.Signals(signum).name)
        try:
            root.after_idle(callback)
        except Exception:  # noqa: BLE001 - Tk already gone
            callback()

    for name in ("SIGINT", "SIGTERM", "SIGHUP"):
        sig = getattr(signal, name, None)
        if sig is not None:
            signal.signal(sig, _handler)

    if not hasattr(root.tk, "createfilehandler"):
        return None
    import tkinter

    reader, writer = socket.socketpair()
    reader.setblocking(False)
    writer.setblocking(False)
    signal.set_wakeup_fd(writer.fileno())

    def _drain(_file, _mask):
        try:
            reader.recv(64)
        except (BlockingIOError, OSError):
            pass

    root.tk.createfilehandler(reader, tkinter.READABLE, _drain)
    return reader, writer


def run_headless(service: KeepAwakeService) -> int:
    """Enable Keep Awake and block until Ctrl+C / SIGTERM. No window, no polling."""
    stop = threading.Event()

    def _handler(signum, _frame):
        log.info("received signal %s; shutting down", signal.Signals(signum).name)
        stop.set()

    for name in ("SIGINT", "SIGTERM", "SIGHUP"):
        sig = getattr(signal, name, None)
        if sig is not None:
            signal.signal(sig, _handler)

    with service:
        try:
            service.start()
        except KeepAwakeError as exc:
            print(f"Keep Awake could not be enabled: {exc}", file=sys.stderr)
            return 1
        print(f"Keep Awake is active (backend: {service.backend_name}). Press Ctrl+C to stop.", flush=True)
        if sys.platform.startswith("win"):
            while not stop.wait(1.0):  # Windows cannot interrupt an indefinite wait with Ctrl+C
                pass
        else:
            stop.wait()
    print("Keep Awake stopped.", flush=True)
    return 0


def _configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    if sys.stderr is None:  # windowed executables have no console
        logging.basicConfig(level=level, handlers=[logging.NullHandler()])
    else:
        logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wakeful", description="Keep the computer awake, non-disruptively.")
    parser.add_argument("-v", "--verbose", action="store_true", help="enable debug logging")
    parser.add_argument("--config", type=Path, default=None, help="path to config.json")
    parser.add_argument("--mode", choices=MODES, default=None, help="override the configured mode")
    parser.add_argument("--interval", type=float, default=None, metavar="SECONDS", help="mouse-mode nudge interval")
    parser.add_argument("--headless", action="store_true", help="no window: enable now, stop on Ctrl+C")
    args = parser.parse_args(argv)
    _configure_logging(args.verbose)

    cfg_path = args.config or default_config_path()
    cfg = load_config(cfg_path)
    if args.mode:
        cfg.mode = args.mode
    if args.interval:
        cfg.interval_seconds = args.interval

    def make_service(mode: str) -> KeepAwakeService:
        return build_service(mode, cfg.interval_seconds)

    if args.headless:
        return run_headless(make_service(cfg.mode))

    try:
        import tkinter  # noqa: F401
    except ImportError:
        print(
            "Wakeful needs tkinter, which is missing from this Python (or use --headless).\n"
            "  Debian/Ubuntu: sudo apt install python3-tk\n"
            "  Fedora:        sudo dnf install python3-tkinter\n"
            "  Arch:          sudo pacman -S tk\n"
            "  macOS:         install Python from python.org, or `brew install python-tk`",
            file=sys.stderr,
        )
        return 2

    window = KeepAwakeWindow(make_service, cfg, cfg_path)
    wakeup = install_signal_handlers(window.root, window.shutdown)
    try:
        window.run()
    finally:
        window.shutdown()
        if wakeup is not None:
            signal.set_wakeup_fd(-1)
            for sock in wakeup:
                sock.close()
    log.debug("exited cleanly")
    return 0
