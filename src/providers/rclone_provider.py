"""rclone integration for direct cloud API backup."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from ..utils import ensure_dir, find_rclone_executable
from .base import BackupResult

logger = logging.getLogger("cloudtohdd.rclone")


def run_rclone_sync(
    remote_spec: str,
    destination: Path,
    result: BackupResult,
    *,
    dry_run: bool,
    mode: str,
    bandwidth_limit_kbps: int,
    exclude_patterns: list[str],
) -> BackupResult:
    rclone = find_rclone_executable()
    if not rclone:
        result.errors.append(
            "rclone not found. Install from https://rclone.org/install/ "
            "or use sync_folder method."
        )
        result.files_failed += 1
        return result

    ensure_dir(destination)
    command = [
        rclone,
        "sync" if mode == "mirror" else "copy",
        remote_spec,
        str(destination),
        "--progress",
        "--stats-one-line",
        "--stats=5s",
        "--transfers=8",
        "--checkers=16",
        "--retries=5",
        "--low-level-retries=20",
        "--checksum",
        "--metadata",
        "--ignore-case",
        "--order-by", "size,ascending",
    ]

    if dry_run:
        command.append("--dry-run")

    if bandwidth_limit_kbps > 0:
        command.extend(["--bwlimit", f"{bandwidth_limit_kbps}K"])

    for pattern in exclude_patterns:
        command.extend(["--exclude", pattern])

    logger.info("[%s] Running rclone: %s", result.provider, " ".join(command))

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        result.errors.append(f"Failed to launch rclone: {exc}")
        result.files_failed += 1
        return result

    if completed.stdout:
        logger.info(completed.stdout.strip())
    if completed.stderr:
        logger.debug(completed.stderr.strip())

    if completed.returncode != 0:
        result.errors.append(completed.stderr.strip() or "rclone exited with error")
        result.files_failed += 1
        return result

    _parse_rclone_stats(completed.stdout + completed.stderr, result)
    return result


def _parse_rclone_stats(output: str, result: BackupResult) -> None:
    for line in output.splitlines():
        lower = line.lower()
        if "transferred:" in lower:
            parts = line.split(",")
            for part in parts:
                part = part.strip()
                if part.startswith("Transferred:"):
                    try:
                        result.files_copied = int(part.split(":")[1].strip().split("/")[0])
                    except (ValueError, IndexError):
                        pass
                if "total size" in part.lower():
                    try:
                        size_str = part.split(":")[1].strip().split()[0]
                        multipliers = {"b": 1, "kib": 1024, "mib": 1024**2, "gib": 1024**3}
                        value, unit = size_str[:-3], size_str[-3:].lower()
                        result.bytes_copied = int(float(value) * multipliers.get(unit, 1))
                    except (ValueError, IndexError):
                        pass
