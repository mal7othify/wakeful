import unittest

from wakeful.keep_awake import BackendUnavailable, create_backend
from wakeful.keep_awake.platform.linux import LinuxBackend
from wakeful.keep_awake.platform.macos import MacOSBackend
from wakeful.keep_awake.platform.windows import WindowsBackend


class BackendSelectionTests(unittest.TestCase):
    def test_windows(self):
        self.assertIsInstance(create_backend(platform="win32"), WindowsBackend)

    def test_macos(self):
        self.assertIsInstance(create_backend(platform="darwin"), MacOSBackend)

    def test_linux(self):
        self.assertIsInstance(create_backend(platform="linux"), LinuxBackend)

    def test_unsupported_platform(self):
        with self.assertRaises(BackendUnavailable):
            create_backend(platform="freebsd14")

    def test_unknown_mode(self):
        with self.assertRaises(BackendUnavailable):
            create_backend(mode="laser", platform="linux")

    def test_construction_never_touches_native_apis(self):
        # Constructing every backend on any host must be side-effect free.
        for platform in ("win32", "darwin", "linux"):
            create_backend(platform=platform)


if __name__ == "__main__":
    unittest.main()


from wakeful.keep_awake.activity import CompositeBackend  # noqa: E402


class MouseModeSelectionTests(unittest.TestCase):
    def test_mouse_mode_is_native_plus_activity(self):
        for platform, native in (("win32", WindowsBackend), ("darwin", MacOSBackend), ("linux", LinuxBackend)):
            backend = create_backend(mode="mouse", platform=platform, interval_seconds=30)
            self.assertIsInstance(backend, CompositeBackend)
            self.assertEqual(backend.name, f"{native.name}+mouse-activity")
            self.assertEqual(backend._backends[1].interval_seconds, 30)

    def test_mouse_mode_unsupported_platform(self):
        with self.assertRaises(BackendUnavailable):
            create_backend(mode="mouse", platform="freebsd14")
