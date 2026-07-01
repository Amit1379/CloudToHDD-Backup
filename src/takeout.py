"""Google Takeout ZIP download processing with original timestamps."""

from __future__ import annotations

import logging
import zipfile
from datetime import datetime
from pathlib import Path

from .metadata import apply_metadata, read_metadata

logger = logging.getLogger("cloudtohdd.takeout")


def find_takeout_zips(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return sorted(folder.rglob("*.zip"))


def _extract_zip_preserve_metadata(archive: zipfile.ZipFile, destination: Path) -> None:
    """Extract ZIP members and restore modification times from the archive."""
    for member in archive.infolist():
        if member.is_dir():
            target_dir = destination / member.filename
            target_dir.mkdir(parents=True, exist_ok=True)
            continue

        archive.extract(member, destination)
        extracted = destination / member.filename
        if not extracted.is_file():
            continue

        try:
            dt = datetime(*member.date_time)
            ts = dt.timestamp()
            meta = read_metadata(extracted)
            meta.mtime = ts
            meta.atime = ts
            apply_metadata(extracted, meta)
        except (OSError, ValueError) as exc:
            logger.debug("Could not restore time for %s: %s", member.filename, exc)


def extract_takeout_archives(
    source_folder: Path,
    destination: Path,
    *,
    dry_run: bool = False,
) -> tuple[int, int, list[str]]:
    """
    Extract all Takeout ZIP files into destination.
    Preserves original file dates stored inside each ZIP.
    Returns (extracted_count, total_bytes, errors).
    """
    zips = find_takeout_zips(source_folder)
    if not zips:
        return 0, 0, [f"No ZIP files found in {source_folder}"]

    extracted = 0
    total_bytes = 0
    errors: list[str] = []

    for zip_path in zips:
        try:
            zip_stat = zip_path.stat()
            marker_key = f"{zip_stat.st_mtime_ns}:{zip_stat.st_size}"
        except OSError:
            marker_key = zip_path.name
        marker = destination / ".extracted" / f"{zip_path.name}.done"
        if marker.exists() and marker.read_text(encoding="utf-8").strip() == marker_key:
            logger.info("Already extracted: %s", zip_path.name)
            continue

        if dry_run:
            extracted += 1
            total_bytes += zip_path.stat().st_size
            continue

        try:
            with zipfile.ZipFile(zip_path, "r") as archive:
                _extract_zip_preserve_metadata(archive, destination)
            extracted += 1
            total_bytes += zip_path.stat().st_size
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(marker_key, encoding="utf-8")
            logger.info("Extracted (metadata preserved): %s", zip_path.name)
        except (zipfile.BadZipFile, OSError) as exc:
            errors.append(f"{zip_path.name}: {exc}")

    return extracted, total_bytes, errors


def count_files_and_size(folder: Path) -> tuple[int, int]:
    if not folder.is_dir():
        return 0, 0
    count = 0
    total = 0
    for path in folder.rglob("*"):
        if path.is_file():
            try:
                count += 1
                total += path.stat().st_size
            except OSError:
                continue
    return count, total
