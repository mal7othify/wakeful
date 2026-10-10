import unittest
from unittest import mock

from wakeful.keep_awake.backend import BackendError
from wakeful.keep_awake.platform import macos
from wakeful.keep_awake.platform.macos import ASSERTION_TYPE, MacOSBackend


class FakeIOKit:
    def __init__(self, fail_create: bool = False, fail_release: bool = False):
        self.created: list[tuple[str, str]] = []
        self.released: list[int] = []
        self.fail_create = fail_create
        self.fail_release = fail_release
        self._next_id = 41

    def create_assertion(self, assertion_type: str, reason: str) -> int:
        if self.fail_create:
            raise BackendError("IOPMAssertionCreateWithName failed")
        self._next_id += 1
        self.created.append((assertion_type, reason))
        return self._next_id

    def release_assertion(self, assertion_id: int) -> None:
        if self.fail_release:
            raise BackendError("IOPMAssertionRelease failed")
        self.released.append(assertion_id)


class MacOSBackendTests(unittest.TestCase):
    def test_acquire_creates_display_assertion(self):
        api = FakeIOKit()
        backend = MacOSBackend(api, reason="why")
        backend.acquire()
        self.assertEqual(api.created, [(ASSERTION_TYPE, "why")])
        self.assertEqual(ASSERTION_TYPE, "PreventUserIdleDisplaySleep")

    def test_release_releases_same_id(self):
        api = FakeIOKit()
        backend = MacOSBackend(api)
        backend.acquire()
        backend.release()
        self.assertEqual(api.released, [42])

    def test_idempotent(self):
        api = FakeIOKit()
        backend = MacOSBackend(api)
        backend.release()
        backend.acquire()
        backend.acquire()
        backend.release()
        backend.release()
        self.assertEqual(len(api.created), 1)
        self.assertEqual(len(api.released), 1)

    def test_create_failure(self):
        backend = MacOSBackend(FakeIOKit(fail_create=True))
        with self.assertRaises(BackendError):
            backend.acquire()
        backend.release()  # no-op, nothing held

    def test_release_failure_propagates_but_clears_state(self):
        api = FakeIOKit(fail_release=True)
        backend = MacOSBackend(api)
        backend.acquire()
        with self.assertRaises(BackendError):
            backend.release()
        backend.release()  # state cleared; second call is a no-op

    def test_missing_iokit_is_a_backend_error(self):
        with mock.patch.object(macos, "IOKitPowerApi", side_effect=OSError("dlopen failed")):
            backend = MacOSBackend()
            with self.assertRaises(BackendError) as ctx:
                backend.acquire()
        self.assertIn("dlopen failed", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()


from wakeful.keep_awake.platform.macos import ACCESSIBILITY_HELP, MacOSCursorMover  # noqa: E402


class FakeQuartz:
    def __init__(self, trusted=True, position=(100.0, 200.0)):
        self.trusted = trusted
        self.position = position
        self.prompted = []
        self.moves = []

    def is_trusted(self, prompt):
        self.prompted.append(prompt)
        return self.trusted

    def cursor_position(self):
        return self.position

    def post_move(self, x, y):
        self.moves.append((x, y))


class MacOSCursorMoverTests(unittest.TestCase):
    def test_nudge_moves_one_pixel_and_returns_to_origin(self):
        api = FakeQuartz()
        mover = MacOSCursorMover(api)
        mover.open()
        mover.nudge()
        self.assertEqual(api.moves, [(101.0, 200.0), (100.0, 200.0)])

    def test_open_prompts_for_accessibility_once_and_fails_when_untrusted(self):
        api = FakeQuartz(trusted=False)
        mover = MacOSCursorMover(api)
        with self.assertRaises(BackendError) as ctx:
            mover.open()
        self.assertEqual(str(ctx.exception), ACCESSIBILITY_HELP)
        with self.assertRaises(BackendError):
            mover.open()  # retry after the user visited System Settings: no second dialog
        self.assertEqual(api.prompted, [True, False])
        api.trusted = True
        mover.open()  # permission granted: works without restarting
        self.assertEqual(api.prompted, [True, False, False])

    def test_missing_quartz_is_a_backend_error(self):
        with mock.patch.object(macos, "QuartzApi", side_effect=OSError("dlopen failed")):
            with self.assertRaises(BackendError):
                MacOSCursorMover().open()
