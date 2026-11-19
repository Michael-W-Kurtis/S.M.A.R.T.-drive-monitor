"""Load tunables from an INI file. No secrets or site values in source."""

from __future__ import annotations

import configparser
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


class ConfigError(Exception):
    pass


@dataclass
class Thresholds:
    temp_warn_c: int = 50
    temp_fail_c: int = 60
    realloc_warn: int = 1
    pending_warn: int = 1
    uncorrectable_warn: int = 1
    reported_uncorrect_warn: int = 1
    spin_retry_warn: int = 1
    end_to_end_warn: int = 1
    crc_warn: int = 10
    nvme_percent_used_warn: int = 90
    nvme_percent_used_fail: int = 100
    nvme_media_errors_warn: int = 1
    nvme_available_spare_min_pct: int = 10


@dataclass
class Config:
    ntfy_server: str
    ntfy_topic: str
    ntfy_token: str
    drives: List[str]
    silence_alerts: bool
    timezone: str
    check_interval_hours: int
    weekly_ok_weekday: int
    hostname: str
    smartctl_path: str
    timeout_seconds: int
    skip_if_standby: bool
    thresholds: Thresholds = field(default_factory=Thresholds)
    config_path: Optional[Path] = None


def _get(parser: configparser.ConfigParser, section: str, key: str, fallback: str = "") -> str:
    if parser.has_option(section, key):
        return parser.get(section, key).strip()
    return fallback


def _get_int(parser: configparser.ConfigParser, section: str, key: str, fallback: int) -> int:
    raw = _get(parser, section, key, "")
    if raw == "":
        return fallback
    try:
        return int(raw, 10)
    except ValueError as exc:
        raise ConfigError(f"{section}.{key} must be an integer, got {raw!r}") from exc


def _get_bool(parser: configparser.ConfigParser, section: str, key: str, fallback: bool) -> bool:
    raw = _get(parser, section, key, "")
    if raw == "":
        return fallback
    val = raw.lower()
    if val in ("1", "true", "yes", "on"):
        return True
    if val in ("0", "false", "no", "off"):
        return False
    raise ConfigError(f"{section}.{key} must be a boolean, got {raw!r}")


def parse_drives(raw: str) -> List[str]:
    if not raw.strip():
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def load_config(path: Path) -> Config:
    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    parser = configparser.ConfigParser(interpolation=None)
    try:
        read = parser.read(path, encoding="utf-8")
    except configparser.Error as exc:
        raise ConfigError(f"invalid config file {path}: {exc}") from exc
    if not read:
        raise ConfigError(f"could not read config file: {path}")

    topic = _get(parser, "ntfy", "topic")
    server = _get(parser, "ntfy", "server", "https://ntfy.sh").rstrip("/")
    weekday = _get_int(parser, "monitor", "weekly_ok_weekday", 6)
    if weekday < 0 or weekday > 6:
        raise ConfigError("monitor.weekly_ok_weekday must be 0..6 (Mon..Sun)")

    th = Thresholds(
        temp_warn_c=_get_int(parser, "thresholds", "temp_warn_c", 50),
        temp_fail_c=_get_int(parser, "thresholds", "temp_fail_c", 60),
        realloc_warn=_get_int(parser, "thresholds", "realloc_warn", 1),
        pending_warn=_get_int(parser, "thresholds", "pending_warn", 1),
        uncorrectable_warn=_get_int(parser, "thresholds", "uncorrectable_warn", 1),
        reported_uncorrect_warn=_get_int(parser, "thresholds", "reported_uncorrect_warn", 1),
        spin_retry_warn=_get_int(parser, "thresholds", "spin_retry_warn", 1),
        end_to_end_warn=_get_int(parser, "thresholds", "end_to_end_warn", 1),
        crc_warn=_get_int(parser, "thresholds", "crc_warn", 10),
        nvme_percent_used_warn=_get_int(parser, "thresholds", "nvme_percent_used_warn", 90),
        nvme_percent_used_fail=_get_int(parser, "thresholds", "nvme_percent_used_fail", 100),
        nvme_media_errors_warn=_get_int(parser, "thresholds", "nvme_media_errors_warn", 1),
        nvme_available_spare_min_pct=_get_int(parser, "thresholds", "nvme_available_spare_min_pct", 10),
    )
    if th.temp_fail_c < th.temp_warn_c:
        raise ConfigError("thresholds.temp_fail_c must be >= temp_warn_c")

    return Config(
        ntfy_server=server or "https://ntfy.sh",
        ntfy_topic=topic,
        ntfy_token=_get(parser, "ntfy", "token"),
        drives=parse_drives(_get(parser, "monitor", "drives")),
        silence_alerts=_get_bool(parser, "monitor", "silence_alerts", False),
        timezone=_get(parser, "monitor", "timezone", "America/New_York") or "America/New_York",
        check_interval_hours=_get_int(parser, "monitor", "check_interval_hours", 24),
        weekly_ok_weekday=weekday,
        hostname=_get(parser, "monitor", "hostname"),
        smartctl_path=_get(parser, "smartctl", "path"),
        timeout_seconds=_get_int(parser, "smartctl", "timeout_seconds", 90),
        skip_if_standby=_get_bool(parser, "smartctl", "skip_if_standby", True),
        thresholds=th,
        config_path=path,
    )


def default_config_path() -> Path:
    return Path(__file__).resolve().parent.parent / "config.ini"
