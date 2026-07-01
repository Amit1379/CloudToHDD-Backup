"""Detect USB-connected iPhone and Android devices on Windows."""

from __future__ import annotations

import logging
import platform
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("cloudtohdd.devices")


@dataclass
class UsbDevice:
    name: str
    device_type: str  # iphone | android
    storage_root: Path
    dcim_path: Path | None = None


def _run_powershell(script: str) -> str:
    if platform.system() != "Windows":
        return ""
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True,
            text=True,
            check=False,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        if completed.returncode != 0 and completed.stderr:
            logger.debug("PowerShell stderr: %s", completed.stderr.strip())
        return completed.stdout.strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.debug("PowerShell failed: %s", exc)
        return ""


def _find_shell_storage_path(device_pattern: str, subfolder: str) -> Path | None:
    """Use Shell COM to find portable device internal storage paths."""
    script = f"""
$ErrorActionPreference = 'SilentlyContinue'
$shell = New-Object -ComObject Shell.Application
$computer = $shell.Namespace(17)
if (-not $computer) {{ exit 1 }}
foreach ($device in $computer.Items()) {{
    $name = $device.Name
    if ($name -notmatch '{device_pattern}') {{ continue }}
    $devNs = $shell.Namespace($device.Path)
    if (-not $devNs) {{ continue }}
    foreach ($storage in $devNs.Items()) {{
        $root = $storage.Path
        if (-not $root) {{ continue }}
        $target = Join-Path $root '{subfolder}'
        if (Test-Path -LiteralPath $target) {{
            Write-Output $target
            exit 0
        }}
    }}
}}
exit 1
"""
    output = _run_powershell(script)
    if output and Path(output).exists():
        return Path(output)
    return None


def detect_iphone_dcim(manual_path: str = "") -> Path | None:
    """Find iPhone DCIM folder (USB). Unlock phone and tap Trust when prompted."""
    from .iphone_usb import detect_iphone_usb

    detection = detect_iphone_usb(manual_path)
    if detection.is_ready and detection.dcim_path:
        return Path(detection.dcim_path)
    return None


def get_iphone_detection(manual_path: str = "") -> "IPhoneDetection":
    from .iphone_usb import IPhoneDetection, detect_iphone_usb

    return detect_iphone_usb(manual_path)


def get_android_detection(manual_path: str = "") -> "AndroidDetection":
    from .android_usb import AndroidDetection, detect_android_usb

    return detect_android_usb(manual_path)


def detect_android_storage(manual_path: str = "") -> Path | None:
    """Find Android internal storage root (USB MTP). Enable File Transfer on phone."""
    from .android_usb import detect_android_usb

    detection = detect_android_usb(manual_path)
    if detection.is_ready and detection.storage_path:
        return Path(detection.storage_path)
    return None


def list_android_folders(storage_root: Path, folder_names: list[str]) -> list[Path]:
    """Return existing folders on Android device to copy."""
    from .android_usb import list_android_folders as _list

    return _list(storage_root, folder_names)


def detect_usb_devices() -> list[UsbDevice]:
    """Detect all connected USB phone devices."""
    devices: list[UsbDevice] = []

    iphone_dcim = detect_iphone_dcim()
    if iphone_dcim:
        devices.append(
            UsbDevice(
                name="Apple iPhone",
                device_type="iphone",
                storage_root=iphone_dcim.parent,
                dcim_path=iphone_dcim,
            )
        )
    else:
        detection = get_iphone_detection()
        if detection.is_connected:
            devices.append(
                UsbDevice(
                    name=detection.device_name or "Apple iPhone",
                    device_type="iphone",
                    storage_root=Path("."),
                    dcim_path=None,
                )
            )

    android_root = detect_android_storage()
    if android_root:
        devices.append(
            UsbDevice(
                name="Android",
                device_type="android",
                storage_root=android_root,
                dcim_path=android_root / "DCIM" if (android_root / "DCIM").exists() else None,
            )
        )
    else:
        android_det = get_android_detection()
        if android_det.is_connected:
            devices.append(
                UsbDevice(
                    name=android_det.device_name or "Android",
                    device_type="android",
                    storage_root=Path("."),
                    dcim_path=None,
                )
            )

    return devices
