"""Shared utilities for path handling, hashing, and Windows detection."""

from __future__ import annotations

import hashlib
import logging
import os
import platform
import re
from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path
from typing import Iterable

logger = logging.getLogger("cloudtohdd.utils")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def backup_date_stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def should_exclude(name: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch(name, pattern) for pattern in patterns)


def resolve_destination(
    root: Path,
    provider: str,
    layout: str,
) -> Path:
    if layout == "digital_archive":
        from .digital_archive import resolve_archive_destination

        return resolve_archive_destination(root, provider)
    if layout == "flat":
        return ensure_dir(root)
    if layout == "provider":
        return ensure_dir(root / provider)
    if layout == "provider_date":
        return ensure_dir(root / provider / backup_date_stamp())
    raise ValueError(f"Unknown layout: {layout}")


def detect_onedrive_folder() -> Path | None:
    if platform.system() != "Windows":
        return None

    user_profile = os.environ.get("USERPROFILE")
    if not user_profile:
        return None

    candidates = [
        Path(user_profile) / "OneDrive",
        Path(user_profile) / "OneDrive - Personal",
    ]

    for entry in Path(user_profile).iterdir():
        if entry.is_dir() and re.match(r"OneDrive\s*-\s*.+", entry.name, re.I):
            candidates.append(entry)

    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return None


def detect_google_drive_folder() -> Path | None:
    if platform.system() != "Windows":
        return None

    user_profile = os.environ.get("USERPROFILE")
    if not user_profile:
        return None

    candidates = [
        Path(user_profile) / "Google Drive",
        Path(user_profile) / "My Drive",
        Path("G:\\"),
        Path("G:\\My Drive"),
    ]

    for candidate in candidates:
        if candidate.exists() and candidate.is_dir():
            return candidate
    return None


def detect_icloud_folder() -> Path | None:
    if platform.system() != "Windows":
        return None

    user_profile = os.environ.get("USERPROFILE")
    if not user_profile:
        return None

    candidates = [
        Path(user_profile) / "iCloudDrive",
        Path(user_profile) / "iCloud Drive",
    ]

    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return None


def resolve_writable_root(configured: str, fallback: Path) -> Path:
    """Return configured root if drive exists and is writable, else fallback."""
    expanded = os.path.expandvars(str(configured))
    path = Path(expanded)
    drive = path.drive
    if drive and not Path(drive + "\\").exists():
        logger.warning("Drive %s not found — using fallback %s", drive, fallback)
        return ensure_dir(fallback)
    try:
        return ensure_dir(path)
    except OSError:
        logger.warning("Cannot write to %s — using fallback %s", path, fallback)
        return ensure_dir(fallback)


def find_rclone_executable() -> str | None:
    import shutil

    found = shutil.which("rclone")
    if found:
        return found

    common_paths = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "rclone" / "rclone.exe",
        Path("C:/Program Files/rclone/rclone.exe"),
        Path("C:/rclone/rclone.exe"),
    ]
    for path in common_paths:
        if path.is_file():
            return str(path)
    return None
