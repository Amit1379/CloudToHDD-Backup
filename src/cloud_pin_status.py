"""Track cloud sync pin progress — local vs cloud-only files before backup."""

from __future__ import annotations

import json
import logging
import os
import platform
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .source_health import is_cloud_placeholder
from .utils import (
    detect_google_drive_folder,
    detect_icloud_folder,
    detect_onedrive_folder,
    ensure_dir,
    should_exclude,
    utc_now_iso,
)

logger = logging.getLogger("cloudtohdd.pin_status")

PIN_INSTRUCTIONS = """
OneDrive - download all files locally before backup:
  1. Open your OneDrive folder in File Explorer
  2. Right-click the OneDrive folder (or subfolder) -> "Always keep on this device"
  3. Click the OneDrive cloud icon in the taskbar -> watch sync until complete
  4. Re-run this check until Local files shows 100%

Google Drive (if using desktop app):
  Right-click folder -> "Available offline" or "Make available offline"

iCloud Photos / iCloud Drive:
  Right-click the iCloud Photos/Drive folder -> "Always keep on this device"
  Keep iCloud for Windows open until downloading is complete.
""".strip()


@dataclass
class CloudPinReport:
    service: str
    source_path: Path
    total_files: int = 0
    local_files: int = 0
    cloud_files: int = 0
    unreadable_files: int = 0
    local_bytes: int = 0
    cloud_bytes: int = 0
    cloud_examples: list[str] = field(default_factory=list)
    scan_seconds: float = 0.0
    timestamp: str = ""

    @property
    def percent_files_local(self) -> float:
        if self.total_files == 0:
            return 100.0
        return (self.local_files / self.total_files) * 100.0

    @property
    def percent_bytes_local(self) -> float:
        total = self.local_bytes + self.cloud_bytes
        if total == 0:
            return 100.0
        return (self.local_bytes / total) * 100.0

    @property
    def ready_for_backup(self) -> bool:
        return self.cloud_files == 0 and self.unreadable_files == 0 and self.total_files > 0

    @property
    def status_label(self) -> str:
        if self.total_files == 0:
            return "empty"
        if self.ready_for_backup:
            return "ready"
        if self.percent_files_local >= 99.0:
            return "almost_ready"
        if self.percent_files_local <= 1.0:
            return "cloud_only"
        return "downloading"


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _is_locally_available(path: Path) -> tuple[bool, str]:
    """Return (is_local, reason) where reason is local | cloud | unreadable."""
    if is_cloud_placeholder(path):
        return False, "cloud"
    try:
        size = path.stat().st_size
    except OSError:
        return False, "unreadable"

    if size == 0:
        return True, "local"

    try:
        with path.open("rb") as handle:
            if handle.read(1):
                return True, "local"
        return False, "unreadable"
    except OSError:
        return False, "unreadable"


def scan_cloud_pin_progress(
    source_root: Path,
    service: str = "onedrive",
    exclude_patterns: list[str] | None = None,
    *,
    progress_callback=None,
    max_examples: int = 15,
) -> CloudPinReport:
    exclude_patterns = exclude_patterns or []
    report = CloudPinReport(
        service=service,
        source_path=source_root,
        timestamp=utc_now_iso(),
    )
    t0 = time.perf_counter()

    files = [
        p
        for p in source_root.rglob("*")
        if p.is_file() and not should_exclude(p.name, exclude_patterns)
    ]
    report.total_files = len(files)

    for idx, path in enumerate(files):
        if progress_callback:
            progress_callback(idx + 1, len(files), path)

        size = _file_size(path)
        is_local, reason = _is_locally_available(path)

        try:
            rel = path.relative_to(source_root).as_posix()
        except ValueError:
            rel = path.name

        if reason == "cloud":
            report.cloud_files += 1
            report.cloud_bytes += size
            if len(report.cloud_examples) < max_examples:
                report.cloud_examples.append(rel)
        elif reason == "unreadable":
            report.unreadable_files += 1
            report.cloud_bytes += size
            if len(report.cloud_examples) < max_examples:
                report.cloud_examples.append(rel)
        else:
            report.local_files += 1
            report.local_bytes += size

    report.scan_seconds = time.perf_counter() - t0
    return report


def resolve_cloud_folder(service: str, config: dict | None = None) -> Path | None:
    config = config or {}
    providers = config.get("providers", {})

    if service == "onedrive":
        cfg = providers.get("onedrive", {})
        sync = cfg.get("sync_folder", "")
        if sync:
            path = Path(sync)
            return path if path.is_dir() else None
        return detect_onedrive_folder()

    if service == "google_drive":
        cfg = providers.get("google_drive", {})
        sync = cfg.get("sync_folder", "")
        if sync:
            path = Path(sync)
            return path if path.is_dir() else None
        return detect_google_drive_folder()

    if service == "icloud":
        cfg = providers.get("icloud", {})
        sync = cfg.get("sync_folder", "")
        if sync:
            path = Path(sync)
            return path if path.is_dir() else None
        return detect_icloud_folder()

    return None


def save_pin_report(report: CloudPinReport, logs_dir: Path) -> Path:
    ensure_dir(logs_dir)
    payload = {
        **asdict(report),
        "source_path": str(report.source_path),
        "percent_files_local": round(report.percent_files_local, 2),
        "percent_bytes_local": round(report.percent_bytes_local, 2),
        "ready_for_backup": report.ready_for_backup,
        "status_label": report.status_label,
    }
    json_path = logs_dir / f"PIN_STATUS_{report.service}_{report.timestamp.replace(':', '-')}.json"
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    txt_path = logs_dir / f"PIN_STATUS_{report.service}_LATEST.txt"
    lines = [
        "=" * 60,
        f"{report.service.upper()} PIN / DOWNLOAD STATUS",
        "=" * 60,
        f"Scanned: {report.timestamp}",
        f"Folder:  {report.source_path}",
        "",
        f"Files local:     {report.local_files:,} / {report.total_files:,}  ({report.percent_files_local:.1f}%)",
        f"Files cloud-only: {report.cloud_files:,}",
        f"Files unreadable: {report.unreadable_files:,}",
        "",
        f"Data local:      {_fmt_bytes(report.local_bytes)}  ({report.percent_bytes_local:.1f}%)",
        f"Data cloud-only: {_fmt_bytes(report.cloud_bytes)}",
        "",
        f"Ready for backup: {'YES' if report.ready_for_backup else 'NO'}",
        "",
    ]
    if not report.ready_for_backup:
        lines.append("ACTION REQUIRED:")
        lines.append(PIN_INSTRUCTIONS)
        lines.append("")
        if report.cloud_examples:
            lines.append("Cloud-only examples:")
            for ex in report.cloud_examples[:10]:
                lines.append(f"  - {ex}")
    else:
        lines.append("All files are local. You can run the backup now.")
    lines.append("=" * 60)
    txt_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("Pin status report: %s", txt_path)
    return txt_path


def open_folder_in_explorer(path: Path) -> None:
    if platform.system() == "Windows":
        os.startfile(str(path))  # noqa: S606
    elif platform.system() == "Darwin":
        subprocess.run(["open", str(path)], check=False)
    else:
        subprocess.run(["xdg-open", str(path)], check=False)


@dataclass
class PinRequestResult:
    source_path: Path
    method: str
    success: bool
    message: str
    cloud_files_before: int = 0


def request_pin_all_windows(source_root: Path, *, service: str = "onedrive") -> PinRequestResult:
    """
    Ask OneDrive to download all files locally ('Always keep on this device').
    Uses Windows Cloud Files API (CfSetPinState) with attrib +P fallback.
    """
    if platform.system() != "Windows":
        return PinRequestResult(
            source_path=source_root,
            method="none",
            success=False,
            message="Automatic pin is only supported on Windows.",
        )

    if not source_root.is_dir():
        return PinRequestResult(
            source_path=source_root,
            method="none",
            success=False,
            message=f"Folder not found: {source_root}",
        )

    # Quick scan to report how many are cloud-only before requesting pin
    quick = scan_cloud_pin_progress(source_root, service=service)
    if quick.ready_for_backup:
        return PinRequestResult(
            source_path=source_root,
            method="skip",
            success=True,
            message="All files are already local.",
            cloud_files_before=0,
        )

    # Method 1: CfSetPinState recursive on sync root (fastest — one API call)
    cf_ok, cf_msg = _cf_pin_recursive(source_root)
    if cf_ok:
        return PinRequestResult(
            source_path=source_root,
            method="CfSetPinState",
            success=True,
            message=(
                f"Requested 'Always keep on this device' for all files under {source_root}. "
                f"{cf_msg} {service.replace('_', ' ').title()} will download "
                f"~{_fmt_bytes(quick.cloud_bytes)} in the background. "
                f"Run: python main.py pin-status --service {service} --watch 120"
            ),
            cloud_files_before=quick.cloud_files,
        )

    # Method 2: attrib +P /S /D (documented by Microsoft for OneDrive)
    attrib_ok, attrib_msg = _attrib_pin_recursive(source_root)
    if attrib_ok:
        return PinRequestResult(
            source_path=source_root,
            method="attrib",
            success=True,
            message=(
                f"Requested local download via attrib +P for {source_root}. "
                f"{attrib_msg} Watch {service.replace('_', ' ').title()} sync. "
                f"Run: python main.py pin-status --service {service} --watch 120"
            ),
            cloud_files_before=quick.cloud_files,
        )

    return PinRequestResult(
        source_path=source_root,
        method="failed",
        success=False,
        message=(
            f"Could not auto-pin files. {cf_msg}; {attrib_msg}. "
            "Manually: right-click the cloud folder -> Always keep on this device."
        ),
        cloud_files_before=quick.cloud_files,
    )


def _cf_pin_recursive(source_root: Path) -> tuple[bool, str]:
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        cldapi = ctypes.windll.cldapi

        GENERIC_READ = 0x80000000
        FILE_SHARE_READ = 0x00000001
        FILE_SHARE_WRITE = 0x00000002
        FILE_SHARE_DELETE = 0x00000004
        OPEN_EXISTING = 3
        FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
        FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
        INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value

        CF_PIN_STATE_PINNED = 1
        CF_SET_PIN_FLAG_RECURSE = 0x00000001

        CreateFileW = kernel32.CreateFileW
        CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        CreateFileW.restype = wintypes.HANDLE

        CfSetPinState = cldapi.CfSetPinState
        CfSetPinState.argtypes = [
            wintypes.HANDLE,
            wintypes.INT,
            wintypes.DWORD,
            wintypes.LPVOID,
        ]
        CfSetPinState.restype = wintypes.HRESULT

        CloseHandle = kernel32.CloseHandle

        handle = CreateFileW(
            str(source_root),
            GENERIC_READ,
            FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
            None,
            OPEN_EXISTING,
            FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT,
            None,
        )
        if handle == INVALID_HANDLE_VALUE:
            err = ctypes.get_last_error()
            return False, f"CfSetPinState open failed (error {err})"

        try:
            hr = CfSetPinState(handle, CF_PIN_STATE_PINNED, CF_SET_PIN_FLAG_RECURSE, None)
            if hr < 0:
                return False, f"CfSetPinState returned {hr:#010x}"
            return True, "Cloud Files API pin request accepted."
        finally:
            CloseHandle(handle)
    except Exception as exc:
        return False, str(exc)


def _attrib_pin_recursive(source_root: Path) -> tuple[bool, str]:
    try:
        completed = subprocess.run(
            ["attrib", "+P", "/S", "/D", str(source_root)],
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )
        if completed.returncode == 0:
            return True, "attrib +P /S /D completed."
        err = (completed.stderr or completed.stdout or "").strip()
        return False, f"attrib failed (code {completed.returncode}): {err[:200]}"
    except Exception as exc:
        return False, str(exc)


def _fmt_bytes(num: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num < 1024:
            return f"{num:.1f} {unit}" if unit != "B" else f"{num} B"
        num /= 1024
    return f"{num:.1f} PB"
