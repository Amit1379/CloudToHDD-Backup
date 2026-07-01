"""iPhone USB detection and MTP-aware copy on Windows."""

from __future__ import annotations

import json
import logging
import platform
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("cloudtohdd.iphone_usb")

_SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
_DETECT_SCRIPT = _SCRIPTS_DIR / "detect_iphone.ps1"
_COPY_SCRIPT = _SCRIPTS_DIR / "copy_iphone_dcim.ps1"


@dataclass
class IPhoneDetection:
    status: str  # not_found | connected_locked | connected_no_dcim | ready
    device_name: str = ""
    storage_name: str = ""
    dcim_path: str = ""
    dcim_file_count: int = 0
    message: str = ""

    @property
    def is_ready(self) -> bool:
        return self.status == "ready" and bool(self.dcim_path)

    @property
    def is_connected(self) -> bool:
        return self.status != "not_found"


def _run_powershell_file(script: Path, *args: str, timeout: int = 90) -> str:
    if platform.system() != "Windows" or not script.exists():
        return ""
    cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), *args]
    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        return (completed.stdout or "").strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.debug("PowerShell failed: %s", exc)
        return ""


def _run_powershell(script: str, *, timeout: int = 90) -> str:
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
            timeout=timeout,
        )
        return (completed.stdout or "").strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.debug("PowerShell failed: %s", exc)
        return ""


def detect_iphone_usb(manual_path: str = "") -> IPhoneDetection:
    if manual_path:
        path = Path(manual_path)
        if path.is_dir():
            files = list_mtp_files(path) if platform.system() == "Windows" else []
            if not files:
                try:
                    files = [p for p in path.rglob("*") if p.is_file()]
                except OSError:
                    files = []
            count = len(files)
            if count == 0:
                return IPhoneDetection(
                    status="connected_no_dcim",
                    device_name="Manual path",
                    dcim_path=str(path),
                    message=f"Manual usb_path has no files: {path}",
                )
            return IPhoneDetection(
                status="ready",
                device_name="Manual path",
                dcim_path=str(path),
                dcim_file_count=count,
                message=f"Using manual path ({count} files).",
            )

    if platform.system() != "Windows":
        return IPhoneDetection(status="not_found", message="iPhone USB backup requires Windows.")

    output = _run_powershell_file(_DETECT_SCRIPT, timeout=90)
    if not output:
        return IPhoneDetection(status="not_found", message="Detection failed — is iPhone connected?")

    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        logger.debug("Invalid detection JSON: %s", output[:200])
        return IPhoneDetection(status="not_found", message="Detection failed — retry with phone unlocked.")

    return IPhoneDetection(
        status=data.get("status", "not_found"),
        device_name=data.get("device_name", ""),
        storage_name=data.get("storage_name", ""),
        dcim_path=data.get("dcim_path", ""),
        dcim_file_count=int(data.get("dcim_file_count", 0)),
        message=data.get("message", ""),
    )


def _path_accessible(path: Path) -> bool:
    try:
        next(path.iterdir())
        return True
    except OSError:
        return False


def detect_iphone_dcim(manual_path: str = "") -> Path | None:
    """Backward-compatible: return DCIM path only when fully ready."""
    detection = detect_iphone_usb(manual_path)
    if detection.is_ready and detection.dcim_path:
        return Path(detection.dcim_path)
    return None


def list_mtp_files(root: Path) -> list[Path]:
    """List files under an MTP/iPhone path using PowerShell."""
    if platform.system() != "Windows":
        return [p for p in root.rglob("*") if p.is_file()]

    root_str = str(root)
    output = _run_powershell(
        f"Get-ChildItem -LiteralPath '{root_str.replace(chr(39), chr(39)*2)}' "
        "-Recurse -File -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName }",
        timeout=600,
    )
    files: list[Path] = []
    for line in output.splitlines():
        line = line.strip()
        if line:
            files.append(Path(line))
    if files:
        return files

    # Python fallback
    try:
        return [p for p in root.rglob("*") if p.is_file()]
    except OSError:
        return []


def copy_mtp_tree(source: Path, destination: Path, *, dry_run: bool = False) -> tuple[bool, int, int, list[str]]:
    """Copy iPhone DCIM tree — robocopy with PowerShell fallback for MTP paths."""
    if dry_run:
        files = list_mtp_files(source)
        total_bytes = 0
        for f in files:
            try:
                total_bytes += f.stat().st_size
            except OSError:
                pass
        return True, len(files), total_bytes, []

    if platform.system() != "Windows":
        return False, 0, 0, ["MTP copy requires Windows"]

    dest_str = str(destination)
    src_str = str(source)
    try:
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(_COPY_SCRIPT),
                "-Source",
                src_str,
                "-Destination",
                dest_str,
            ],
            capture_output=True,
            text=True,
            check=False,
            encoding="utf-8",
            errors="replace",
            timeout=86400,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, 0, 0, [str(exc)]

    errors: list[str] = []
    if completed.returncode != 0:
        err = (completed.stderr or completed.stdout or "").strip()
        errors.append(err or f"Copy failed with code {completed.returncode}")

    files = list_mtp_files(destination)
    total_bytes = 0
    for f in files:
        try:
            total_bytes += f.stat().st_size
        except OSError:
            pass
    return completed.returncode == 0, len(files), total_bytes, errors


def is_mtp_path(path: Path) -> bool:
    s = str(path)
    return "::" in s or "usb#vid" in s.lower() or "wpd" in s.lower()
