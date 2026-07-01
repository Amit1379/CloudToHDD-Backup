"""Android USB (MTP) detection and file listing on Windows."""

from __future__ import annotations

import json
import logging
import platform
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .iphone_usb import copy_mtp_tree, is_mtp_path, list_mtp_files

logger = logging.getLogger("cloudtohdd.android_usb")

_SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
_DETECT_SCRIPT = _SCRIPTS_DIR / "detect_android.ps1"


@dataclass
class AndroidDetection:
    status: str  # not_found | connected_locked | ready
    device_name: str = ""
    storage_path: str = ""
    folders: list[str] = None  # type: ignore[assignment]
    file_count: int = 0
    message: str = ""

    def __post_init__(self) -> None:
        if self.folders is None:
            self.folders = []

    @property
    def is_ready(self) -> bool:
        return self.status == "ready" and bool(self.storage_path)

    @property
    def is_connected(self) -> bool:
        return self.status != "not_found"


def _run_ps_file(script: Path, *args: str, timeout: int = 120) -> str:
    if platform.system() != "Windows" or not script.exists():
        return ""
    cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), *args]
    try:
        completed = subprocess.run(
            cmd, capture_output=True, text=True, check=False,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
        return (completed.stdout or "").strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.debug("PowerShell failed: %s", exc)
        return ""


def detect_android_usb(manual_path: str = "") -> AndroidDetection:
    if manual_path:
        path = Path(manual_path)
        if path.is_dir():
            if not is_mtp_path(path) and ":" in str(path) and "usb#" not in str(path).lower():
                return AndroidDetection(
                    status="not_found",
                    message=f"Not an Android MTP path (looks like a PC folder): {path}",
                )
            files = list_mtp_files(path) if is_mtp_path(path) else _local_files(path)
            if files:
                return AndroidDetection(
                    status="ready",
                    device_name="Manual path",
                    storage_path=str(path),
                    file_count=len(files),
                    message=f"Manual path: {len(files)} files.",
                )
            return AndroidDetection(
                status="connected_locked",
                storage_path=str(path),
                message=f"Manual path has no files: {path}",
            )

    if platform.system() != "Windows":
        return AndroidDetection(status="not_found", message="Android USB backup requires Windows.")

    output = _run_ps_file(_DETECT_SCRIPT)
    if not output:
        return AndroidDetection(status="not_found", message="Detection failed — is phone connected via USB?")

    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return AndroidDetection(status="not_found", message="Detection failed — retry with phone unlocked.")

    return AndroidDetection(
        status=data.get("status", "not_found"),
        device_name=data.get("device_name", ""),
        storage_path=data.get("storage_path", ""),
        folders=list(data.get("folders") or []),
        file_count=int(data.get("file_count", 0)),
        message=data.get("message", ""),
    )


def _local_files(root: Path) -> list[Path]:
    try:
        return [p for p in root.rglob("*") if p.is_file()]
    except OSError:
        return []


def detect_android_storage(manual_path: str = "") -> Path | None:
    """Backward-compatible: return MTP storage root when ready."""
    det = detect_android_usb(manual_path)
    if det.is_ready and det.storage_path:
        return Path(det.storage_path)
    return None


def list_android_folders(storage_root: Path, folder_names: list[str]) -> list[Path]:
    """Return folders that exist on Android (MTP-aware)."""
    if not is_mtp_path(storage_root):
        found: list[Path] = []
        for name in folder_names:
            candidate = storage_root / name.replace("/", "\\")
            if candidate.is_dir():
                found.append(candidate)
        return found

    if platform.system() != "Windows":
        return []

    names_json = json.dumps(folder_names)
    script = f"""
$names = ConvertFrom-Json '{names_json.replace("'", "''")}'
$root = '{str(storage_root).replace("'", "''")}'
$found = @()
foreach ($n in $names) {{
    $p = Join-Path $root ($n -replace '/', '\\')
    if (Test-Path -LiteralPath $p) {{ $found += $p }}
}}
$found -join "`n"
"""
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True, text=True, check=False, encoding="utf-8", errors="replace", timeout=60,
        )
        output = (completed.stdout or "").strip()
    except (OSError, subprocess.TimeoutExpired):
        return []

    paths = [Path(line.strip()) for line in output.splitlines() if line.strip()]
    return paths


# Re-export for android provider
__all__ = [
    "AndroidDetection",
    "copy_mtp_tree",
    "detect_android_storage",
    "detect_android_usb",
    "is_mtp_path",
    "list_android_folders",
    "list_mtp_files",
]
