"""iPhone USB backup — copies DCIM to 01_iPhone."""

from __future__ import annotations

import logging
from pathlib import Path

from ..device_detection import detect_iphone_dcim
from ..iphone_usb import copy_mtp_tree, detect_iphone_usb, is_mtp_path, list_mtp_files
from ..manifest import ManifestStore
from .base import BackupProvider, BackupResult

logger = logging.getLogger("cloudtohdd.provider.iphone")


class IPhoneProvider(BackupProvider):
    name = "iphone"

    def resolve_source(self) -> tuple[Path | str | None, str]:
        manual = self.config.get("usb_path", "").strip() or self.config.get("device_path", "").strip()
        detection = detect_iphone_usb(manual)
        if detection.is_ready and detection.dcim_path:
            return Path(detection.dcim_path), "usb_dcim"
        if detection.is_connected:
            return detection.message, "locked"
        return None, "unavailable"

    def _iter_source_files(self, source_root: Path) -> list[Path]:
        return list_mtp_files(source_root)

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
                "iPhone not found. Connect via USB, unlock, tap Trust This Computer, "
                "then run: python main.py iphone-detect"
            )
            result.files_failed = 1
            result.completeness_percent = 0.0
            result.verification_passed = False
            return result

        source_root = Path(source)
        use_mtp = is_mtp_path(source_root) or not _path_exists(source_root)

        if use_mtp:
            logger.info("[%s] Copying via MTP-aware copy from %s", self.name, source_root)
            ok, files, nbytes, errors = copy_mtp_tree(
                source_root, destination, dry_run=dry_run
            )
            result.method = "usb_mtp"
            result.files_copied = files
            result.bytes_copied = nbytes
            result.errors.extend(errors)
            if not ok and errors:
                result.files_failed = max(1, len(errors))
            elif not ok:
                result.files_failed = 1
            if not dry_run and ok:
                manifest.save()
            return result

        return super().backup_to(
            destination,
            manifest,
            dry_run=dry_run,
            verify_checksums=verify_checksums,
            incremental=False,
            mode=mode,
        )


def _path_exists(path: Path) -> bool:
    try:
        return path.exists()
    except OSError:
        return False
