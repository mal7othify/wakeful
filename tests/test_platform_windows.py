import unittest
from unittest import mock

from wakeful.keep_awake.backend import BackendError
from wakeful.keep_awake.platform import windows
from wakeful.keep_awake.platform.windows import AWAKE_FLAGS, RESTORE_FLAGS, WindowsBackend


class RecordingApi:
    def __init__(self, fail: bool = False):
        self.calls: list[int] = []
        self.fail = fail

    def __call__(self, flags: int) -> int:
        self.calls.append(flags)
        return 0 if self.fail else 0x80000000


class WindowsBackendTests(unittest.TestCase):
    def test_acquire_requests_system_and_display(self):
        api = RecordingApi()
        backend = WindowsBackend(api)
        backend.acquire()
        self.assertEqual(api.calls, [AWAKE_FLAGS])
        self.assertTrue(AWAKE_FLAGS & windows.ES_DISPLAY_REQUIRED)
        self.assertTrue(AWAKE_FLAGS & windows.ES_SYSTEM_REQUIRED)
        self.assertTrue(AWAKE_FLAGS & windows.ES_CONTINUOUS)

    def test_release_restores_continuous_only(self):
        api = RecordingApi()
        backend = WindowsBackend(api)
        backend.acquire()
        backend.release()
        self.assertEqual(api.calls, [AWAKE_FLAGS, RESTORE_FLAGS])

    def test_idempotent(self):
        api = RecordingApi()
        backend = WindowsBackend(api)
        backend.release()
        backend.acquire()
        backend.acquire()
        backend.release()
        backend.release()
        self.assertEqual(api.calls, [AWAKE_FLAGS, RESTORE_FLAGS])

    def test_api_failure_raises_and_is_not_held(self):
        api = RecordingApi(fail=True)
        backend = WindowsBackend(api)
        with self.assertRaises(BackendError):
            backend.acquire()
        backend.release()  # nothing held, so no second call
        self.assertEqual(api.calls, [AWAKE_FLAGS])

    def test_missing_kernel32_is_a_backend_error(self):
        with mock.patch.object(windows, "load_set_thread_execution_state", side_effect=OSError("no kernel32")):
            backend = WindowsBackend()
            with self.assertRaises(BackendError) as ctx:
                backend.acquire()
        self.assertIn("no kernel32", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()


from wakeful.keep_awake.platform.windows import INPUT, MOUSEEVENTF_MOVE, WindowsCursorMover  # noqa: E402


class RecordingSendInput:
    def __init__(self, inject=None):
        self.batches = []
        self.inject = inject

    def __call__(self, count, batch, size):
        self.batches.append([(batch[i].mi.dx, batch[i].mi.dy, batch[i].mi.dwFlags) for i in range(count)])
        assert size == __import__("ctypes").sizeof(INPUT)
        return count if self.inject is None else self.inject


class WindowsCursorMoverTests(unittest.TestCase):
    def test_nudge_moves_one_pixel_and_back_in_one_batch(self):
        send = RecordingSendInput()
        mover = WindowsCursorMover(send)
        mover.open()
        mover.nudge()
        mover.close()
        self.assertEqual(send.batches, [[(1, 0, MOUSEEVENTF_MOVE), (-1, 0, MOUSEEVENTF_MOVE)]])

    def test_partial_injection_is_an_error(self):
        mover = WindowsCursorMover(RecordingSendInput(inject=1))
        mover.open()
        with self.assertRaises(BackendError):
            mover.nudge()

    def test_missing_user32_is_a_backend_error(self):
        with mock.patch.object(windows, "load_send_input", side_effect=OSError("no user32")):
            with self.assertRaises(BackendError):
                WindowsCursorMover().open()

    def test_input_struct_matches_win64_layout(self):
        import ctypes

        self.assertEqual(ctypes.sizeof(INPUT), 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)
        self.assertEqual(ctypes.sizeof(windows.MOUSEINPUT), 32 if ctypes.sizeof(ctypes.c_void_p) == 8 else 24)
