"""Orchestrates multi-provider cloud backups with verification loop."""

from __future__ import annotations

import json
import logging
import os
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

import yaml
from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn

from .backup_log import export_backup_log
from .digital_archive import ensure_archive_structure
from .exif_gps import save_gps_report, verify_gps_preserved
from .manifest import ManifestStore
from .preflight import run_preflight
from .safety_certification import (
    SafetyCertificate,
    certify_provider,
    save_safety_certificate,
)
from .source_health import scan_source_health
from .providers import (
    AndroidProvider,
    BackupResult,
    GoogleDriveProvider,
    GooglePhotosProvider,
    ICloudProvider,
    IPhoneProvider,
    OneDriveProvider,
)
from .utils import ensure_dir, resolve_destination, utc_now_iso
from .verifier import verify_backup

logger = logging.getLogger("cloudtohdd.engine")

LOCAL_COPY_METHODS = ("sync_folder", "robocopy", "usb_dcim", "usb_mtp")

PROVIDER_MAP = {
    "iphone": IPhoneProvider,
    "android": AndroidProvider,
    "google_photos": GooglePhotosProvider,
    "google_drive": GoogleDriveProvider,
    "onedrive": OneDriveProvider,
    "icloud": ICloudProvider,
}


class BackupEngine:
    def __init__(self, config_path: Path):
        from .cloud_connect import set_config_path

        self.config_path = config_path
        set_config_path(config_path)
        self.config = self._load_config(config_path)
        self.backup_cfg = self.config.get("backup", {})
        self.providers_cfg = self.config.get("providers", {})
        self.safety_cfg = self.config.get("safety") or self.backup_cfg.get("safety") or {}

    @staticmethod
    def _load_config(path: Path) -> dict:
        if not path.exists():
            raise FileNotFoundError(
                f"Config not found: {path}. Run: python main.py wizard"
            )
        with path.open("r", encoding="utf-8") as handle:
            return yaml.safe_load(handle) or {}

    def list_providers(self) -> list[dict]:
        status = []
        for name, provider_cls in PROVIDER_MAP.items():
            cfg = self.providers_cfg.get(name, {})
            provider = provider_cls(cfg, self.config)
            source, method = provider.resolve_source()
            status.append(
                {
                    "name": name,
                    "enabled": cfg.get("enabled", False),
                    "method": method,
                    "source": str(source) if source else None,
                }
            )
        return status

    def _resolve_destination_root(self) -> Path:
        from .utils import resolve_writable_root

        raw = self.backup_cfg.get("destination_root", "./backups")
        fallback = Path(os.path.expandvars("%USERPROFILE%")) / "CloudToHDD_Backup"
        return resolve_writable_root(str(raw), fallback)

    @contextmanager
    def _exclusive_run_lock(self, destination_root: Path):
        """Prevent overlapping backup runs from writing the same archive."""
        lock_path = destination_root / ".backup.lock"
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise RuntimeError(
                f"Another backup appears to be running: {lock_path}. "
                "If no backup is active, remove this lock file and retry."
            ) from exc

        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(f"pid={os.getpid()}\nstarted_at={utc_now_iso()}\n")
            yield
        finally:
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass

    def _get_local_source_path(self, name: str, provider, source, method: str) -> Path | None:
        if method == "usb_dcim" and source:
            return Path(source)
        if method == "usb_mtp" and source:
            return Path(source)
        if method in LOCAL_COPY_METHODS and source:
            return Path(source)
        return None

    def _fallback_locked_iphone_to_icloud(self, enabled_names: list[str]) -> list[str]:
        """Use iCloud Photos when USB iPhone is enabled but not readable."""
        if "iphone" not in enabled_names:
            return enabled_names

        iphone_cfg = self.providers_cfg.get("iphone", {})
        manual = iphone_cfg.get("usb_path", "") or iphone_cfg.get("device_path", "")

        try:
            from .iphone_usb import detect_iphone_usb

            iphone = detect_iphone_usb(str(manual).strip())
        except Exception as exc:
            logger.warning("Could not check iPhone USB status for fallback: %s", exc)
            return enabled_names

        if iphone.is_ready:
            return enabled_names

        icloud_cfg = self.providers_cfg.get("icloud", {})
        if not str(icloud_cfg.get("sync_folder", "")).strip():
            return enabled_names
        icloud_provider = ICloudProvider(icloud_cfg, self.config)
        source, method = icloud_provider.resolve_source()
        local = self._get_local_source_path("icloud", icloud_provider, source, method)
        if not local or not local.exists():
            return enabled_names

        updated = [name for name in enabled_names if name != "iphone"]
        if "icloud" not in updated:
            updated.append("icloud")
        logger.warning(
            "iPhone USB is not readable (%s). Using iCloud Photos folder instead: %s",
            iphone.status,
            local,
        )
        return updated

    def _run_verification_loop(
        self,
        name: str,
        provider,
        source_root: Path,
        destination: Path,
        manifest: ManifestStore,
        result: BackupResult,
        *,
        dry_run: bool,
        verify_checksums: bool,
    ) -> BackupResult:
        rounds = int(self.backup_cfg.get("verification_rounds", 3))
        require_100 = bool(self.backup_cfg.get("require_100_percent", True))
        meta_cfg = self.backup_cfg.get("preserve_metadata", {})
        verify_meta = bool(meta_cfg.get("verify_timestamps", True))
        meta_tol = float(meta_cfg.get("timestamp_tolerance_seconds", 2.0))

        source_files = None
        if hasattr(provider, "_iter_source_files"):
            source_files = provider._iter_source_files(source_root)

        for round_num in range(1, rounds + 1):
            logger.info("[%s] Verification round %d/%d", name, round_num, rounds)

            progress = Progress(
                TextColumn("[bold green]Verifying"),
                BarColumn(),
                TextColumn("{task.completed}/{task.total}"),
                TimeElapsedColumn(),
            )
            with progress:
                task = progress.add_task(name, total=1)

                def _cb(done, total, _rel):
                    progress.update(task, total=total, completed=done)

                report = verify_backup(
                    name,
                    source_root,
                    destination,
                    provider.exclude_patterns,
                    provider.include_paths,
                    verify_checksums=verify_checksums and round_num == rounds,
                    verify_metadata=verify_meta,
                    metadata_tolerance_seconds=meta_tol,
                    source_file_paths=source_files,
                    progress_callback=_cb,
                )

            result.files_verified = report.verified_ok
            result.files_missing = report.missing_count
            result.files_mismatch = report.mismatch_count
            result.completeness_percent = report.completeness_percent
            result.verification_passed = report.passed

            if report.passed:
                logger.info("[%s] 100%% verification passed", name)
                break

            if dry_run:
                break

            logger.warning(
                "[%s] %d issues found — retrying failed files (round %d)",
                name,
                len(report.issues),
                round_num,
            )
            copied, failed, errors = provider.retry_issues(
                source_root,
                destination,
                manifest,
                report.issues,
                dry_run=False,
                verify_checksums=verify_checksums,
            )
            result.files_copied += copied
            result.files_failed += failed
            result.errors.extend(errors)
            manifest.save()

        if require_100 and not result.verification_passed:
            if report.total_source_files == 0:
                result.errors.append(
                    "No source files found on device — unlock phone, enable File Transfer (MTP), "
                    "then run: python main.py android-detect"
                    if name == "android"
                    else "No source files found — check that the source is connected and readable."
                )
            else:
                result.errors.append(
                    f"Backup incomplete: {result.completeness_percent:.2f}% "
                    f"({result.files_missing} missing, {result.files_mismatch} mismatched)"
                )

        return result

    def _run_gps_verification(
        self,
        name: str,
        provider,
        source_root: Path,
        destination: Path,
        result: BackupResult,
        destination_root: Path,
    ) -> BackupResult:
        meta_cfg = self.backup_cfg.get("preserve_metadata", {})
        if not meta_cfg.get("verify_gps", True):
            return result

        tolerance = float(meta_cfg.get("gps_tolerance_meters", 50.0))
        source_files = None
        if hasattr(provider, "_iter_source_files"):
            source_files = provider._iter_source_files(source_root)

        logger.info("[%s] Verifying EXIF GPS in photos...", name)
        progress = Progress(
            TextColumn("[bold magenta]GPS check"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            TimeElapsedColumn(),
        )
        with progress:
            task = progress.add_task(name, total=1)

            def _cb(done, total, _rel):
                progress.update(task, total=total, completed=done)

            gps_report = verify_gps_preserved(
                name,
                source_root,
                destination,
                provider.exclude_patterns,
                provider.include_paths,
                tolerance_meters=tolerance,
                progress_callback=_cb,
                image_paths=source_files,
            )

        result.gps_images_scanned = gps_report.images_scanned
        result.gps_source_with_location = gps_report.source_with_gps
        result.gps_preserved = gps_report.gps_preserved
        result.gps_lost = gps_report.gps_lost_count
        result.gps_preservation_percent = gps_report.preservation_percent
        result.gps_verification_passed = gps_report.passed

        from .digital_archive import ARCHIVE_FOLDERS

        layout = self.backup_cfg.get("layout", "digital_archive")
        if layout == "digital_archive":
            logs_dir = destination_root / ARCHIVE_FOLDERS["logs"]
        else:
            logs_dir = destination_root / "logs"
        gps_path = save_gps_report(gps_report, logs_dir)
        logger.info("[%s] GPS report: %s", name, gps_path)

        if not gps_report.passed:
            require = bool(meta_cfg.get("require_gps_preserved", True))
            lost_sample = ", ".join(f.relative_path for f in gps_report.gps_lost[:5])
            msg = (
                f"GPS location lost in {gps_report.gps_lost_count} photo(s) "
                f"({gps_report.preservation_percent:.1f}% preserved). "
                f"Examples: {lost_sample}"
            )
            result.errors.append(msg)
            if require:
                result.gps_verification_passed = False

        return result

    def run(self, providers: list[str] | None = None, *, skip_preflight: bool = False) -> list[BackupResult]:
        destination_root = self._resolve_destination_root()
        layout = self.backup_cfg.get("layout", "digital_archive")
        dry_run = bool(self.backup_cfg.get("dry_run", False))
        verify = bool(self.backup_cfg.get("verify_checksums", True))
        incremental = bool(self.backup_cfg.get("incremental", True))
        mode = self.backup_cfg.get("mode", "copy")
        disk_margin = float(self.backup_cfg.get("disk_space_margin", 2.0))
        safety_cfg = self.safety_cfg
        detect_ph = bool(safety_cfg.get("detect_cloud_placeholders", True))

        ensure_dir(destination_root)
        with self._exclusive_run_lock(destination_root):
            return self._run_locked(
                providers,
                skip_preflight=skip_preflight,
                destination_root=destination_root,
                layout=layout,
                dry_run=dry_run,
                verify=verify,
                incremental=incremental,
                mode=mode,
                disk_margin=disk_margin,
                safety_cfg=safety_cfg,
                detect_ph=detect_ph,
            )

    def _run_locked(
        self,
        providers: list[str] | None,
        *,
        skip_preflight: bool,
        destination_root: Path,
        layout: str,
        dry_run: bool,
        verify: bool,
        incremental: bool,
        mode: str,
        disk_margin: float,
        safety_cfg: dict,
        detect_ph: bool,
    ) -> list[BackupResult]:
        if layout == "digital_archive":
            ensure_archive_structure(destination_root)
            logger.info("CloudToHDD folder structure ready at %s", destination_root)

        results: list[BackupResult] = []
        selected = providers or list(PROVIDER_MAP.keys())
        enabled_names = [
            name for name in selected
            if self.providers_cfg.get(name, {}).get("enabled", False)
        ]
        enabled_names = self._fallback_locked_iphone_to_icloud(enabled_names)

        if not skip_preflight and not dry_run:
            sources: list[tuple[str, Path, list[str]]] = []
            for name in enabled_names:
                cfg = self.providers_cfg.get(name, {})
                provider = PROVIDER_MAP[name](cfg, self.config)
                source, method = provider.resolve_source()
                local = self._get_local_source_path(name, provider, source, method)
                if local and local.exists():
                    sources.append((name, local, cfg.get("exclude_patterns", [])))

            if sources:
                preflight = run_preflight(destination_root, sources, safety_margin=disk_margin)
                for warning in preflight.warnings:
                    logger.warning(warning)
                if not preflight.ok:
                    for error in preflight.errors:
                        logger.error(error)
                    raise RuntimeError("Pre-flight checks failed. Fix issues and retry.")

                if detect_ph:
                    for pname, plocal, pex in sources:
                        prov = PROVIDER_MAP[pname](self.providers_cfg.get(pname, {}), self.config)
                        pfiles = prov._iter_source_files(plocal) if hasattr(prov, "_iter_source_files") else []
                        if not pfiles:
                            pfiles = [f for f in plocal.rglob("*") if f.is_file()]
                        health = scan_source_health(pname, plocal, pfiles)
                        if health.placeholders:
                            sample = ", ".join(health.placeholders[:5])
                            raise RuntimeError(
                                f"[{pname}] {len(health.placeholders)} cloud-only file(s) not downloaded locally. "
                                f"Run: python main.py pin-request  (then pin-status --watch 120). "
                                f"Or manually: right-click OneDrive -> Always keep on this device. "
                                f"Examples: {sample}"
                            )

        logger.info("Starting backup | dry_run=%s | mode=%s | layout=%s", dry_run, mode, layout)

        if not enabled_names:
            raise RuntimeError(
                "No backup sources enabled. Enable at least one source in config.yaml or the GUI."
            )

        for name in enabled_names:
            cfg = self.providers_cfg.get(name, {})
            provider_cls = PROVIDER_MAP.get(name)
            if not provider_cls:
                logger.warning("Unknown provider: %s", name)
                continue

            destination = resolve_destination(destination_root, name, layout)
            manifest_path = destination_root / ".manifests" / f"{name}.json"
            manifest = ManifestStore(manifest_path)

            provider = provider_cls(cfg, self.config)
            source, method = provider.resolve_source()
            logger.info(
                "Backing up [bold]%s[/bold] via %s from %s -> %s",
                name,
                method,
                source,
                destination,
            )

            result = provider.backup_to(
                destination,
                manifest,
                dry_run=dry_run,
                verify_checksums=verify,
                incremental=incremental and method not in ("rclone", "robocopy", "usb_mtp"),
                mode=mode,
            )

            local_source = self._get_local_source_path(name, provider, source, result.method)
            if (
                local_source
                and local_source.exists()
                and not dry_run
                and result.files_copied > 0
                and result.method != "locked"
            ):
                result = self._run_verification_loop(
                    name,
                    provider,
                    local_source,
                    destination,
                    manifest,
                    result,
                    dry_run=dry_run,
                    verify_checksums=verify,
                )
                result = self._run_gps_verification(
                    name,
                    provider,
                    local_source,
                    destination,
                    result,
                    destination_root,
                )
            elif dry_run and local_source and local_source.exists():
                if hasattr(provider, "_iter_source_files"):
                    source_files = provider._iter_source_files(local_source)
                else:
                    source_files = []
                if not source_files and local_source.is_dir():
                    source_files = list(local_source.rglob("*"))
                    source_files = [f for f in source_files if f.is_file()]
                result.files_copied = len(source_files)
                result.bytes_copied = sum(f.stat().st_size for f in source_files if f.is_file())
                result.completeness_percent = 100.0 if source_files else 0.0
                result.verification_passed = len(source_files) > 0
                logger.info(
                    "[%s] Dry-run: %d files (%.2f GB) would be copied",
                    name,
                    len(source_files),
                    result.bytes_copied / (1024**3) if result.bytes_copied else 0,
                )

            results.append(result)

            level = logging.INFO if result.success else logging.ERROR
            logger.log(
                level,
                "[%s] Done: copied=%d skipped=%d failed=%d verified=%d complete=%.1f%% "
                "gps=%d/%d",
                name,
                result.files_copied,
                result.files_skipped,
                result.files_failed,
                result.files_verified,
                result.completeness_percent,
                result.gps_preserved,
                result.gps_source_with_location,
            )
            for err in result.errors[:10]:
                logger.error("[%s] %s", name, err)

        if not dry_run and bool(safety_cfg.get("auto_certify_after_backup", True)):
            successful = [r.provider for r in results if r.success]
            if successful:
                certificate = self.certify(providers=successful)
                cert_map = {p.provider: p for p in certificate.providers}
            else:
                cert_map = {}
                logger.error(
                    "Skipping safety certification because no provider completed successfully."
                )
            for result in results:
                if not result.success:
                    result.safe_to_delete_source = False
                    continue
                pc = cert_map.get(result.provider)
                if pc:
                    result.safe_to_delete_source = pc.safe_to_delete_source
                    if not pc.safe_to_delete_source:
                        result.errors.append(
                            "NOT certified safe to delete source — see Logs\\SAFETY_CERTIFICATE_LATEST.txt"
                        )

        self._write_report(destination_root, results)
        csv_path, xlsx_path = export_backup_log(results, destination_root)
        logger.info("Backup log: %s", csv_path)
        if xlsx_path:
            logger.info("Excel log: %s", xlsx_path)
        return results

    def verify_only(self, providers: list[str] | None = None) -> list[dict]:
        destination_root = self._resolve_destination_root()
        layout = self.backup_cfg.get("layout", "digital_archive")
        verify = bool(self.backup_cfg.get("verify_checksums", True))
        meta_cfg = self.backup_cfg.get("preserve_metadata", {})
        verify_meta = bool(meta_cfg.get("verify_timestamps", True))
        meta_tol = float(meta_cfg.get("timestamp_tolerance_seconds", 2.0))
        reports = []

        for name in providers or list(PROVIDER_MAP.keys()):
            cfg = self.providers_cfg.get(name, {})
            if not cfg.get("enabled", False):
                continue
            provider = PROVIDER_MAP[name](cfg, self.config)
            source, method = provider.resolve_source()
            local = self._get_local_source_path(name, provider, source, method)
            if not local or not local.exists():
                continue
            destination = resolve_destination(destination_root, name, layout)
            source_files = None
            if hasattr(provider, "_iter_source_files"):
                source_files = provider._iter_source_files(local)
            report = verify_backup(
                name,
                local,
                destination,
                provider.exclude_patterns,
                provider.include_paths,
                verify_checksums=verify,
                verify_metadata=verify_meta,
                metadata_tolerance_seconds=meta_tol,
                source_file_paths=source_files,
            )
            reports.append(
                {
                    "provider": name,
                    "passed": report.passed,
                    "completeness_percent": report.completeness_percent,
                    "verified_ok": report.verified_ok,
                    "total_source_files": report.total_source_files,
                    "issues": len(report.issues),
                }
            )
        return reports

    def verify_gps_only(self, providers: list[str] | None = None) -> list[dict]:
        destination_root = self._resolve_destination_root()
        layout = self.backup_cfg.get("layout", "digital_archive")
        meta_cfg = self.backup_cfg.get("preserve_metadata", {})
        tolerance = float(meta_cfg.get("gps_tolerance_meters", 50.0))
        reports = []

        for name in providers or list(PROVIDER_MAP.keys()):
            cfg = self.providers_cfg.get(name, {})
            if not cfg.get("enabled", False):
                continue
            provider = PROVIDER_MAP[name](cfg, self.config)
            source, method = provider.resolve_source()
            local = self._get_local_source_path(name, provider, source, method)
            if not local or not local.exists():
                continue
            destination = resolve_destination(destination_root, name, layout)
            source_files = None
            if hasattr(provider, "_iter_source_files"):
                source_files = provider._iter_source_files(local)
            gps_report = verify_gps_preserved(
                name,
                local,
                destination,
                provider.exclude_patterns,
                provider.include_paths,
                tolerance_meters=tolerance,
                image_paths=source_files,
            )
            from .digital_archive import ARCHIVE_FOLDERS

            if layout == "digital_archive":
                save_gps_report(gps_report, destination_root / ARCHIVE_FOLDERS["logs"])
            reports.append(
                {
                    "provider": name,
                    "passed": gps_report.passed,
                    "images_scanned": gps_report.images_scanned,
                    "source_with_gps": gps_report.source_with_gps,
                    "gps_preserved": gps_report.gps_preserved,
                    "gps_lost": gps_report.gps_lost_count,
                    "preservation_percent": gps_report.preservation_percent,
                }
            )
        return reports

    def certify(self, providers: list[str] | None = None) -> SafetyCertificate:
        """Run full pre-delete safety certification on completed backups."""
        from .digital_archive import ARCHIVE_FOLDERS

        destination_root = self._resolve_destination_root()
        layout = self.backup_cfg.get("layout", "digital_archive")
        safety_cfg = self.safety_cfg
        meta_cfg = self.backup_cfg.get("preserve_metadata", {})

        certificate = SafetyCertificate(
            timestamp=utc_now_iso(),
            destination_root=str(destination_root),
        )

        for name in providers or list(PROVIDER_MAP.keys()):
            cfg = self.providers_cfg.get(name, {})
            if not cfg.get("enabled", False):
                continue

            provider = PROVIDER_MAP[name](cfg, self.config)
            source, method = provider.resolve_source()
            destination = resolve_destination(destination_root, name, layout)
            local = self._get_local_source_path(name, provider, source, method)
            remote = str(source) if method == "rclone" else None

            logger.info("[bold]Safety certification[/bold]: %s", name)
            pc = certify_provider(
                name,
                provider,
                Path(local) if local else (Path(source) if method == "takeout" and source else None),
                destination,
                method,
                remote,
                verify_checksums=bool(self.backup_cfg.get("verify_checksums", True)),
                verify_metadata=bool(meta_cfg.get("verify_timestamps", True)),
                verify_gps=bool(meta_cfg.get("verify_gps", True)),
                detect_placeholders=bool(safety_cfg.get("detect_cloud_placeholders", True)),
                sample_reads=int(safety_cfg.get("sample_read_count", 20)),
                gps_tolerance=float(meta_cfg.get("gps_tolerance_meters", 50.0)),
                metadata_tolerance=float(meta_cfg.get("timestamp_tolerance_seconds", 2.0)),
            )
            certificate.providers.append(pc)

        logs_dir = destination_root / (
            ARCHIVE_FOLDERS["logs"] if layout == "digital_archive" else "logs"
        )
        save_safety_certificate(certificate, logs_dir)

        if certificate.global_safe_to_delete:
            logger.info("[bold green]CERTIFIED: Safe to delete source (all automated checks passed)[/bold green]")
        else:
            logger.error(
                "[bold red]NOT CERTIFIED — DO NOT delete source until issues are resolved[/bold red]"
            )
            for blocker in certificate.blockers[:10]:
                logger.error("  %s", blocker)

        return certificate

    def _write_report(self, root: Path, results: list[BackupResult]) -> None:
        from .digital_archive import ARCHIVE_FOLDERS

        layout = self.backup_cfg.get("layout", "digital_archive")
        if layout == "digital_archive":
            report_dir = ensure_dir(root / ARCHIVE_FOLDERS["logs"])
        else:
            report_dir = ensure_dir(root / "reports")
        report_path = report_dir / f"backup_report_{utc_now_iso().replace(':', '-')}.json"
        payload = {
            "timestamp": utc_now_iso(),
            "config": str(self.config_path),
            "results": [asdict(r) for r in results],
            "summary": {
                "providers": len(results),
                "total_copied": sum(r.files_copied for r in results),
                "total_skipped": sum(r.files_skipped for r in results),
                "total_failed": sum(r.files_failed for r in results),
                "total_bytes": sum(r.bytes_copied for r in results),
                "all_success": all(r.success for r in results),
                "all_100_percent": all(r.verification_passed for r in results),
                "min_completeness": min((r.completeness_percent for r in results), default=100.0),
                "gps_photos_with_location": sum(r.gps_source_with_location for r in results),
                "gps_preserved": sum(r.gps_preserved for r in results),
                "gps_lost": sum(r.gps_lost for r in results),
                "all_gps_preserved": all(r.gps_verification_passed for r in results),
                "all_safe_to_delete": (
                    all(r.safe_to_delete_source for r in results if r.safe_to_delete_source is not None)
                    if any(r.safe_to_delete_source is not None for r in results)
                    else None
                ),
            },
        }
        report_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        logger.info("Report saved: %s", report_path)
