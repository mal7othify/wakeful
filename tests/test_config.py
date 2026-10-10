import json
import tempfile
import unittest
from pathlib import Path

from wakeful.config import KeepAwakeConfig, load_config, save_config


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "nested" / "config.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_defaults(self):
        cfg = KeepAwakeConfig()
        self.assertFalse(cfg.enabled)
        self.assertEqual(cfg.mode, "native")

    def test_missing_file_gives_defaults(self):
        self.assertEqual(load_config(self.path), KeepAwakeConfig())

    def test_corrupt_file_gives_defaults(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{not json")
        self.assertEqual(load_config(self.path), KeepAwakeConfig())

    def test_wrong_shape_gives_defaults(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps({"keep_awake": [1, 2]}))
        self.assertEqual(load_config(self.path), KeepAwakeConfig())

    def test_roundtrip_creates_parent_dirs(self):
        save_config(self.path, KeepAwakeConfig(enabled=True, mode="native"))
        self.assertEqual(load_config(self.path), KeepAwakeConfig(enabled=True))
        self.assertEqual(
            json.loads(self.path.read_text()),
            {"keep_awake": {"enabled": True, "mode": "native", "interval_seconds": 60}},
        )
        self.assertFalse(self.path.with_suffix(".json.tmp").exists())

    def test_partial_file_fills_defaults(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps({"keep_awake": {"enabled": 1}}))
        cfg = load_config(self.path)
        self.assertTrue(cfg.enabled)
        self.assertEqual(cfg.mode, "native")


if __name__ == "__main__":
    unittest.main()


class ConfigModeAndIntervalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "config.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_mouse_mode_and_interval_roundtrip(self):
        save_config(self.path, KeepAwakeConfig(enabled=True, mode="mouse", interval_seconds=45))
        cfg = load_config(self.path)
        self.assertEqual(cfg, KeepAwakeConfig(enabled=True, mode="mouse", interval_seconds=45.0))
        self.assertEqual(json.loads(self.path.read_text())["keep_awake"]["interval_seconds"], 45)

    def test_unknown_mode_and_bad_interval_fall_back(self):
        self.path.write_text(json.dumps({"keep_awake": {"mode": "laser", "interval_seconds": "soon"}}))
        cfg = load_config(self.path)
        self.assertEqual(cfg.mode, "native")
        self.assertEqual(cfg.interval_seconds, 60)

    def test_interval_below_one_second_is_rejected(self):
        self.path.write_text(json.dumps({"keep_awake": {"interval_seconds": 0}}))
        self.assertEqual(load_config(self.path).interval_seconds, 60)
