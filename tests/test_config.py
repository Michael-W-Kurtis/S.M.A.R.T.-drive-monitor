import tempfile
import unittest
from pathlib import Path

from smart_monitor.config import ConfigError, load_config, parse_drives


EXAMPLE = Path(__file__).resolve().parents[1] / "config.example.ini"


class ParseDrivesTests(unittest.TestCase):
    def test_empty_means_all(self):
        self.assertEqual(parse_drives(""), [])
        self.assertEqual(parse_drives("  "), [])

    def test_comma_list(self):
        self.assertEqual(parse_drives("/dev/sda, /dev/sdb"), ["/dev/sda", "/dev/sdb"])


class LoadConfigTests(unittest.TestCase):
    def test_example_loads(self):
        cfg = load_config(EXAMPLE)
        self.assertTrue(cfg.ntfy_topic.startswith("CHANGE-ME"))
        self.assertEqual(cfg.weekly_ok_weekday, 6)
        self.assertEqual(cfg.thresholds.temp_fail_c, 60)
        self.assertEqual(cfg.drives, [])
        self.assertFalse(cfg.silence_alerts)

    def test_missing_file(self):
        with self.assertRaises(ConfigError):
            load_config(Path("/no/such/config.ini"))

    def test_bad_weekday(self):
        text = EXAMPLE.read_text(encoding="utf-8").replace(
            "weekly_ok_weekday = 6", "weekly_ok_weekday = 9"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.ini"
            path.write_text(text, encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_config(path)

    def test_silence_and_drives(self):
        text = EXAMPLE.read_text(encoding="utf-8")
        text = text.replace("silence_alerts = false", "silence_alerts = true")
        text = text.replace("drives =", "drives = /dev/sda, PD0")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.ini"
            path.write_text(text, encoding="utf-8")
            cfg = load_config(path)
            self.assertTrue(cfg.silence_alerts)
            self.assertEqual(cfg.drives, ["/dev/sda", "PD0"])


if __name__ == "__main__":
    unittest.main()
