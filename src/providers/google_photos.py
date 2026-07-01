"""Google Photos backup via Takeout ZIPs or rclone."""

from __future__ import annotations

import logging
from pathlib import Path

from ..manifest import ManifestStore
from ..takeout import count_files_and_size, extract_takeout_archives, find_takeout_zips
from ..utils import find_rclone_executable
from .base import BackupProvider, BackupResult

logger = logging.getLogger("cloudtohdd.google_photos")

TAKEOUT_HELP = """
Google Photos backup needs export files from Google Takeout:

  1. Open https://takeout.google.com
  2. Click 'Deselect all', then select ONLY 'Google Photos'
  3. Choose .zip files, max size 50 GB (or larger if you have space)
  4. Create export and wait for Google's email (can take hours/days)
  5. Download ALL .zip files to your takeout_download_folder
  6. Run backup again — ZIPs are extracted automatically

Set folder in GUI: Connect Cloud Accounts -> Google Photos -> Set Takeout folder
Or: python main.py connect google-photos --takeout
""".strip()


class GooglePhotosProvider(BackupProvider):
    name = "google_photos"

    def resolve_source(self) -> tuple[Path | str | None, str]:
        method = self.config.get("method", "takeout")
        takeout_folder = self.config.get("takeout_download_folder", "").strip()

        if method in ("takeout", "auto") and takeout_folder:
            path = Path(takeout_folder)
            return path, "takeout"

        rclone_remote = self.config.get("rclone_remote", "gphotos")
        if method in ("rclone", "auto") and find_rclone_executable():
            return f"{rclone_remote}:", "rclone"

        return None, "unavailable"

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

        if source is None:
            result.errors.append(
                "Google Photos not configured. Either:\n"
                "  1) Download from takeout.google.com → set takeout_download_folder\n"
                "  2) Run: scripts\\Setup-Rclone.ps1 and set method: rclone"
            )
            result.files_failed += 1
            result.verification_passed = False
            return result

        if method == "rclone":
            return self._backup_via_rclone(
                str(source), destination, result, dry_run=dry_run, mode=mode
            )

        takeout_path = Path(source)
        auto_extract = self.config.get("auto_extract", True)

        # ZIPs belong in _downloads, not the same folder as extracted photos
        try:
            if takeout_path.resolve() == destination.resolve():
                result.errors.append(
                    "takeout_download_folder must not be the same as the backup folder. "
                    f"Use: {destination / '_downloads'}"
                )
                result.files_failed += 1
                result.verification_passed = False
                return result
        except OSError:
            pass

        zips = find_takeout_zips(takeout_path)
        if not zips and not dry_run:
            takeout_path.mkdir(parents=True, exist_ok=True)
            result.errors.append(
                f"No Takeout .zip files in {takeout_path}.\n{TAKEOUT_HELP}"
            )
            result.files_failed += 1
            result.verification_passed = False
            return result

        if method == "takeout" and auto_extract:
            extracted, nbytes, errors = extract_takeout_archives(
                takeout_path, destination, dry_run=dry_run
            )
            result.files_copied = extracted
            result.bytes_copied = nbytes
            result.errors.extend(errors)
            if errors:
                result.files_failed += len(errors)

        # Copy any loose files (non-zip) from takeout folder
        if method == "takeout" and not dry_run:
            from ..robocopy_provider import run_robocopy

            ok, files, nbytes, errors = run_robocopy(
                takeout_path,
                destination / "_takeout_raw",
                dry_run=False,
                max_retries=self.max_retries,
                exclude_patterns=["*.zip", *self.exclude_patterns],
                metadata_level=self._metadata_level(),
            )
            result.files_copied += files
            result.bytes_copied += nbytes
            result.errors.extend(errors)

        file_count, total_size = count_files_and_size(destination)
        if file_count > 0:
            result.files_copied = max(result.files_copied, file_count)
            result.bytes_copied = max(result.bytes_copied, total_size)
            result.verification_passed = result.files_failed == 0
        elif dry_run:
            zips = find_takeout_zips(takeout_path)
            result.files_copied = len(zips)
            result.bytes_copied = sum(z.stat().st_size for z in zips)
            result.verification_passed = len(zips) > 0
            logger.info("[%s] Dry-run: %d Takeout ZIPs to extract", self.name, len(zips))
        else:
            result.verification_passed = False
            result.errors.append(
                f"No Google Photos data in {takeout_path}. "
                "Export at https://takeout.google.com (Google Photos only)."
            )
            result.files_failed += 1

        if not dry_run:
            manifest.save()
        return result
