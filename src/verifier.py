"""Post-backup verification for 100% completeness."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from .metadata import metadata_matches
from .utils import sha256_file, should_exclude

logger = logging.getLogger("cloudtohdd.verifier")


@dataclass
class FileIssue:
    relative_path: str
    issue: str  # missing | size_mismatch | hash_mismatch | metadata_mismatch


@dataclass
class VerificationReport:
    provider: str
    source_root: Path
    destination: Path
    total_source_files: int = 0
    total_dest_files: int = 0
    verified_ok: int = 0
    issues: list[FileIssue] = field(default_factory=list)

    @property
    def missing_count(self) -> int:
        return sum(1 for i in self.issues if i.issue == "missing")

    @property
    def mismatch_count(self) -> int:
        return sum(1 for i in self.issues if i.issue != "missing")

    @property
    def completeness_percent(self) -> float:
        if self.total_source_files == 0:
            return 100.0
        bad = len(self.issues)
        return max(0.0, ((self.total_source_files - bad) / self.total_source_files) * 100.0)

    @property
    def passed(self) -> bool:
        if self.total_source_files == 0:
            return False
        return len(self.issues) == 0


def _index_files(
    root: Path,
    exclude_patterns: list[str],
    include_paths: list[str],
) -> dict[str, tuple[int, float]]:
    indexed: dict[str, tuple[int, float]] = {}
    if include_paths:
        roots = [root / p for p in include_paths]
    else:
        roots = [root]

    for base in roots:
        if not base.exists():
            continue
        if base.is_file():
            rel = base.relative_to(root).as_posix()
            stat = base.stat()
            indexed[rel] = (stat.st_size, stat.st_mtime)
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            if should_exclude(path.name, exclude_patterns):
                continue
            try:
                rel = path.relative_to(root).as_posix()
                stat = path.stat()
                indexed[rel] = (stat.st_size, stat.st_mtime)
            except OSError:
                continue
    return indexed


def verify_backup(
    provider: str,
    source_root: Path,
    destination: Path,
    exclude_patterns: list[str],
    include_paths: list[str] | None = None,
    *,
    verify_checksums: bool = False,
    verify_metadata: bool = True,
    metadata_tolerance_seconds: float = 2.0,
    source_file_paths: list[Path] | None = None,
    progress_callback=None,
) -> VerificationReport:
    include_paths = include_paths or []
    report = VerificationReport(
        provider=provider,
        source_root=source_root,
        destination=destination,
    )

    if source_file_paths is not None:
        source_index: dict[str, tuple[int, float]] = {}
        for path in source_file_paths:
            if not path.is_file():
                continue
            if should_exclude(path.name, exclude_patterns):
                continue
            try:
                rel = path.relative_to(source_root).as_posix()
                st = path.stat()
                source_index[rel] = (st.st_size, st.st_mtime)
            except OSError:
                continue
    else:
        source_index = _index_files(source_root, exclude_patterns, include_paths)
    report.total_source_files = len(source_index)

    dest_index: dict[str, tuple[int, float]] = {}
    if destination.exists():
        for path in destination.rglob("*"):
            if not path.is_file():
                continue
            try:
                rel = path.relative_to(destination).as_posix()
                stat = path.stat()
                dest_index[rel] = (stat.st_size, stat.st_mtime)
            except OSError:
                continue
    report.total_dest_files = len(dest_index)

    items = list(source_index.items())
    for idx, (rel, (src_size, _src_mtime)) in enumerate(items):
        if progress_callback:
            progress_callback(idx + 1, len(items), rel)

        dest_entry = dest_index.get(rel)
        if dest_entry is None:
            report.issues.append(FileIssue(rel, "missing"))
            continue

        dest_size, _ = dest_entry
        if dest_size != src_size:
            report.issues.append(FileIssue(rel, "size_mismatch"))
            continue

        if verify_checksums:
            src_path = source_root / Path(rel)
            dest_path = destination / Path(rel)
            try:
                if sha256_file(src_path) != sha256_file(dest_path):
                    report.issues.append(FileIssue(rel, "hash_mismatch"))
                    continue
            except OSError as exc:
                report.issues.append(FileIssue(rel, f"hash_error: {exc}"))
                continue

        if verify_metadata:
            src_path = source_root / Path(rel)
            dest_path = destination / Path(rel)
            if not metadata_matches(
                src_path,
                dest_path,
                tolerance_seconds=metadata_tolerance_seconds,
            ):
                report.issues.append(FileIssue(rel, "metadata_mismatch"))
                continue

        report.verified_ok += 1

    logger.info(
        "[%s] Verification: %d/%d OK, %d issues (%.2f%% complete)",
        provider,
        report.verified_ok,
        report.total_source_files,
        len(report.issues),
        report.completeness_percent,
    )
    return report


def verify_rclone(remote_spec: str, destination: Path, provider: str) -> VerificationReport:
    """Use rclone check for cloud API backups."""
    import subprocess

    from .utils import find_rclone_executable

    report = VerificationReport(
        provider=provider,
        source_root=Path(remote_spec),
        destination=destination,
    )

    rclone = find_rclone_executable()
    if not rclone:
        report.issues.append(FileIssue(".", "missing_rclone"))
        return report

    command = [rclone, "check", remote_spec, str(destination), "--checksum", "--one-way"]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)

    if completed.returncode == 0:
        report.verified_ok = 1
        report.total_source_files = 1
        return report

    for line in (completed.stdout + completed.stderr).splitlines():
        line = line.strip()
        if not line or line.startswith("ERROR"):
            if "not in" in line.lower() or "differ" in line.lower():
                report.issues.append(FileIssue(line[:120], "rclone_check"))
    if not report.issues:
        report.issues.append(FileIssue("rclone check failed", "rclone_check"))
    report.total_source_files = max(len(report.issues), 1)
    return report
