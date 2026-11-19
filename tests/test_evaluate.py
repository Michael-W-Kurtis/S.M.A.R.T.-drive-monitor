import unittest

from smart_monitor.config import Thresholds
from smart_monitor.evaluate import SEVERITY_FAIL, SEVERITY_WARN, actionable, evaluate_drive
from smart_monitor.smartctl import DriveReport


TH = Thresholds()


def report(name="/dev/sdb", protocol="ATA", exit_status=0, payload=None, **kwargs) -> DriveReport:
    return DriveReport(
        name=name,
        dev_type="sat",
        protocol=protocol,
        model="TEST-DISK",
        serial="SN1",
        exit_status=exit_status,
        payload=payload or {},
        **kwargs,
    )


def ata_attr(attr_id, name, raw, when_failed="", prefail=True):
    return {
        "id": attr_id,
        "name": name,
        "when_failed": when_failed,
        "flags": {"prefailure": prefail},
        "raw": {"value": raw, "string": str(raw)},
    }


class EvaluateAtaTests(unittest.TestCase):
    def test_healthy_is_quiet(self):
        payload = {
            "smart_status": {"passed": True},
            "temperature": {"current": 34},
            "ata_smart_attributes": {
                "table": [
                    ata_attr(5, "Reallocated_Sector_Ct", 0),
                    ata_attr(197, "Current_Pending_Sector", 0),
                    ata_attr(199, "UDMA_CRC_Error_Count", 0),
                    ata_attr(1, "Raw_Read_Error_Rate", 999999),
                ]
            },
        }
        self.assertEqual(actionable(evaluate_drive(report(payload=payload), TH)), [])

    def test_pending_is_fail(self):
        payload = {
            "smart_status": {"passed": True},
            "ata_smart_attributes": {"table": [ata_attr(197, "Current_Pending_Sector", 3)]},
        }
        findings = evaluate_drive(report(payload=payload), TH)
        self.assertTrue(any(f.severity == SEVERITY_FAIL and "Pending" in f.summary for f in findings))

    def test_crc_is_warn_not_fail(self):
        payload = {
            "smart_status": {"passed": True},
            "ata_smart_attributes": {"table": [ata_attr(199, "UDMA_CRC_Error_Count", 25)]},
        }
        findings = evaluate_drive(report(payload=payload), TH)
        self.assertEqual({f.severity for f in actionable(findings)}, {SEVERITY_WARN})

    def test_overall_failed(self):
        payload = {"smart_status": {"passed": False}}
        findings = evaluate_drive(report(payload=payload, exit_status=8), TH)
        self.assertTrue(any(f.severity == SEVERITY_FAIL for f in findings))

    def test_hot_disk(self):
        payload = {"smart_status": {"passed": True}, "temperature": {"current": 61}}
        findings = evaluate_drive(report(payload=payload), TH)
        self.assertTrue(any("temperature 61C" in f.summary and f.severity == SEVERITY_FAIL for f in findings))

    def test_standby_is_info(self):
        findings = evaluate_drive(report(payload={}, standby=True), TH)
        self.assertEqual([f.severity for f in findings], ["info"])
        self.assertEqual(actionable(findings), [])

    def test_open_failed_is_warn(self):
        findings = evaluate_drive(
            report(payload={}, open_failed=True, messages=["Permission denied"]),
            TH,
        )
        self.assertEqual([f.severity for f in findings], [SEVERITY_WARN])


class EvaluateNvmeTests(unittest.TestCase):
    def test_media_errors(self):
        payload = {
            "smart_status": {"passed": True},
            "nvme_smart_health_information_log": {
                "critical_warning": 0,
                "temperature": 308,
                "available_spare": 100,
                "available_spare_threshold": 10,
                "percentage_used": 5,
                "media_errors": 2,
            },
        }
        findings = evaluate_drive(report(name="/dev/nvme0", protocol="NVMe", payload=payload), TH)
        self.assertTrue(any("media_errors=2" in f.summary and f.severity == SEVERITY_FAIL for f in findings))

    def test_wear_warn(self):
        payload = {
            "smart_status": {"passed": True},
            "nvme_smart_health_information_log": {
                "critical_warning": 0,
                "temperature": 300,
                "available_spare": 100,
                "percentage_used": 92,
                "media_errors": 0,
            },
        }
        findings = evaluate_drive(report(name="/dev/nvme0", protocol="NVMe", payload=payload), TH)
        self.assertEqual({f.severity for f in actionable(findings)}, {SEVERITY_WARN})

    def test_critical_warning(self):
        payload = {
            "smart_status": {"passed": False},
            "nvme_smart_health_information_log": {
                "critical_warning": 0x9,
                "temperature": 300,
                "available_spare": 5,
                "available_spare_threshold": 10,
                "percentage_used": 40,
                "media_errors": 0,
            },
        }
        findings = evaluate_drive(report(name="/dev/nvme0", protocol="NVMe", payload=payload), TH)
        self.assertTrue(any("critical_warning" in f.summary for f in findings))
        self.assertTrue(any("available spare" in f.summary for f in findings))
