"""CloudToHDD Backup folder layout for consolidated photo archives."""

from __future__ import annotations

from pathlib import Path

from .utils import ensure_dir

# Master checklist folder structure under destination_root
ARCHIVE_FOLDERS = {
    "iphone": "01_iPhone",
    "android": "02_Android",
    "google_photos": "03_GooglePhotos",
    "google_drive": "04_GoogleDrive",
    "onedrive": "05_OneDrive",
    "deduplicated": "06_Deduplicated",
    "important_documents": "07_Important_Documents",
    "videos": "08_Videos",
    "final_archive": "09_Final_Archive",
    "logs": "Logs",
    "icloud": "10_iCloud",
}

# Android folders to copy when device is connected (relative to device storage root)
ANDROID_DEFAULT_FOLDERS = [
    "DCIM",
    "Pictures",
    "Download",
    "Downloads",
    "WhatsApp/Media/WhatsApp Images",
    "WhatsApp/Media/WhatsApp Video",
    "Camera",
]


def ensure_archive_structure(root: Path) -> dict[str, Path]:
    """Create all CloudToHDD archive subfolders and return path map."""
    paths = {key: ensure_dir(root / folder) for key, folder in ARCHIVE_FOLDERS.items()}
    ensure_dir(paths["deduplicated"] / "Review")
    for sub in ("Photos", "Videos", "Documents", "Family", "Travel", "Work", "Important"):
        ensure_dir(paths["final_archive"] / sub)
    return paths


def resolve_archive_destination(root: Path, provider: str) -> Path:
    """Map provider name to checklist folder (no date subfolder)."""
    folder = ARCHIVE_FOLDERS.get(provider)
    if not folder:
        raise ValueError(f"No archive folder mapping for provider: {provider}")
    return ensure_dir(root / folder)
