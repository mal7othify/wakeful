import gc
import unittest

from fakes import FakeBackend

from wakeful.keep_awake import BackendError, BackendUnavailable, KeepAwakeService


class ServiceStateTests(unittest.TestCase):
    def test_initial_state_is_inactive(self):
        svc = KeepAwakeService(FakeBackend())
        self.assertFalse(svc.is_active())
        self.assertTrue(svc.available)
        self.assertEqual(svc.backend_name, "fake")

    def test_start_activates(self):
        backend = FakeBackend()
        svc = KeepAwakeService(backend)
        svc.start()
        self.assertTrue(svc.is_active())
        self.assertTrue(backend.held)

    def test_stop_deactivates(self):
        backend = FakeBackend()
        svc = KeepAwakeService(backend)
        svc.start()
        svc.stop()
        self.assertFalse(svc.is_active())
        self.assertFalse(backend.held)

    def test_start_twice_is_idempotent(self):
        backend = FakeBackend()
        svc = KeepAwakeService(backend)
        svc.start()
        svc.start()
        self.assertEqual(backend.acquire_calls, 1)
        self.assertTrue(svc.is_active())

    def test_stop_twice_is_idempotent(self):
        backend = FakeBackend()
        svc = KeepAwakeService(backend)
        svc.stop()  # never started
        svc.start()
        svc.stop()
        svc.stop()
        self.assertEqual(backend.release_calls, 1)
        self.assertFalse(svc.is_active())

    def test_restart_after_stop(self):
        backend = FakeBackend()
        svc = KeepAwakeService(backend)
        svc.start()
        svc.stop()
        svc.start()
        self.assertTrue(svc.is_active())
        self.assertEqual(backend.acquire_calls, 2)


class ServiceCleanupTests(unittest.TestCase):
    def test_cleanup_when_destroyed(self):
        backend = FakeBackend()
        svc = KeepAwakeService(backend)
        svc.start()
        del svc
        gc.collect()
        self.assertFalse(backend.held)
        self.assertEqual(backend.release_calls, 1)

    def test_close_releases_and_is_idempotent(self):
        backend = FakeBackend()
        svc = KeepAwakeService(backend)
        svc.start()
        svc.close()
        svc.close()
        self.assertFalse(svc.is_active())
        self.assertEqual(backend.release_calls, 1)

    def test_close_without_start_does_not_touch_backend(self):
        backend = FakeBackend()
        svc = KeepAwakeService(backend)
        svc.close()
        self.assertEqual(backend.release_calls, 0)

    def test_context_manager_releases_on_exception(self):
        backend = FakeBackend()
        with self.assertRaises(ValueError):
            with KeepAwakeService(backend) as svc:
                svc.start()
                raise ValueError("ui exploded")
        self.assertFalse(backend.held)

    def test_close_swallows_release_errors(self):
        backend = FakeBackend(fail_release=True)
        svc = KeepAwakeService(backend)
        svc.start()
        svc.close()  # must not raise
        self.assertFalse(svc.is_active())


class ServiceFailureTests(unittest.TestCase):
    def test_backend_failure_leaves_service_inactive_and_cleans_up(self):
        backend = FakeBackend(fail_acquire=True)
        svc = KeepAwakeService(backend)
        with self.assertRaises(BackendError) as ctx:
            svc.start()
        self.assertIn("native API said no", str(ctx.exception))
        self.assertFalse(svc.is_active())
        self.assertEqual(backend.release_calls, 1, "partially acquired resources must be released")
        self.assertFalse(backend.held)

    def test_unexpected_exception_is_wrapped(self):
        backend = FakeBackend(fail_acquire=True, raise_plain=True)
        svc = KeepAwakeService(backend)
        with self.assertRaises(BackendError):
            svc.start()
        self.assertFalse(svc.is_active())
        self.assertEqual(backend.release_calls, 1)

    def test_can_retry_after_failure(self):
        backend = FakeBackend(fail_acquire=True)
        svc = KeepAwakeService(backend)
        with self.assertRaises(BackendError):
            svc.start()
        backend.fail_acquire = False
        svc.start()
        self.assertTrue(svc.is_active())

    def test_release_failure_on_stop_still_marks_inactive(self):
        backend = FakeBackend(fail_release=True)
        svc = KeepAwakeService(backend)
        svc.start()
        with self.assertRaises(BackendError):
            svc.stop()
        self.assertFalse(svc.is_active())

    def test_app_continues_without_backend(self):
        svc = KeepAwakeService(None)
        self.assertFalse(svc.available)
        self.assertEqual(svc.backend_name, "none")
        self.assertFalse(svc.is_active())
        with self.assertRaises(BackendUnavailable):
            svc.start()
        self.assertFalse(svc.is_active())
        svc.stop()  # safe
        svc.close()  # safe
        with svc:
            pass


if __name__ == "__main__":
    unittest.main()


class UnavailableReasonTests(unittest.TestCase):
    def test_reason_is_surfaced(self):
        svc = KeepAwakeService(None, unavailable_reason="mode 'laser' is not a thing")
        self.assertEqual(svc.unavailable_reason, "mode 'laser' is not a thing")
        with self.assertRaises(BackendUnavailable) as ctx:
            svc.start()
        self.assertIn("laser", str(ctx.exception))

    def test_reason_is_none_with_backend(self):
        self.assertIsNone(KeepAwakeService(FakeBackend()).unavailable_reason)
