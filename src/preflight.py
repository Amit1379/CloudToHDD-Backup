"""Pre-backup safety checks."""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("cloudtohdd.preflight")


@dataclass
class PreflightResult:
    ok: bool = True
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    free_bytes: int = 0
    required_bytes: int = 0

    def add_error(self, message: str) -> None:
        self.errors.append(message)
        self.ok = False

    def add_warning(self, message: str) -> None:
        self.warnings.append(message)


def estimate_source_size(source_root: Path, exclude_patterns: list[str]) -> int:
    from .utils import should_exclude

    total = 0
    if not source_root.exists():
        return 0
    for path in source_root.rglob("*"):
        if not path.is_file():
            continue
        if should_exclude(path.name, exclude_patterns):
            continue
        try:
            total += path.stat().st_size
        except OSError:
            continue
    return total


def run_preflight(
    destination_root: Path,
    sources: list[tuple[str, Path, list[str]]],
    *,
    safety_margin: float = 2.0,
) -> PreflightResult:
    """Validate destination drive space and write permissions."""
    result = PreflightResult()

    try:
        destination_root.mkdir(parents=True, exist_ok=True)
        probe = destination_root / ".preflight_write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except OSError as exc:
        result.add_error(f"Cannot write to destination: {destination_root} ({exc})")
        return result

    required = 0
    for name, source, excludes in sources:
        if not source.exists():
            result.add_warning(f"[{name}] Source not found: {source}")
            continue
        size = estimate_source_size(source, excludes)
        required += size
        logger.info("[%s] Estimated source size: %.2f GB", name, size / (1024**3))

    result.required_bytes = required

    try:
        usage = shutil.disk_usage(destination_root)
        result.free_bytes = usage.free
    except OSError as exc:
        result.add_error(f"Cannot read disk space for {destination_root}: {exc}")
        return result

    needed = int(required * safety_margin)
    if required > 0 and usage.free < needed:
        result.add_error(
            f"Insufficient disk space. Need ~{_fmt(needed)} free, "
            f"but only {_fmt(usage.free)} available at {destination_root}."
        )
    elif required > 0 and usage.free < required * 1.2:
        result.add_warning(
            f"Low disk space: {_fmt(usage.free)} free for ~{_fmt(required)} of data."
        )

    return result


def _fmt(num: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num < 1024:
            return f"{num:.1f} {unit}" if unit != "B" else f"{num} B"
        num /= 1024
    return f"{num:.1f} PB"
