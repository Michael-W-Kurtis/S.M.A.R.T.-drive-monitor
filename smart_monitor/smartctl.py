"""Invoke smartctl and decode JSON. Linux and Windows share this path."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence


class SmartctlError(Exception):
    pass


@dataclass
class ScanDevice:
    name: str
    dev_type: str
    protocol: str
    info_name: str


@dataclass
class DriveReport:
    name: str
    dev_type: str
    protocol: str
    model: str
    serial: str
    exit_status: int
    payload: Dict[str, Any]
    standby: bool = False
    open_failed: bool = False
    messages: List[str] = field(default_factory=list)


Runner = Callable[[Sequence[str], int], subprocess.CompletedProcess]


def default_runner(argv: Sequence[str], timeout: int) -> subprocess.CompletedProcess:
    kwargs: Dict[str, Any] = {
        "args": list(argv),
        "capture_output": True,
        "timeout": timeout,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(**kwargs)


def discover_smartctl(configured: str = "") -> str:
    if configured:
        path = configured
        if os.path.isfile(path):
            return path
        found = shutil.which(path)
        if found:
            return found
        raise SmartctlError(f"smartctl not found at configured path: {configured}")
    found = shutil.which("smartctl") or shutil.which("smartctl.exe")
    if found:
        return found
    candidates = [
        r"C:\Program Files\smartmontools\bin\smartctl.exe",
        r"C:\Program Files (x86)\smartmontools\bin\smartctl.exe",
        "/usr/sbin/smartctl",
        "/usr/bin/smartctl",
        "/usr/local/sbin/smartctl",
    ]
    for cand in candidates:
        if os.path.isfile(cand):
            return cand
    raise SmartctlError(
        "smartctl not found in PATH or common install locations. "
        "Install smartmontools and/or set smartctl.path in config.ini"
    )


def _decode_json(raw: bytes) -> Dict[str, Any]:
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        raise SmartctlError("smartctl produced empty stdout")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SmartctlError(f"smartctl stdout is not JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise SmartctlError("smartctl JSON root is not an object")
    return data


def _messages(payload: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    for item in payload.get("smartctl", {}).get("messages") or []:
        if isinstance(item, dict):
            s = str(item.get("string") or "").strip()
            if s:
                out.append(s)
        elif item:
            out.append(str(item))
    return out


def is_standby_message(messages: Sequence[str], payload: Dict[str, Any]) -> bool:
    blob = " ".join(messages).lower()
    if "standby mode" in blob or "device is in standby" in blob:
        return True
    if "sleep mode" in blob or "device is in sleep" in blob:
        return True
    pm = str(payload.get("power_mode") or "").lower()
    return pm in ("standby", "sleep")


def scan_devices(
    smartctl_bin: str,
    timeout: int,
    runner: Optional[Runner] = None,
) -> List[ScanDevice]:
    run = runner or default_runner
    proc = run([smartctl_bin, "--scan", "-j"], timeout)
    payload = _decode_json(proc.stdout or b"{}")
    devices: List[ScanDevice] = []
    for item in payload.get("devices") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        info = str(item.get("info_name") or name)
        low = info.lower()
        if any(tok in low for tok in ("cdrom", "cd-rom", "dvd", "bd-rom", "optical")):
            continue
        devices.append(
            ScanDevice(
                name=name,
                dev_type=str(item.get("type") or ""),
                protocol=str(item.get("protocol") or ""),
                info_name=info,
            )
        )
    return devices


def query_drive(
    smartctl_bin: str,
    device: ScanDevice,
    timeout: int,
    skip_if_standby: bool,
    runner: Optional[Runner] = None,
) -> DriveReport:
    run = runner or default_runner
    argv = [smartctl_bin, "-j", "-H", "-A", "-l", "error", "-l", "selftest"]
    if skip_if_standby:
        argv.extend(["-n", "standby"])
    if device.dev_type:
        argv.extend(["-d", device.dev_type])
    argv.append(device.name)
    try:
        proc = run(argv, timeout)
    except subprocess.TimeoutExpired as exc:
        raise SmartctlError(f"smartctl timed out on {device.name}") from exc
    stdout = proc.stdout or b""
    if not stdout.strip():
        err = (proc.stderr or b"").decode("utf-8", errors="replace").strip()
        raise SmartctlError(f"smartctl produced no JSON for {device.name}: {err or proc.returncode}")
    payload = _decode_json(stdout)
    messages = _messages(payload)
    exit_status = int(payload.get("smartctl", {}).get("exit_status", proc.returncode or 0))
    standby = is_standby_message(messages, payload)
    open_failed = bool(exit_status & 0x02) and not standby
    ident = payload.get("device") if isinstance(payload.get("device"), dict) else {}
    model = str(
        payload.get("model_name")
        or payload.get("scsi_model_name")
        or ident.get("name")
        or ""
    )
    serial = str(payload.get("serial_number") or payload.get("scsi_serial_number") or "")
    protocol = str(ident.get("protocol") or device.protocol)
    return DriveReport(
        name=device.name,
        dev_type=device.dev_type or str(ident.get("type") or ""),
        protocol=protocol,
        model=model,
        serial=serial,
        exit_status=exit_status,
        payload=payload,
        standby=standby,
        open_failed=open_failed,
        messages=messages,
    )
