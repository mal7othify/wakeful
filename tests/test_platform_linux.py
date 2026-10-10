import io
import socket
import subprocess
import unittest

from wakeful.keep_awake.backend import BackendError
from wakeful.keep_awake.platform.linux import (
    HOLD_COMMAND,
    LinuxBackend,
    candidate_commands,
    desktop_tokens,
)


class FakeProc:
    """Mimics a Popen'd inhibitor wrapper. ``behaviour``: ready | exit | hang | stuck."""

    def __init__(self, argv, behaviour: str):
        self.argv = argv
        self.behaviour = behaviour
        self.pid = 4242
        self.returncode = None
        self.terminated = False
        self.killed = False
        child, parent = socket.socketpair()
        self._child = child
        self.stdout = parent.makefile("rb", buffering=0)
        parent.close()  # the file object keeps the socket alive until it is closed
        self.stdin = io.BytesIO()
        self.stderr = io.BytesIO(b"warning: something\nFailed to inhibit: No such interface\n")
        if behaviour == "ready":
            child.sendall(b"ok")
        elif behaviour == "exit":
            self._finish(1)

    def _finish(self, code: int) -> int:
        self.returncode = code
        self._child.close()
        return code

    def _stdin_closed(self) -> bool:
        return self.stdin.closed

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self.returncode is not None:
            return self.returncode
        if self.behaviour in ("ready", "hang") and self._stdin_closed():
            return self._finish(0)
        if self.killed:
            return self._finish(-9)
        if self.terminated and self.behaviour != "stuck":
            return self._finish(-15)
        raise subprocess.TimeoutExpired(self.argv, timeout)

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True


class FakePopen:
    def __init__(self, behaviours: dict[str, str]):
        self.behaviours = behaviours
        self.procs: list[FakeProc] = []

    def __call__(self, argv, **kwargs):
        name = argv[0]
        behaviour = self.behaviours.get(name)
        if behaviour is None:
            raise FileNotFoundError(2, "No such file or directory", name)
        proc = FakeProc(argv, behaviour)
        self.procs.append(proc)
        return proc


def which_for(*available):
    return lambda name: f"/usr/bin/{name}" if name in available else None


class CandidateSelectionTests(unittest.TestCase):
    def test_desktop_tokens(self):
        self.assertEqual(desktop_tokens({"XDG_CURRENT_DESKTOP": "ubuntu:GNOME"}), {"ubuntu", "gnome"})
        self.assertEqual(desktop_tokens({}), set())

    def test_systemd_only_without_desktop(self):
        cmds = candidate_commands({}, which_for("systemd-inhibit", "gnome-session-inhibit"))
        self.assertEqual([label for label, _ in cmds], ["systemd-inhibit"])
        self.assertEqual(tuple(cmds[0][1][-3:]), HOLD_COMMAND)
        self.assertIn("--what=idle", cmds[0][1])

    def test_gnome_adds_session_inhibit(self):
        env = {"XDG_CURRENT_DESKTOP": "ubuntu:GNOME"}
        cmds = candidate_commands(env, which_for("systemd-inhibit", "gnome-session-inhibit"))
        self.assertEqual([label for label, _ in cmds], ["systemd-inhibit", "gnome-session-inhibit"])

    def test_kde_adds_kde_inhibit(self):
        env = {"XDG_CURRENT_DESKTOP": "KDE"}
        cmds = candidate_commands(env, which_for("kde-inhibit"))
        self.assertEqual([label for label, _ in cmds], ["kde-inhibit"])

    def test_missing_helper_is_skipped(self):
        env = {"XDG_CURRENT_DESKTOP": "GNOME"}
        self.assertEqual(candidate_commands(env, which_for()), [])


class LinuxBackendTests(unittest.TestCase):
    def make(self, behaviours, env=None, available=None, timeout=0.5):
        popen = FakePopen(behaviours)
        available = list(behaviours) if available is None else available
        backend = LinuxBackend(popen=popen, which=which_for(*available), environ=env or {}, ready_timeout=timeout)
        return backend, popen

    def test_acquire_and_release_via_pipe_close(self):
        backend, popen = self.make({"systemd-inhibit": "ready"})
        backend.acquire()
        self.assertEqual(len(popen.procs), 1)
        self.assertFalse(popen.procs[0].stdin.closed)
        backend.release()
        proc = popen.procs[0]
        self.assertTrue(proc.stdin.closed)
        self.assertEqual(proc.returncode, 0)
        self.assertFalse(proc.terminated, "pipe close should be enough; no signals sent")

    def test_idempotent(self):
        backend, popen = self.make({"systemd-inhibit": "ready"})
        backend.release()
        backend.acquire()
        backend.acquire()
        backend.release()
        backend.release()
        self.assertEqual(len(popen.procs), 1)

    def test_gnome_holds_both_inhibitors(self):
        env = {"XDG_CURRENT_DESKTOP": "ubuntu:GNOME"}
        backend, popen = self.make({"systemd-inhibit": "ready", "gnome-session-inhibit": "ready"}, env=env)
        backend.acquire()
        self.assertEqual([p.argv[0] for p in popen.procs], ["systemd-inhibit", "gnome-session-inhibit"])
        backend.release()
        self.assertTrue(all(p.stdin.closed for p in popen.procs))

    def test_partial_failure_still_succeeds(self):
        env = {"XDG_CURRENT_DESKTOP": "GNOME"}
        backend, popen = self.make({"systemd-inhibit": "exit", "gnome-session-inhibit": "ready"}, env=env)
        backend.acquire()  # systemd-inhibit died, gnome one is fine
        self.assertEqual(len(popen.procs), 2)
        backend.release()

    def test_all_failures_raise_with_stderr_detail(self):
        backend, _ = self.make({"systemd-inhibit": "exit"})
        with self.assertRaises(BackendError) as ctx:
            backend.acquire()
        self.assertIn("systemd-inhibit", str(ctx.exception))
        self.assertIn("No such interface", str(ctx.exception))

    def test_nothing_installed_raises_helpful_error(self):
        backend, _ = self.make({}, available=[])
        with self.assertRaises(BackendError) as ctx:
            backend.acquire()
        self.assertIn("systemd", str(ctx.exception))

    def test_binary_vanished_between_which_and_popen(self):
        backend, _ = self.make({}, available=["systemd-inhibit"])
        with self.assertRaises(BackendError):
            backend.acquire()

    def test_ready_timeout_kills_hung_helper(self):
        backend, popen = self.make({"systemd-inhibit": "hang"}, timeout=0.05)
        with self.assertRaises(BackendError) as ctx:
            backend.acquire()
        self.assertIn("timed out", str(ctx.exception))
        proc = popen.procs[0]
        self.assertTrue(proc.stdin.closed)
        self.assertIsNotNone(proc.returncode)

    def test_release_escalates_to_terminate_then_kill(self):
        backend, popen = self.make({"systemd-inhibit": "ready"})
        backend.acquire()
        proc = popen.procs[0]
        proc.behaviour = "stuck"  # ignores EOF and SIGTERM
        backend.release()
        self.assertTrue(proc.terminated)
        self.assertTrue(proc.killed)
        self.assertEqual(proc.returncode, -9)


if __name__ == "__main__":
    unittest.main()


from unittest import mock  # noqa: E402

from wakeful.keep_awake.platform import linux  # noqa: E402
from wakeful.keep_awake.platform.linux import X11_HELP, X11CursorMover  # noqa: E402


class FakeXTest:
    def __init__(self, fail_open=False):
        self.displays = []
        self.motions = []
        self.closed = 0
        self.fail_open = fail_open

    def open_display(self, name):
        if self.fail_open:
            raise BackendError("cannot connect")
        self.displays.append(name)

    def fake_relative_motion(self, dx, dy):
        self.motions.append((dx, dy))

    def close_display(self):
        self.closed += 1


class X11CursorMoverTests(unittest.TestCase):
    def test_nudge_moves_one_pixel_and_back(self):
        api = FakeXTest()
        mover = X11CursorMover(api, environ={"DISPLAY": ":0"})
        mover.open()
        mover.nudge()
        mover.close()
        self.assertEqual(api.displays, [":0"])
        self.assertEqual(api.motions, [(1, 0), (-1, 0)])
        self.assertEqual(api.closed, 1)

    def test_open_is_idempotent_and_close_without_open_is_noop(self):
        api = FakeXTest()
        mover = X11CursorMover(api, environ={"DISPLAY": ":1"})
        mover.close()
        mover.open()
        mover.open()
        mover.close()
        mover.close()
        self.assertEqual(len(api.displays), 1)
        self.assertEqual(api.closed, 1)

    def test_no_display_is_a_clear_error(self):
        with self.assertRaises(BackendError) as ctx:
            X11CursorMover(FakeXTest(), environ={}).open()
        self.assertEqual(str(ctx.exception), X11_HELP)

    def test_connect_failure_propagates(self):
        with self.assertRaises(BackendError):
            X11CursorMover(FakeXTest(fail_open=True), environ={"DISPLAY": ":0"}).open()

    def test_missing_libxtst_is_a_backend_error(self):
        with mock.patch.object(linux, "XTestApi", side_effect=OSError("libXtst.so.6: cannot open")):
            with self.assertRaises(BackendError) as ctx:
                X11CursorMover(environ={"DISPLAY": ":0"}).open()
        self.assertIn("libxtst6", str(ctx.exception))
