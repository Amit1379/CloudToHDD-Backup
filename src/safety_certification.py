"""Pre-delete safety certification — verify backup before users delete originals."""

from __future__ import annotations

import json
import logging
import random
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .exif_gps import IMAGE_EXTENSIONS, verify_gps_preserved
from .source_health import scan_source_health
from .takeout import find_takeout_zips
from .utils import ensure_dir, utc_now_iso
from .verifier import VerificationReport, verify_backup, verify_rclone

logger = logging.getLogger("cloudtohdd.safety")


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str


@dataclass
class ProviderCertification:
    provider: str
    safe_to_delete_source: bool = False
    checks: list[CheckResult] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    file_verification: VerificationReport | None = None
    source_files: int = 0
    dest_files: int = 0
    source_bytes: int = 0
    dest_bytes: int = 0
    sample_reads_passed: int = 0
    sample_reads_failed: int = 0

    def add_check(self, name: str, passed: bool, detail: str) -> None:
        self.checks.append(CheckResult(name, passed, detail))
        if not passed:
            self.safe_to_delete_source = False

    @property
    def all_checks_passed(self) -> bool:
        return all(c.passed for c in self.checks)


@dataclass
class SafetyCertificate:
    timestamp: str
    destination_root: str
    providers: list[ProviderCertification] = field(default_factory=list)
    global_safe_to_delete: bool = False
    warnings: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)

    def finalize(self) -> None:
        self.blockers.clear()
        self.warnings.clear()
        for p in self.providers:
            self.warnings.extend(p.warnings)
            if not p.all_checks_passed:
                self.blockers.append(f"{p.provider}: one or more checks failed")
            for c in p.checks:
                if not c.passed:
                    self.blockers.append(f"{p.provider}/{c.name}: {c.detail}")
        self.global_safe_to_delete = len(self.blockers) == 0 and bool(self.providers) and all(
            p.safe_to_delete_source for p in self.providers
        )


def _sum_bytes(paths: list[Path]) -> int:
    total = 0
    for p in paths:
        try:
            total += p.stat().st_size
        except OSError:
            pass
    return total


def _sample_read_test(dest_root: Path, file_paths: list[Path], sample_size: int = 20) -> tuple[int, int, list[str]]:
    """Read random files on destination to confirm they are accessible."""
    if not file_paths:
        return 0, 0, []

    sample = file_paths if len(file_paths) <= sample_size else random.sample(file_paths, sample_size)
    passed = 0
    failed = 0
    errors: list[str] = []

    for path in sample:
        try:
            rel = path.relative_to(dest_root).as_posix()
            with path.open("rb") as handle:
                chunk = handle.read(4096)
            if path.stat().st_size > 0 and len(chunk) == 0:
                failed += 1
                errors.append(f"{rel}: empty read")
            else:
                passed += 1
        except OSError as exc:
            failed += 1
            errors.append(f"{path.name}: {exc}")

    return passed, failed, errors


def certify_provider(
    provider_name: str,
    provider,
    source_root: Path | None,
    destination: Path,
    method: str,
    remote_spec: str | None,
    *,
    verify_checksums: bool = True,
    verify_metadata: bool = True,
    verify_gps: bool = True,
    detect_placeholders: bool = True,
    sample_reads: int = 20,
    gps_tolerance: float = 50.0,
    metadata_tolerance: float = 2.0,
) -> ProviderCertification:
    cert = ProviderCertification(provider=provider_name, safe_to_delete_source=True)

    # --- Google Photos Takeout (no live source tree) ---
    if method == "takeout":
        zips = find_takeout_zips(source_root) if source_root else []
        markers = list((destination / ".extracted").glob("*.done")) if destination.exists() else []
        cert.add_check(
            "takeout_zips_present",
            len(zips) > 0 or destination.exists(),
            f"{len(zips)} ZIP(s) in download folder",
        )
        cert.add_check(
            "takeout_extracted",
            len(markers) >= len(zips) if zips else destination.exists(),
            f"{len(markers)}/{len(zips)} archives extracted",
        )
        dest_files = [p for p in destination.rglob("*") if p.is_file() and not p.name.endswith(".done")]
        cert.dest_files = len(dest_files)
        cert.dest_bytes = _sum_bytes(dest_files)
        cert.add_check(
            "destination_has_files",
            cert.dest_files > 0,
            f"{cert.dest_files} files ({cert.dest_bytes / (1024**3):.2f} GB) in backup",
        )
        sp, sf, serr = _sample_read_test(destination, dest_files, sample_reads)
        cert.sample_reads_passed = sp
        cert.sample_reads_failed = sf
        cert.add_check(
            "sample_read_test",
            sf == 0,
            f"{sp}/{sp + sf} random files readable" + (f"; failures: {serr[:3]}" if serr else ""),
        )
        cert.warnings.append(
            "Takeout: confirm export at photos.google.com includes ALL albums before deleting Google Photos"
        )
        cert.safe_to_delete_source = cert.all_checks_passed
        return cert

    # --- rclone cloud API ---
    if method == "rclone" and remote_spec:
        check = verify_rclone(remote_spec, destination, provider_name)
        cert.file_verification = check
        cert.add_check(
            "rclone_checksum_verify",
            check.passed,
            "rclone check --checksum passed" if check.passed else "rclone check failed",
        )
        dest_files = [p for p in destination.rglob("*") if p.is_file()]
        cert.dest_files = len(dest_files)
        cert.dest_bytes = _sum_bytes(dest_files)
        cert.add_check(
            "destination_has_files",
            cert.dest_files > 0,
            f"{cert.dest_files} files in backup",
        )
        cert.warnings.append(
            "Cloud API backup: manually open important documents and photos before deleting online data"
        )
        cert.safe_to_delete_source = cert.all_checks_passed
        return cert

    if not source_root or not source_root.exists():
        cert.safe_to_delete_source = False
        cert.add_check("source_available", False, "Source not available for verification")
        return cert

    # Build file list (respects Android partial folders etc.)
    if hasattr(provider, "_iter_source_files"):
        source_files = provider._iter_source_files(source_root)
    else:
        source_files = [p for p in source_root.rglob("*") if p.is_file()]

    cert.source_files = len(source_files)
    cert.source_bytes = _sum_bytes(source_files)

    # Source health — placeholders mean source isn't fully local
    health = scan_source_health(
        provider_name, source_root, source_files, detect_placeholders=detect_placeholders
    )
    cert.add_check(
        "no_cloud_placeholders",
        health.passed and not health.placeholders,
        "no cloud-only stubs" if not health.placeholders else f"{len(health.placeholders)} placeholder(s)",
    )
    if health.zero_byte_files:
        cert.add_check(
            "zero_byte_review",
            True,
            f"{len(health.zero_byte_files)} zero-byte files (review if unexpected)",
        )

    # Full file-by-file verification with checksums
    file_report = verify_backup(
        provider_name,
        source_root,
        destination,
        provider.exclude_patterns,
        provider.include_paths,
        verify_checksums=verify_checksums,
        verify_metadata=verify_metadata,
        metadata_tolerance_seconds=metadata_tolerance,
        source_file_paths=source_files,
    )
    cert.file_verification = file_report
    cert.add_check(
        "file_count_and_checksum",
        file_report.passed,
        f"{file_report.verified_ok}/{file_report.total_source_files} files verified "
        f"({file_report.completeness_percent:.2f}%)",
    )

    dest_files = []
    for src in source_files:
        try:
            rel = src.relative_to(source_root)
            dest = destination / rel
            if dest.is_file():
                dest_files.append(dest)
        except (OSError, ValueError):
            continue
    cert.dest_files = len(dest_files)
    cert.dest_bytes = _sum_bytes(dest_files)

    cert.add_check(
        "byte_total_match",
        cert.source_bytes == cert.dest_bytes,
        f"source {_fmt(cert.source_bytes)} vs dest {_fmt(cert.dest_bytes)}",
    )

    # GPS
    if verify_gps:
        gps = verify_gps_preserved(
            provider_name,
            source_root,
            destination,
            provider.exclude_patterns,
            provider.include_paths,
            tolerance_meters=gps_tolerance,
            image_paths=[p for p in source_files if p.suffix.lower() in IMAGE_EXTENSIONS],
        )
        if gps.source_with_gps > 0:
            cert.add_check(
                "gps_preserved",
                gps.passed,
                f"{gps.gps_preserved}/{gps.source_with_gps} photos with GPS preserved",
            )

    # Random read test on destination
    sp, sf, serr = _sample_read_test(destination, dest_files, sample_reads)
    cert.sample_reads_passed = sp
    cert.sample_reads_failed = sf
    cert.add_check(
        "sample_read_test",
        sf == 0,
        f"{sp}/{sp + sf} files readable on destination",
    )

    cert.safe_to_delete_source = cert.all_checks_passed
    return cert


def _fmt(num: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num < 1024:
            return f"{num:.1f} {unit}" if unit != "B" else f"{num} B"
        num /= 1024
    return f"{num:.1f} PB"


def save_safety_certificate(certificate: SafetyCertificate, logs_dir: Path) -> Path:
    ensure_dir(logs_dir)
    certificate.finalize()
    path = logs_dir / f"SAFETY_CERTIFICATE_{certificate.timestamp.replace(':', '-')}.json"
    payload = {
        "timestamp": certificate.timestamp,
        "destination_root": certificate.destination_root,
        "global_safe_to_delete": certificate.global_safe_to_delete,
        "verdict": (
            "SAFE TO DELETE SOURCE — all checks passed"
            if certificate.global_safe_to_delete
            else "DO NOT DELETE SOURCE — backup not fully verified"
        ),
        "blockers": certificate.blockers,
        "warnings": certificate.warnings,
        "providers": [
            {
                "provider": p.provider,
                "safe_to_delete_source": p.safe_to_delete_source,
                "source_files": p.source_files,
                "dest_files": p.dest_files,
                "source_bytes": p.source_bytes,
                "dest_bytes": p.dest_bytes,
                "checks": [asdict(c) for c in p.checks],
            }
            for p in certificate.providers
        ],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    # Human-readable summary
    txt_path = logs_dir / "SAFETY_CERTIFICATE_LATEST.txt"
    lines = [
        "=" * 60,
        "CLOUDTOHDD BACKUP SAFETY CERTIFICATE",
        "=" * 60,
        f"Date: {certificate.timestamp}",
        f"Destination: {certificate.destination_root}",
        "",
        payload["verdict"],
        "",
    ]
    if certificate.blockers:
        lines.append("BLOCKERS:")
        for b in certificate.blockers:
            lines.append(f"  ✗ {b}")
        lines.append("")
    for p in certificate.providers:
        status = "SAFE" if p.safe_to_delete_source else "NOT SAFE"
        lines.append(f"[{p.provider}] {status}")
        for c in p.checks:
            mark = "✓" if c.passed else "✗"
            lines.append(f"  {mark} {c.name}: {c.detail}")
        lines.append("")
    lines.extend(
        [
            "RECOMMENDATION:",
            "  1. Keep source data until you manually spot-check photos/documents",
            "  2. Prefer a second copy on another drive before deleting cloud data",
            "  3. Empty cloud trash only after 30 days of successful backup use",
            "=" * 60,
        ]
    )
    txt_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("Safety certificate: %s", path)
    return path
