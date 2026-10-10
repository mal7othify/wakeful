import threading
import time
import unittest

from fakes import FakeBackend

from wakeful.keep_awake.activity import CompositeBackend, MouseActivityBackend
from wakeful.keep_awake.backend import BackendError


class FakeMover:
    def __init__(self, fail_open=False, fail_nudge_after=None):
        self.opened = 0
        self.closed = 0
        self.nudges = 0
        self.fail_open = fail_open
        self.fail_nudge_after = fail_nudge_after
        self.thread_ids: set[int] = set()

    def open(self):
        self.opened += 1
        if self.fail_open:
            raise BackendError("no permission")

    def nudge(self):
        self.nudges += 1
        self.thread_ids.add(threading.get_ident())
        if self.fail_nudge_after is not None and self.nudges > self.fail_nudge_after:
            raise BackendError("injection failed")

    def close(self):
        self.closed += 1


def wait_until(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


class MouseActivityBackendTests(unittest.TestCase):
    def test_acquire_opens_and_nudges_immediately(self):
        mover = FakeMover()
        backend = MouseActivityBackend(mover, interval_seconds=60)
        backend.acquire()
        try:
            self.assertEqual(mover.opened, 1)
            self.assertEqual(mover.nudges, 1)
        finally:
            backend.release()

    def test_periodic_nudges_on_background_thread(self):
        mover = FakeMover()
        backend = MouseActivityBackend(mover, interval_seconds=0.01)
        self.assertEqual(backend.interval_seconds, 1.0, "interval is clamped to the safe minimum")
        backend._interval = 0.01  # test-only: make the loop fast
        backend.acquire()
        try:
            self.assertTrue(wait_until(lambda: mover.nudges >= 4))
            self.assertIn(threading.get_ident(), mover.thread_ids)  # first nudge is synchronous
            self.assertEqual(len(mover.thread_ids), 2)  # the rest come from the worker
        finally:
            backend.release()

    def test_release_stops_immediately_and_closes(self):
        mover = FakeMover()
        backend = MouseActivityBackend(mover)
        backend._interval = 0.01
        backend.acquire()
        self.assertTrue(wait_until(lambda: mover.nudges >= 2))
        started = time.monotonic()
        backend.release()
        self.assertLess(time.monotonic() - started, 1.0)
        count = mover.nudges
        time.sleep(0.05)
        self.assertEqual(mover.nudges, count, "no nudges after release")
        self.assertEqual(mover.closed, 1)
        self.assertFalse(any(t.name == "wakeful-activity" for t in threading.enumerate()))

    def test_open_failure_raises_and_starts_nothing(self):
        mover = FakeMover(fail_open=True)
        backend = MouseActivityBackend(mover)
        with self.assertRaises(BackendError):
            backend.acquire()
        self.assertEqual(mover.nudges, 0)
        backend.release()
        self.assertEqual(mover.closed, 0)

    def test_first_nudge_failure_raises_and_closes(self):
        mover = FakeMover(fail_nudge_after=0)
        backend = MouseActivityBackend(mover)
        with self.assertRaises(BackendError):
            backend.acquire()
        self.assertEqual(mover.closed, 1)
        backend.release()
        self.assertEqual(mover.closed, 1)

    def test_nudge_failure_in_loop_keeps_running(self):
        mover = FakeMover(fail_nudge_after=2)
        backend = MouseActivityBackend(mover)
        backend._interval = 0.01
        backend.acquire()
        try:
            self.assertTrue(wait_until(lambda: mover.nudges >= 6))
        finally:
            backend.release()

    def test_idempotent(self):
        mover = FakeMover()
        backend = MouseActivityBackend(mover)
        backend.release()
        backend.acquire()
        backend.acquire()
        backend.release()
        backend.release()
        self.assertEqual(mover.opened, 1)
        self.assertEqual(mover.closed, 1)


class CompositeBackendTests(unittest.TestCase):
    def test_acquires_in_order_and_releases_in_reverse(self):
        order = []
        a, b = FakeBackend(), FakeBackend()
        a.acquire, b.acquire = lambda: order.append("a+"), lambda: order.append("b+")
        a.release, b.release = lambda: order.append("a-"), lambda: order.append("b-")
        comp = CompositeBackend([a, b])
        self.assertEqual(comp.name, "fake+fake")
        comp.acquire()
        comp.release()
        self.assertEqual(order, ["a+", "b+", "b-", "a-"])

    def test_failure_rolls_back_earlier_backends(self):
        a, b = FakeBackend(), FakeBackend(fail_acquire=True)
        comp = CompositeBackend([a, b])
        with self.assertRaises(BackendError):
            comp.acquire()
        self.assertEqual(a.release_calls, 1)
        self.assertFalse(a.held)

    def test_release_continues_past_failures_then_raises(self):
        a, b = FakeBackend(fail_release=True), FakeBackend()
        comp = CompositeBackend([a, b])
        comp.acquire()
        with self.assertRaises(BackendError):
            comp.release()
        self.assertEqual(b.release_calls, 1)


if __name__ == "__main__":
    unittest.main()
