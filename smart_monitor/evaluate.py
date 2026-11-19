"""Decide which SMART facts are worth an ntfy ping.

Noisy vendor-specific counters (Seagate Raw_Read_Error_Rate / Seek_Error_Rate,
Power_On_Hours, load cycle) are ignored. We alert on confirmed media failure,
pre-fail attributes, NVMe critical warnings, and a handful of raw counts that
historically precede a dead disk.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from smart_monitor.config import Thresholds
from smart_monitor.smartctl import DriveReport

SEVERITY_FAIL = "fail"
SEVERITY_WARN = "warn"
SEVERITY_INFO = "info"

# ATA attribute IDs that actually correlate with dying media / interface.
# value in mapping: (label, threshold_field, severity)
ATA_COUNT_ATTRS: Dict[int, Tuple[str, str, str]] = {
    5: ("Reallocated sectors", "realloc_warn", SEVERITY_FAIL),
    10: ("Spin retries", "spin_retry_warn", SEVERITY_WARN),
    184: ("End-to-end errors", "end_to_end_warn", SEVERITY_FAIL),
    187: ("Reported uncorrectable", "reported_uncorrect_warn", SEVERITY_FAIL),
    196: ("Reallocation events", "realloc_warn", SEVERITY_WARN),
    197: ("Pending sectors", "pending_warn", SEVERITY_FAIL),
    198: ("Offline uncorrectable", "uncorrectable_warn", SEVERITY_FAIL),
    199: ("UDMA CRC errors (cable/interface)", "crc_warn", SEVERITY_WARN),
}


@dataclass(frozen=True)
class Finding:
    severity: str
    device: str
    summary: str


def _label(report: DriveReport) -> str:
    bits = [report.name]
    if report.model:
        bits.append(report.model)
    if report.serial:
        bits.append(f"SN {report.serial}")
    return " ".join(bits)


def _raw_int(attr: Dict[str, Any]) -> int:
    raw = attr.get("raw")
    if isinstance(raw, dict):
        val = raw.get("value", 0)
        try:
            return int(val)
        except (TypeError, ValueError):
            return 0
    return 0


def _when_failed(attr: Dict[str, Any]) -> str:
    return str(attr.get("when_failed") or "").strip()


def _celsius(report: DriveReport) -> Optional[int]:
    payload = report.payload
    temp = payload.get("temperature")
    if isinstance(temp, dict) and temp.get("current") is not None:
        try:
            return int(temp["current"])
        except (TypeError, ValueError):
            pass
    nvme = payload.get("nvme_smart_health_information_log")
    if isinstance(nvme, dict) and nvme.get("temperature") is not None:
        try:
            kelvin = int(nvme["temperature"])
            if kelvin > 200:  # NVMe SMART log is Kelvin
                return kelvin - 273
            return kelvin
        except (TypeError, ValueError):
            pass
    return None


def _smart_passed(payload: Dict[str, Any]) -> Optional[bool]:
    status = payload.get("smart_status")
    if isinstance(status, dict) and "passed" in status:
        return bool(status["passed"])
    return None


def evaluate_drive(report: DriveReport, thresholds: Thresholds) -> List[Finding]:
    findings: List[Finding] = []
    label = _label(report)

    if report.standby:
        return [Finding(SEVERITY_INFO, report.name, f"{label}: skipped (standby/sleep)")]

    if report.open_failed:
        extra = report.messages[0] if report.messages else "device open failed"
        findings.append(
            Finding(
                SEVERITY_WARN,
                report.name,
                f"{label}: cannot read SMART ({extra}). "
                "Run as Administrator / root if this disk should be monitored.",
            )
        )
        return findings

    passed = _smart_passed(report.payload)
    if passed is False or (report.exit_status & 0x08):
        findings.append(Finding(SEVERITY_FAIL, report.name, f"{label}: SMART overall status FAILED"))
    if report.exit_status & 0x10:
        findings.append(
            Finding(SEVERITY_FAIL, report.name, f"{label}: pre-fail attribute below threshold")
        )

    temp_c = _celsius(report)
    if temp_c is not None:
        if temp_c >= thresholds.temp_fail_c:
            findings.append(
                Finding(
                    SEVERITY_FAIL,
                    report.name,
                    f"{label}: temperature {temp_c}C (fail >={thresholds.temp_fail_c}C)",
                )
            )
        elif temp_c >= thresholds.temp_warn_c:
            findings.append(
                Finding(
                    SEVERITY_WARN,
                    report.name,
                    f"{label}: temperature {temp_c}C (warn >={thresholds.temp_warn_c}C)",
                )
            )

    protocol = (report.protocol or "").upper()
    if protocol == "NVME" or isinstance(report.payload.get("nvme_smart_health_information_log"), dict):
        findings.extend(_evaluate_nvme(report, thresholds, label))
    else:
        findings.extend(_evaluate_ata(report, thresholds, label))
        findings.extend(_evaluate_scsi(report, thresholds, label))

    if report.exit_status & 0x40:
        if not any("error log" in f.summary.lower() for f in findings):
            findings.append(
                Finding(SEVERITY_WARN, report.name, f"{label}: SMART error log contains errors")
            )
    if report.exit_status & 0x80:
        if not any("self-test" in f.summary.lower() for f in findings):
            findings.append(
                Finding(SEVERITY_FAIL, report.name, f"{label}: SMART self-test log contains errors")
            )

    return _dedupe(findings)


def _evaluate_ata(report: DriveReport, thresholds: Thresholds, label: str) -> List[Finding]:
    findings: List[Finding] = []
    table = (
        (report.payload.get("ata_smart_attributes") or {}).get("table")
        if isinstance(report.payload.get("ata_smart_attributes"), dict)
        else None
    )
    if not isinstance(table, list):
        return findings
    for attr in table:
        if not isinstance(attr, dict):
            continue
        raw_id = attr.get("id")
        if raw_id is None:
            continue
        try:
            attr_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        name = str(attr.get("name") or f"ID {attr_id}")
        flags = attr.get("flags") if isinstance(attr.get("flags"), dict) else {}
        when = _when_failed(attr)
        if when == "FAILING_NOW":
            sev = SEVERITY_FAIL if flags.get("prefailure") else SEVERITY_WARN
            findings.append(
                Finding(sev, report.name, f"{label}: {name} is FAILING NOW")
            )
        spec = ATA_COUNT_ATTRS.get(attr_id)
        if not spec:
            continue
        pretty, field_name, severity = spec
        limit = int(getattr(thresholds, field_name))
        raw = _raw_int(attr)
        if raw >= limit:
            findings.append(
                Finding(
                    severity,
                    report.name,
                    f"{label}: {pretty} raw={raw} (alert >={limit})",
                )
            )
    err = report.payload.get("ata_smart_error_log")
    if isinstance(err, dict):
        summary = err.get("summary") if isinstance(err.get("summary"), dict) else {}
        count = summary.get("count")
        try:
            count_i = int(count)
        except (TypeError, ValueError):
            count_i = 0
        if count_i > 0:
            findings.append(
                Finding(
                    SEVERITY_WARN,
                    report.name,
                    f"{label}: ATA error log count={count_i}",
                )
            )
    st = report.payload.get("ata_smart_self_test_log")
    if isinstance(st, dict):
        standard = st.get("standard") if isinstance(st.get("standard"), dict) else {}
        err_total = standard.get("error_count_total", 0)
        try:
            err_i = int(err_total)
        except (TypeError, ValueError):
            err_i = 0
        if err_i > 0:
            findings.append(
                Finding(
                    SEVERITY_FAIL,
                    report.name,
                    f"{label}: self-test errors={err_i}",
                )
            )
    return findings


def _evaluate_nvme(report: DriveReport, thresholds: Thresholds, label: str) -> List[Finding]:
    findings: List[Finding] = []
    log = report.payload.get("nvme_smart_health_information_log")
    if not isinstance(log, dict):
        return findings
    crit = int(log.get("critical_warning") or 0)
    if crit:
        bits = []
        if crit & 0x1:
            bits.append("available spare below threshold")
        if crit & 0x2:
            bits.append("temperature")
        if crit & 0x4:
            bits.append("reliability degraded")
        if crit & 0x8:
            bits.append("media read-only")
        if crit & 0x10:
            bits.append("volatile memory backup failed")
        extra = ", ".join(bits) if bits else hex(crit)
        findings.append(
            Finding(SEVERITY_FAIL, report.name, f"{label}: NVMe critical_warning={crit} ({extra})")
        )
    used = int(log.get("percentage_used") or 0)
    if used >= thresholds.nvme_percent_used_fail:
        findings.append(
            Finding(SEVERITY_FAIL, report.name, f"{label}: NVMe percentage used {used}%")
        )
    elif used >= thresholds.nvme_percent_used_warn:
        findings.append(
            Finding(SEVERITY_WARN, report.name, f"{label}: NVMe percentage used {used}%")
        )
    spare = log.get("available_spare")
    spare_th = log.get("available_spare_threshold")
    if spare is not None:
        spare_i = int(spare)
        floor = thresholds.nvme_available_spare_min_pct
        if spare_th is not None:
            floor = max(floor, int(spare_th))
        if spare_i < floor:
            findings.append(
                Finding(
                    SEVERITY_FAIL,
                    report.name,
                    f"{label}: NVMe available spare {spare_i}% (min {floor}%)",
                )
            )
    media = int(log.get("media_errors") or 0)
    if media >= thresholds.nvme_media_errors_warn:
        findings.append(
            Finding(SEVERITY_FAIL, report.name, f"{label}: NVMe media_errors={media}")
        )
    return findings


def _evaluate_scsi(report: DriveReport, thresholds: Thresholds, label: str) -> List[Finding]:
    findings: List[Finding] = []
    grown = report.payload.get("scsi_grown_defect_list")
    if grown is not None:
        try:
            grown_i = int(grown)
        except (TypeError, ValueError):
            grown_i = 0
        if grown_i >= thresholds.realloc_warn:
            findings.append(
                Finding(
                    SEVERITY_FAIL,
                    report.name,
                    f"{label}: SCSI grown defect list={grown_i}",
                )
            )
    return findings


def _dedupe(findings: Sequence[Finding]) -> List[Finding]:
    seen = set()
    out: List[Finding] = []
    for item in findings:
        key = (item.severity, item.device, item.summary)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def worst_severity(findings: Iterable[Finding]) -> Optional[str]:
    order = {SEVERITY_FAIL: 2, SEVERITY_WARN: 1, SEVERITY_INFO: 0}
    worst = None
    worst_n = -1
    for item in findings:
        n = order.get(item.severity, 0)
        if n > worst_n:
            worst_n = n
            worst = item.severity
    return worst


def actionable(findings: Iterable[Finding]) -> List[Finding]:
    return [f for f in findings if f.severity in (SEVERITY_FAIL, SEVERITY_WARN)]
