"""Test doubles shared by the unit tests. No native API or process is ever touched."""

from __future__ import annotations

from wakeful.keep_awake.backend import BackendError


class FakeBackend:
    name = "fake"

    def __init__(self, fail_acquire: bool = False, fail_release: bool = False, raise_plain: bool = False) -> None:
        self.acquire_calls = 0
        self.release_calls = 0
        self.held = False
        self.fail_acquire = fail_acquire
        self.fail_release = fail_release
        self.raise_plain = raise_plain

    def acquire(self) -> None:
        self.acquire_calls += 1
        if self.fail_acquire:
            self.held = True  # simulate a half-acquired resource
            if self.raise_plain:
                raise RuntimeError("boom")
            raise BackendError("native API said no")
        self.held = True

    def release(self) -> None:
        self.release_calls += 1
        if self.fail_release:
            raise BackendError("release failed")
        self.held = False
