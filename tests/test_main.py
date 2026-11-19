import io
import json
import unittest
from datetime import datetime
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch
from zoneinfo import ZoneInfo

from smart_monitor.config import load_config
from smart_monitor.main import EXIT_FAIL, EXIT_OK, EXIT_WARN, run_check


EXAMPLE = Path(__file__).resolve().parents[1] / "config.example.ini"
TZ = ZoneInfo("America/New_York")
THURSDAY = datetime(2026, 11, 19, 8, 0, tzinfo=TZ)
SUNDAY = datetime(2026, 11, 22, 8, 0, tzinfo=TZ)

HEALTHY = {
    "smartctl": {"exit_status": 0, "messages": []},
    "device": {"name": "/dev/sdb", "type": "sat", "protocol": "ATA"},
    "model_name": "TEST-DISK",
    "serial_number": "SN1",
    "smart_status": {"passed": True},
    "temperature": {"current": 35},
    "ata_smart_attributes": {
        "table": [
            {
                "id": 5,
                "name": "Reallocated_Sector_Ct",
                "when_failed": "",
                "flags": {"prefailure": True},
                "raw": {"value": 0, "string": "0"},
            }
        ]
    },
}

FAILING = json.loads(json.dumps(HEALTHY))
FAILING["smart_status"]["passed"] = False
FAILING["ata_smart_attributes"]["table"][0]["raw"]["value"] = 8
FAILING["smartctl"]["exit_status"] = 8

SCAN = {
    "devices": [
        {"name": "/dev/sdb", "info_name": "/dev/sdb [SAT]", "type": "sat", "protocol": "ATA"}
    ]
}


def runner_for(payloads):
    def runner(argv, timeout):
        if "--scan" in argv:
            return CompletedProcess(argv, 0, stdout=json.dumps(SCAN).encode(), stderr=b"")
        name = argv[-1]
        body = payloads[name]
        return CompletedProcess(argv, 0, stdout=json.dumps(body).encode(), stderr=b"")

    return runner


class RunCheckTests(unittest.TestCase):
    def setUp(self):
        self.cfg = load_config(EXAMPLE)
        self.cfg.ntfy_topic = "unit-test-topic"
        self.cfg.hostname = "testhost"
        self.sent = []

        def fake_notify(*args, **kwargs):
            self.sent.append((args, kwargs))

        self.fake_notify = fake_notify

    def _run(self, payloads, now, **kwargs):
        log = io.StringIO()
        with patch("smart_monitor.main.discover_smartctl", return_value="smartctl"):
            rc = run_check(
                self.cfg,
                now=now,
                notifier=self.fake_notify,
                log=log,
                runner=runner_for(payloads),
                **kwargs,
            )
        return rc, log.getvalue()

    def test_healthy_weekday_silent(self):
        rc, out = self._run({"/dev/sdb": HEALTHY}, THURSDAY)
        self.assertEqual(rc, EXIT_OK)
        self.assertEqual(self.sent, [])
        self.assertIn(" OK ", out)

    def test_healthy_sunday_heartbeat(self):
        rc, out = self._run({"/dev/sdb": HEALTHY}, SUNDAY)
        self.assertEqual(rc, EXIT_OK)
        self.assertEqual(len(self.sent), 1)
        title = self.sent[0][0][2]
        self.assertTrue(title.startswith("SMART OK"))
        self.assertIn("ntfy sent", out)

    def test_failure_alerts(self):
        rc, out = self._run({"/dev/sdb": FAILING}, THURSDAY)
        self.assertEqual(rc, EXIT_FAIL)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0][1]["priority"], 5)
        self.assertIn("FAILED", self.sent[0][0][3])

    def test_silence_suppresses_ntfy(self):
        self.cfg.silence_alerts = True
        rc, out = self._run({"/dev/sdb": FAILING}, SUNDAY)
        self.assertEqual(rc, EXIT_FAIL)
        self.assertEqual(self.sent, [])
        self.assertIn("silenced", out)

    def test_dry_run_failure_does_not_post(self):
        rc, out = self._run({"/dev/sdb": FAILING}, THURSDAY, dry_run=True)
        self.assertEqual(rc, EXIT_FAIL)
        self.assertEqual(self.sent, [])
        self.assertIn("DRY-RUN", out)

    def test_scan_only(self):
        rc, out = self._run({"/dev/sdb": HEALTHY}, THURSDAY, scan_only=True)
        self.assertEqual(rc, EXIT_OK)
        self.assertIn("/dev/sdb", out)
        self.assertEqual(self.sent, [])

    def test_missing_configured_drive(self):
        self.cfg.drives = ["/dev/nvme0n1"]
        rc, out = self._run({"/dev/sdb": HEALTHY}, THURSDAY)
        self.assertEqual(rc, EXIT_WARN)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("not present", self.sent[0][0][3])
