"""Google Drive provider."""

from __future__ import annotations

from pathlib import Path

from ..utils import detect_google_drive_folder
from .base import BackupProvider


class GoogleDriveProvider(BackupProvider):
    name = "google_drive"

    def resolve_source(self) -> tuple[Path | str | None, str]:
        method = self.config.get("method", "auto")
        sync_folder = self.config.get("sync_folder", "").strip()
        rclone_remote = self.config.get("rclone_remote", "gdrive")

        if method in ("sync_folder", "auto"):
            folder = Path(sync_folder) if sync_folder else detect_google_drive_folder()
            if folder and folder.is_dir():
                return folder, "sync_folder"

        if method in ("rclone", "auto"):
            return f"{rclone_remote}:", "rclone"

        return None, "unavailable"
