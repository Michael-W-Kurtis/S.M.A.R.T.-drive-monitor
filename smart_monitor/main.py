"""One check, then exit. Scheduler (cron / Task Scheduler) owns the interval."""

from __future__ import annotations

import argparse
import datetime as dt
import socket
import sys
from pathlib import Path
from typing import Callable, List, Optional, Sequence, TextIO
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from smart_monitor.config import Config, ConfigError, default_config_path, load_config
from smart_monitor.evaluate import (
    SEVERITY_FAIL,
    Finding,
    actionable,
    evaluate_drive,
    worst_severity,
)
from smart_monitor.notify import NotifyError, send_ntfy
from smart_monitor.smartctl import (
    ScanDevice,
    SmartctlError,
    discover_smartctl,
    query_drive,
    scan_devices,
)

EXIT_OK = 0
EXIT_WARN = 1
EXIT_FAIL = 2
EXIT_ERROR = 3

Notifier = Callable[..., None]


def _now(timezone: str) -> dt.datetime:
    try:
        tz = ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ConfigError(
            f"unknown timezone {timezone!r}. On Windows: pip install tzdata"
        ) from exc
    return dt.datetime.now(tz)


def _hostname(cfg: Config) -> str:
    return cfg.hostname or socket.gethostname()


def _log(stream: TextIO, msg: str) -> None:
    stream.write(msg.rstrip() + "\n")
    stream.flush()


def select_devices(scanned: Sequence[ScanDevice], wanted: Sequence[str]) -> List[ScanDevice]:
    if not wanted:
        return list(scanned)
    by_name = {d.name: d for d in scanned}
    selected: List[ScanDevice] = []
    missing: List[str] = []
    for name in wanted:
        if name in by_name:
            selected.append(by_name[name])
        else:
            missing.append(name)
            selected.append(ScanDevice(name=name, dev_type="", protocol="", info_name=name))
    return selected


def format_alert(host: str, findings: Sequence[Finding]) -> tuple[str, str, int, str]:
    worst = worst_severity(actionable(findings)) or SEVERITY_FAIL
    if worst == SEVERITY_FAIL:
        title = f"SMART FAIL on {host}"
        priority = 5
        tags = "rotating_light,hard_disk"
    else:
        title = f"SMART warning on {host}"
        priority = 4
        tags = "warning,hard_disk"
    lines = [f.summary for f in findings if f.severity != "info"]
    body = "\n".join(lines) if lines else "unspecified SMART problem"
    return title, body, priority, tags


def format_weekly_ok(host: str, reports_n: int, skipped: int, names: Sequence[str]) -> tuple[str, str, int, str]:
    title = f"SMART OK on {host}"
    bits = [f"{reports_n} drive(s) checked"]
    if skipped:
        bits.append(f"{skipped} skipped (standby)")
    if names:
        bits.append(", ".join(names))
    body = "All systems good. " + ". ".join(bits) + "."
    return title, body, 2, "white_check_mark,hard_disk"


def run_check(
    cfg: Config,
    *,
    dry_run: bool = False,
    scan_only: bool = False,
    now: Optional[dt.datetime] = None,
    notifier: Optional[Notifier] = None,
    log: Optional[TextIO] = None,
    runner=None,
) -> int:
    out: TextIO = log if log is not None else sys.stdout
    notify = notifier or send_ntfy
    stamp = (now or _now(cfg.timezone)).strftime("%Y-%m-%dT%H:%M:%S%z")
    host = _hostname(cfg)

    try:
        smartctl_bin = discover_smartctl(cfg.smartctl_path)
        scanned = scan_devices(smartctl_bin, cfg.timeout_seconds, runner=runner)
    except SmartctlError as exc:
        _log(out, f"{stamp} ERROR {exc}")
        if not cfg.silence_alerts and not dry_run:
            try:
                notify(
                    cfg.ntfy_server,
                    cfg.ntfy_topic,
                    f"SMART monitor error on {host}",
                    str(exc),
                    priority=4,
                    tags="x,hard_disk",
                    token=cfg.ntfy_token,
                )
            except NotifyError as nexc:
                _log(out, f"{stamp} ERROR ntfy {nexc}")
                return EXIT_ERROR
        return EXIT_ERROR

    if scan_only:
        if not scanned:
            _log(out, f"{stamp} no devices from smartctl --scan")
            return EXIT_WARN
        for dev in scanned:
            _log(out, f"{dev.name}\ttype={dev.dev_type}\tprotocol={dev.protocol}\t{dev.info_name}")
        return EXIT_OK

    if not scanned and not cfg.drives:
        msg = "smartctl --scan returned no disks"
        _log(out, f"{stamp} WARN {msg}")
        findings = [Finding("warn", "*", msg)]
        return _emit(cfg, findings, host, stamp, dry_run, notify, out, weekly=False, ok_names=[], skipped=0, checked=0)

    devices = select_devices(scanned, cfg.drives)
    known = {d.name for d in scanned}
    findings: List[Finding] = []
    ok_names: List[str] = []
    skipped = 0
    for dev in devices:
        if cfg.drives and dev.name not in known:
            findings.append(
                Finding("warn", dev.name, f"{dev.name}: listed in config but not present in smartctl --scan")
            )
            continue
        try:
            report = query_drive(
                smartctl_bin,
                dev,
                cfg.timeout_seconds,
                cfg.skip_if_standby,
                runner=runner,
            )
        except SmartctlError as exc:
            findings.append(Finding("warn", dev.name, f"{dev.name}: {exc}"))
            continue
        drive_findings = evaluate_drive(report, cfg.thresholds)
        info_only = drive_findings and all(f.severity == "info" for f in drive_findings)
        if report.standby:
            skipped += 1
            findings.extend(drive_findings)
            continue
        action = actionable(drive_findings)
        if action:
            findings.extend(action)
        elif info_only:
            findings.extend(drive_findings)
        else:
            ok_names.append(dev.name)

    when = now or _now(cfg.timezone)
    weekly = when.weekday() == cfg.weekly_ok_weekday
    return _emit(
        cfg,
        findings,
        host,
        stamp,
        dry_run,
        notify,
        out,
        weekly=weekly,
        ok_names=ok_names,
        skipped=skipped,
        checked=len(devices),
    )


def _emit(
    cfg: Config,
    findings: Sequence[Finding],
    host: str,
    stamp: str,
    dry_run: bool,
    notify: Notifier,
    log: TextIO,
    *,
    weekly: bool,
    ok_names: Sequence[str],
    skipped: int,
    checked: int,
) -> int:
    for item in findings:
        _log(log, f"{stamp} {item.severity.upper()} {item.summary}")

    problems = actionable(findings)
    if not problems:
        _log(log, f"{stamp} OK {checked} drive(s), {len(ok_names)} healthy, {skipped} standby")

    if cfg.silence_alerts:
        _log(log, f"{stamp} silenced; no ntfy")
        if problems:
            return EXIT_FAIL if worst_severity(problems) == SEVERITY_FAIL else EXIT_WARN
        return EXIT_OK

    send_problems = bool(problems)
    send_weekly = (not problems) and weekly
    if not send_problems and not send_weekly:
        return EXIT_OK

    if send_problems:
        title, body, priority, tags = format_alert(host, problems)
    else:
        title, body, priority, tags = format_weekly_ok(host, checked, skipped, ok_names)

    if dry_run:
        _log(log, f"{stamp} DRY-RUN ntfy title={title!r} priority={priority}\n{body}")
        if problems:
            return EXIT_FAIL if worst_severity(problems) == SEVERITY_FAIL else EXIT_WARN
        return EXIT_OK

    if not cfg.ntfy_topic or cfg.ntfy_topic.startswith("CHANGE-ME"):
        _log(log, f"{stamp} ERROR ntfy.topic is unset; copy config.example.ini to config.ini")
        return EXIT_ERROR

    try:
        notify(
            cfg.ntfy_server,
            cfg.ntfy_topic,
            title,
            body,
            priority=priority,
            tags=tags,
            token=cfg.ntfy_token,
        )
    except NotifyError as exc:
        _log(log, f"{stamp} ERROR ntfy {exc}")
        return EXIT_ERROR
    _log(log, f"{stamp} ntfy sent title={title!r}")
    if problems:
        return EXIT_FAIL if worst_severity(problems) == SEVERITY_FAIL else EXIT_WARN
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="One-shot SMART check via smartctl; ntfy on failure/warning or Sunday OK.",
    )
    p.add_argument("--config", dest="config", default="", help="Path to config.ini")
    p.add_argument("--dry-run", action="store_true", help="Check and print; do not call ntfy")
    p.add_argument("--scan", action="store_true", help="Print smartctl --scan results and exit")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    path = Path(args.config) if args.config else default_config_path()
    try:
        cfg = load_config(path)
    except ConfigError as exc:
        sys.stderr.write(f"ERROR: {exc}\n")
        return EXIT_ERROR
    try:
        return run_check(cfg, dry_run=args.dry_run, scan_only=args.scan)
    except ConfigError as exc:
        sys.stderr.write(f"ERROR: {exc}\n")
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
