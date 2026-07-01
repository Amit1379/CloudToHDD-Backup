"""Preserve file metadata (timestamps, attributes, ACLs) during copy."""

from __future__ import annotations

import logging
import os
import platform
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("cloudtohdd.metadata")


@dataclass
class FileMetadata:
    size: int
    mtime: float
    atime: float
    ctime: float
    mode: int


def read_metadata(path: Path) -> FileMetadata:
    st = path.stat()
    return FileMetadata(
        size=st.st_size,
        mtime=st.st_mtime,
        atime=st.st_atime,
        ctime=st.st_ctime,
        mode=st.st_mode,
    )


def apply_metadata(path: Path, meta: FileMetadata) -> None:
    """Restore timestamps and permission bits on the destination file."""
    try:
        os.utime(path, (meta.atime, meta.mtime))
    except OSError as exc:
        logger.debug("Could not set utime on %s: %s", path, exc)

    if platform.system() == "Windows":
        _set_windows_creation_time(path, meta.ctime)
    else:
        try:
            os.chmod(path, stat.S_IMODE(meta.mode))
        except OSError as exc:
            logger.debug("Could not chmod %s: %s", path, exc)


def _set_windows_creation_time(path: Path, created_ts: float) -> None:
    """Set Windows creation time (st_ctime on source)."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.SetFileTime.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    kernel32.SetFileTime.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    def _to_filetime(timestamp: float) -> wintypes.FILETIME:
        ticks = int((max(timestamp, 0) + 11644473600) * 10_000_000)
        ft = wintypes.FILETIME()
        ft.dwLowDateTime = ticks & 0xFFFFFFFF
        ft.dwHighDateTime = ticks >> 32
        return ft

    handle = kernel32.CreateFileW(
        str(path),
        0x0100,  # FILE_WRITE_ATTRIBUTES
        0x00000001 | 0x00000002 | 0x00000004,  # share read/write/delete
        None,
        3,  # OPEN_EXISTING
        0x80,
        None,
    )
    invalid = wintypes.HANDLE(-1).value
    if handle == invalid or handle is None:
        return

    try:
        ctime = _to_filetime(created_ts)
        if not kernel32.SetFileTime(handle, ctypes.byref(ctime), None, None):
            logger.debug("SetFileTime failed for %s", path)
    finally:
        kernel32.CloseHandle(handle)


def copy_file_preserve_metadata(source: Path, destination: Path) -> None:
    """
    Binary copy that retains size, timestamps, creation time, and mode bits.
    EXIF and other embedded photo metadata stay intact (raw byte copy).
    """
    ensure_parent = destination.parent
    ensure_parent.mkdir(parents=True, exist_ok=True)
    meta = read_metadata(source)
    temp_destination = destination.with_name(f".{destination.name}.partial")
    try:
        shutil.copy2(source, temp_destination)
        apply_metadata(temp_destination, meta)
        temp_destination.replace(destination)
        apply_metadata(destination, meta)
    finally:
        try:
            if temp_destination.exists():
                temp_destination.unlink()
        except OSError as exc:
            logger.debug("Could not remove partial copy %s: %s", temp_destination, exc)


def robocopy_flags(level: str = "full") -> list[str]:
    """
    Robocopy flags by preservation level.

    full      — data, attributes, all timestamps, ACLs, owner (NTFS best effort)
    standard  — data, attributes, all timestamps (recommended for USB/cloud)
    compatible— FAT-friendly 2-second time rounding (legacy MTP devices)
    """
    if level == "compatible":
        return ["/COPY:DAT", "/DCOPY:T", "/FFT"]
    if level == "standard":
        return ["/COPY:DAT", "/DCOPY:DAT"]
    # DATSO = data, attributes, timestamps, security (ACLs), owner — no auditing (needs admin)
    return ["/COPY:DATSO", "/DCOPY:DAT"]


def metadata_matches(
    source: Path,
    destination: Path,
    *,
    tolerance_seconds: float = 2.0,
    compare_atime: bool = False,
) -> bool:
    """Check size and durable timestamps match within tolerance.

    Access time is intentionally ignored by default because checksum scans,
    antivirus, indexing, and opening a restored file can all update it. A backup
    should not be marked unsafe only because it was verified.
    """
    try:
        src = read_metadata(source)
        dst = read_metadata(destination)
    except OSError:
        return False

    if src.size != dst.size:
        return False

    if abs(src.mtime - dst.mtime) > tolerance_seconds:
        return False
    if compare_atime and abs(src.atime - dst.atime) > tolerance_seconds:
        return False
    if platform.system() == "Windows" and abs(src.ctime - dst.ctime) > tolerance_seconds:
        return False
    return True
