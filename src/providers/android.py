"""Android USB backup — copies DCIM, Pictures, WhatsApp, etc. to 02_Android."""

from __future__ import annotations

import logging
from pathlib import Path

from ..android_usb import detect_android_usb, list_android_folders
from ..digital_archive import ANDROID_DEFAULT_FOLDERS
from ..iphone_usb import copy_mtp_tree, is_mtp_path, list_mtp_files
from ..manifest import ManifestStore
from ..robocopy_provider import run_robocopy
from .base import BackupProvider, BackupResult

logger = logging.getLogger("cloudtohdd.android")


class AndroidProvider(BackupProvider):
    name = "android"

    def resolve_source(self) -> tuple[Path | str | None, str]:
        manual = self.config.get("usb_path", "").strip() or self.config.get("device_path", "").strip()
        detection = detect_android_usb(manual)
        if detection.is_ready and detection.storage_path:
            return Path(detection.storage_path), "usb_mtp"
        if detection.is_connected:
            return detection.message, "locked"
        return None, "unavailable"

    def get_copy_folders(self, storage_root: Path) -> list[Path]:
        configured = self.config.get("copy_folders", [])
        names = configured if configured else ANDROID_DEFAULT_FOLDERS
        return list_android_folders(storage_root, names)

    def backup_to(
        self,
        destination: Path,
        manifest: ManifestStore,
        *,
        dry_run: bool = False,
        verify_checksums: bool = True,
        incremental: bool = True,
        mode: str = "copy",
    ) -> BackupResult:
        source, method = self.resolve_source()
        result = BackupResult(
            provider=self.name,
            method=method,
            destination=str(destination),
            dry_run=dry_run,
        )

        if method == "locked":
            result.errors.append(str(source))
            result.files_failed = 1
            result.completeness_percent = 0.0
            result.verification_passed = False
            return result

        if method == "unavailable" or source is None:
            result.errors.append(
                "Android not detected. Connect phone via USB, enable File Transfer (MTP), "
                "unlock phone, then run: python main.py android-detect"
            )
            result.files_failed = 1
            result.completeness_percent = 0.0
            result.verification_passed = False
            return result

        storage_root = Path(source)
        folders = self.get_copy_folders(storage_root)
        if not folders:
            result.errors.append(
                f"No media folders found on Android at {storage_root}. "
                "Unlock phone and set USB mode to File Transfer / MTP."
            )
            result.files_failed += 1
            result.completeness_percent = 0.0
            result.verification_passed = False
            return result

        use_mtp = is_mtp_path(storage_root) or any(is_mtp_path(f) for f in folders)
        logger.info("[%s] Copying %d folders from %s (mtp=%s)", self.name, len(folders), storage_root, use_mtp)

        for folder in folders:
            try:
                rel_name = folder.relative_to(storage_root)
            except ValueError:
                rel_name = Path(folder.name)
            dest_sub = destination / rel_name
            if use_mtp:
                ok, files, nbytes, errors = copy_mtp_tree(
                    folder, dest_sub, dry_run=dry_run
                )
            else:
                ok, files, nbytes, errors = run_robocopy(
                    folder,
                    dest_sub,
                    dry_run=dry_run,
                    max_retries=self.max_retries,
                    exclude_patterns=self.exclude_patterns,
                    metadata_level=self._metadata_level(),
                )
            result.files_copied += files
            result.bytes_copied += nbytes
            result.errors.extend(errors)
            if not ok:
                result.files_failed += max(1, len(errors))

        result.verification_passed = result.files_failed == 0 and result.files_copied > 0
        if not dry_run and result.verification_passed:
            manifest.save()
        return result

    def _iter_source_files(self, source_root: Path) -> list[Path]:
        files: list[Path] = []
        folders = self.get_copy_folders(source_root)
        if is_mtp_path(source_root) or any(is_mtp_path(f) for f in folders):
            for folder in folders:
                files.extend(list_mtp_files(folder))
            return files

        for folder in folders:
            if folder.is_file():
                from ..utils import should_exclude
                if not should_exclude(folder.name, self.exclude_patterns):
                    files.append(folder)
                continue
            for path in folder.rglob("*"):
                if not path.is_file():
                    continue
                from ..utils import should_exclude
                if should_exclude(path.name, self.exclude_patterns):
                    continue
                files.append(path)
        return files
