"""Detect cloud-only placeholders and unreadable source files before backup."""

from __future__ import annotations

import logging
import os
import platform
import stat
from dataclasses import dataclass, field
from pathlib import Path

from .utils import should_exclude

logger = logging.getLogger("cloudtohdd.source_health")

# Windows FILE_ATTRIBUTE_OFFLINE | FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS
_WIN_OFFLINE_ATTR = 0x00001000
_WIN_RECALL_ATTR = 0x00400000


@dataclass
class SourceHealthReport:
    provider: str
    source_root: Path
    total_files: int = 0
    placeholders: list[str] = field(default_factory=list)
    zero_byte_files: list[str] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)

    @property
    def placeholder_count(self) -> int:
        return len(self.placeholders)

    @property
    def passed(self) -> bool:
        return not self.placeholders and not self.unreadable

    @property
    def warnings(self) -> list[str]:
        msgs = []
        if self.placeholders:
            msgs.append(
                f"{len(self.placeholders)} cloud-only placeholder file(s) — "
                "download locally before backup (OneDrive: 'Always keep on this device')"
            )
        if self.zero_byte_files:
            msgs.append(f"{len(self.zero_byte_files)} zero-byte file(s) detected")
        if self.unreadable:
            msgs.append(f"{len(self.unreadable)} unreadable file(s)")
        return msgs


def is_cloud_placeholder(path: Path) -> bool:
    """True if file is a cloud stub not fully downloaded locally (OneDrive etc.)."""
    if platform.system() != "Windows":
        return False
    try:
        st = path.stat()
        if st.st_size == 0 and path.suffix.lower() not in ("", ".txt", ".gitkeep"):
            # zero-byte non-trivial files are suspicious
            pass
    except OSError:
        return True

    try:
        import ctypes

        attrs = ctypes.windll.kernel32.GetFileAttributesW(str(path))
        if attrs == -1:
            return False
        if attrs & (_WIN_OFFLINE_ATTR | _WIN_RECALL_ATTR):
            return True
    except (OSError, AttributeError):
        pass

    try:
        if path.is_symlink():
            return True
        st = path.lstat()
        if stat.S_ISLNK(st.st_mode):
            return True
    except OSError:
        return True

    return False


def _can_read_file(path: Path, min_bytes: int = 1) -> bool:
    try:
        with path.open("rb") as handle:
            data = handle.read(min_bytes)
        return len(data) >= min_bytes or path.stat().st_size == 0
    except OSError:
        return False


def scan_source_health(
    provider: str,
    source_root: Path,
    file_paths: list[Path],
    *,
    detect_placeholders: bool = True,
) -> SourceHealthReport:
    report = SourceHealthReport(provider=provider, source_root=source_root)
    report.total_files = len(file_paths)

    for path in file_paths:
        try:
            rel = path.relative_to(source_root).as_posix()
        except ValueError:
            rel = str(path)

        if detect_placeholders and is_cloud_placeholder(path):
            report.placeholders.append(rel)
            continue

        try:
            size = path.stat().st_size
        except OSError:
            report.unreadable.append(rel)
            continue

        if size == 0 and path.suffix:
            report.zero_byte_files.append(rel)

        if size > 0 and not _can_read_file(path):
            report.unreadable.append(rel)

    if report.placeholders:
        logger.warning(
            "[%s] %d placeholder files — NOT safe to delete cloud source",
            provider,
            len(report.placeholders),
        )
    return report
