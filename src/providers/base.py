"""Base provider interface and shared copy logic."""

from __future__ import annotations

import logging
import platform
import shutil
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from ..manifest import ManifestStore
from ..metadata import copy_file_preserve_metadata
from ..robocopy_provider import run_robocopy
from ..utils import ensure_dir, should_exclude, sha256_file

logger = logging.getLogger("cloudtohdd.provider")


@dataclass
class BackupResult:
    provider: str
    method: str
    destination: str
    files_copied: int = 0
    files_skipped: int = 0
    files_failed: int = 0
    bytes_copied: int = 0
    files_verified: int = 0
    files_missing: int = 0
    files_mismatch: int = 0
    completeness_percent: float = 100.0
    verification_passed: bool = True
    gps_images_scanned: int = 0
    gps_source_with_location: int = 0
    gps_preserved: int = 0
    gps_lost: int = 0
    gps_preservation_percent: float = 100.0
    gps_verification_passed: bool = True
    safe_to_delete_source: bool | None = None
    errors: list[str] = field(default_factory=list)
    dry_run: bool = False

    @property
    def success(self) -> bool:
        if self.dry_run:
            return self.files_failed == 0
        if self.files_failed > 0:
            return False
        if self.files_copied == 0 and self.files_skipped == 0:
            return False
        if not self.verification_passed:
            return False
        if not self.gps_verification_passed:
            return False
        if self.safe_to_delete_source is False:
            return False
        return True


class BackupProvider(ABC):
    name: str = "base"

    def __init__(self, config: dict, global_config: dict):
        self.config = config
        self.global_config = global_config
        self.exclude_patterns = config.get("exclude_patterns", [])
        self.include_paths = config.get("include_paths", [])
        self.backup_cfg = global_config.get("backup", {})
        self.max_retries = self.backup_cfg.get("max_retries", 3)
        self.use_robocopy = self.backup_cfg.get("use_robocopy", True)
        meta_cfg = self.backup_cfg.get("preserve_metadata", {})
        self.preserve_metadata = meta_cfg.get("enabled", True)
        self.metadata_level = meta_cfg.get("level", "full")

    def _metadata_level(self) -> str:
        if not self.preserve_metadata:
            return "compatible"
        return self.metadata_level

    @abstractmethod
    def resolve_source(self) -> tuple[Path | str | None, str]:
        """Return (source, method_used). Source may be Path or rclone remote spec."""

    def _iter_source_files(self, source_root: Path) -> list[Path]:
        if self.include_paths:
            roots = [source_root / p for p in self.include_paths]
        else:
            roots = [source_root]

        files: list[Path] = []
        for root in roots:
            if not root.exists():
                logger.warning("[%s] Include path not found: %s", self.name, root)
                continue
            if root.is_file():
                files.append(root)
                continue
            for path in root.rglob("*"):
                if not path.is_file():
                    continue
                if should_exclude(path.name, self.exclude_patterns):
                    continue
                files.append(path)
        return files

    def _copy_file(
        self,
        source: Path,
        destination: Path,
        dry_run: bool,
        verify_checksums: bool,
    ) -> tuple[bool, int, str | None]:
        ensure_dir(destination.parent)
        size = source.stat().st_size

        if dry_run:
            return True, size, None

        last_error: str | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                if self.preserve_metadata:
                    copy_file_preserve_metadata(source, destination)
                else:
                    shutil.copy2(source, destination)
                if verify_checksums:
                    if sha256_file(source) != sha256_file(destination):
                        raise IOError("Checksum mismatch after copy")
                return True, size, None
            except OSError as exc:
                last_error = str(exc)
                logger.warning(
                    "[%s] Copy failed (attempt %d/%d): %s -> %s (%s)",
                    self.name,
                    attempt,
                    self.max_retries,
                    source,
                    destination,
                    exc,
                )
                time.sleep(min(2 ** attempt, 10))

        return False, 0, last_error

    def _copy_specific_files(
        self,
        source_root: Path,
        destination: Path,
        relative_paths: list[str],
        *,
        dry_run: bool,
        verify_checksums: bool,
        manifest: ManifestStore,
    ) -> tuple[int, int, list[str]]:
        copied = 0
        failed = 0
        errors: list[str] = []

        for rel in relative_paths:
            src = source_root / Path(rel)
            dest = destination / Path(rel)
            if not src.is_file():
                failed += 1
                errors.append(f"{rel}: source missing")
                continue
            stat = src.stat()
            ok, nbytes, err = self._copy_file(src, dest, dry_run, verify_checksums)
            if ok:
                copied += 1
                if not dry_run:
                    manifest.update_record(
                        rel.replace("\\", "/"),
                        stat.st_size,
                        stat.st_mtime,
                        src,
                        compute_hash=verify_checksums,
                    )
            else:
                failed += 1
                if err:
                    errors.append(f"{rel}: {err}")
        return copied, failed, errors

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
            result.errors.append(f"No source available for {self.name}")
            result.files_failed += 1
            result.verification_passed = False
            return result

        if method == "rclone":
            return self._backup_via_rclone(
                str(source),
                destination,
                result,
                dry_run=dry_run,
                mode=mode,
            )

        source_root = Path(source)
        if not source_root.exists():
            result.errors.append(f"Source path does not exist: {source_root}")
            result.files_failed += 1
            result.verification_passed = False
            return result

        # Use robocopy on Windows for bulk reliable copy with full metadata
        if (
            platform.system() == "Windows"
            and self.use_robocopy
            and not self.include_paths
            and method in ("sync_folder", "usb_dcim")
        ):
            ok, files, nbytes, errors = run_robocopy(
                source_root,
                destination,
                dry_run=dry_run,
                max_retries=self.max_retries,
                exclude_patterns=self.exclude_patterns,
                metadata_level=self._metadata_level(),
            )
            result.method = "robocopy"
            result.files_copied = files
            result.bytes_copied = nbytes
            result.errors.extend(errors)
            if not ok and errors:
                result.files_failed += max(1, len(errors))
            if not dry_run and ok:
                manifest.save()
            return result

        files = self._iter_source_files(source_root)
        logger.info("[%s] Found %d files to process via %s", self.name, len(files), method)

        from rich.progress import (
            BarColumn,
            MofNCompleteColumn,
            Progress,
            TextColumn,
            TimeElapsedColumn,
        )

        progress = Progress(
            TextColumn("[bold blue]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            transient=False,
        )

        with progress:
            task = progress.add_task(f"[{self.name}] Copying files", total=len(files))
            for source_file in files:
                rel = source_file.relative_to(source_root).as_posix()
                dest_file = destination / rel
                stat = source_file.stat()

                if incremental and not manifest.needs_backup(
                    rel,
                    stat.st_size,
                    stat.st_mtime,
                    verify_checksums,
                    source_file,
                ):
                    result.files_skipped += 1
                    progress.advance(task)
                    continue

                ok, nbytes, err = self._copy_file(
                    source_file,
                    dest_file,
                    dry_run=dry_run,
                    verify_checksums=verify_checksums,
                )
                if ok:
                    result.files_copied += 1
                    result.bytes_copied += nbytes
                    if not dry_run:
                        manifest.update_record(
                            rel,
                            stat.st_size,
                            stat.st_mtime,
                            source_file,
                            compute_hash=verify_checksums,
                        )
                else:
                    result.files_failed += 1
                    if err:
                        result.errors.append(f"{rel}: {err}")
                progress.advance(task)

        if mode == "mirror" and not dry_run:
            self._mirror_cleanup(source_root, destination, manifest, result)

        if not dry_run:
            manifest.save()

        return result

    def retry_issues(
        self,
        source_root: Path,
        destination: Path,
        manifest: ManifestStore,
        issues: list,
        *,
        dry_run: bool,
        verify_checksums: bool,
    ) -> tuple[int, int, list[str]]:
        rels = [issue.relative_path for issue in issues]
        return self._copy_specific_files(
            source_root,
            destination,
            rels,
            dry_run=dry_run,
            verify_checksums=verify_checksums,
            manifest=manifest,
        )

    def _mirror_cleanup(
        self,
        source_root: Path,
        destination: Path,
        manifest: ManifestStore,
        result: BackupResult,
    ) -> None:
        source_rels = {
            p.relative_to(source_root).as_posix()
            for p in self._iter_source_files(source_root)
        }
        for dest_file in destination.rglob("*"):
            if not dest_file.is_file():
                continue
            rel = dest_file.relative_to(destination).as_posix()
            if rel not in source_rels:
                try:
                    dest_file.unlink()
                    manifest.records.pop(rel, None)
                    logger.info("[%s] Mirror removed: %s", self.name, rel)
                except OSError as exc:
                    result.files_failed += 1
                    result.errors.append(f"Failed to remove {rel}: {exc}")

    def _backup_via_rclone(
        self,
        remote_spec: str,
        destination: Path,
        result: BackupResult,
        *,
        dry_run: bool,
        mode: str,
    ) -> BackupResult:
        from .rclone_provider import run_rclone_sync

        result = run_rclone_sync(
            remote_spec=remote_spec,
            destination=destination,
            result=result,
            dry_run=dry_run,
            mode=mode,
            bandwidth_limit_kbps=self.backup_cfg.get("bandwidth_limit_kbps", 0),
            exclude_patterns=self.exclude_patterns,
        )
        if not dry_run and result.files_failed == 0:
            from ..verifier import verify_rclone

            check = verify_rclone(remote_spec, destination, self.name)
            result.files_verified = check.verified_ok
            result.files_missing = check.missing_count
            result.files_mismatch = check.mismatch_count
            result.completeness_percent = check.completeness_percent
            result.verification_passed = check.passed
        return result
