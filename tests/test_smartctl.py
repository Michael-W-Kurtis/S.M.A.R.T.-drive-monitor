import json
import unittest
from subprocess import CompletedProcess

from smart_monitor.smartctl import ScanDevice, is_standby_message, query_drive, scan_devices


SCAN = {
    "devices": [
        {"name": "/dev/sda", "info_name": "/dev/sda", "type": "scsi", "protocol": "SCSI"},
        {"name": "/dev/sdb", "info_name": "/dev/sdb [SAT]", "type": "sat", "protocol": "ATA"},
        {"name": "/dev/sr0", "info_name": "/dev/sr0 [cdrom]", "type": "scsi", "protocol": "SCSI"},
    ]
}

STANDBY = {
    "smartctl": {
        "exit_status": 2,
        "messages": [{"string": "Device is in STANDBY mode, exit 2", "severity": "error"}],
    }
}


class ScanTests(unittest.TestCase):
    def test_drops_optical(self):
        def runner(argv, timeout):
            return CompletedProcess(argv, 0, stdout=json.dumps(SCAN).encode(), stderr=b"")

        devices = scan_devices("smartctl", 10, runner=runner)
        names = [d.name for d in devices]
        self.assertEqual(names, ["/dev/sda", "/dev/sdb"])


class StandbyTests(unittest.TestCase):
    def test_standby_message(self):
        self.assertTrue(is_standby_message(["Device is in STANDBY mode, exit 2"], {}))
        self.assertFalse(is_standby_message(["Permission denied"], {}))

    def test_query_marks_standby(self):
        def runner(argv, timeout):
            self.assertIn("-n", argv)
            return CompletedProcess(argv, 2, stdout=json.dumps(STANDBY).encode(), stderr=b"")

        report = query_drive(
            "smartctl",
            ScanDevice("/dev/sdb", "sat", "ATA", "/dev/sdb"),
            10,
            True,
            runner=runner,
        )
        self.assertTrue(report.standby)
        self.assertFalse(report.open_failed)
