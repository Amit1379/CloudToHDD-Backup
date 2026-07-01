"""Windows robocopy engine for reliable bulk file copy with full metadata."""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

from .metadata import robocopy_flags
from .utils import ensure_dir

logger = logging.getLogger("cloudtohdd.robocopy")

ROBOCOPY_SUCCESS_MAX = 7


def run_robocopy(
    source: Path,
    destination: Path,
    *,
    dry_run: bool = False,
    max_retries: int = 3,
    threads: int = 8,
    exclude_patterns: list[str] | None = None,
    metadata_level: str = "full",
) -> tuple[bool, int, int, list[str]]:
    """
    Copy source tree to destination using robocopy.
    Preserves data, timestamps, attributes, and NTFS ACLs (full level).
    Returns (success, files_copied_estimate, bytes_copied_estimate, errors).
    """
    ensure_dir(destination)
    exclude_patterns = exclude_patterns or []
    copy_flags = robocopy_flags(metadata_level)

    command = [
        "robocopy",
        str(source),
        str(destination),
        "/E",
        *copy_flags,
        "/R:" + str(max_retries),
        "/W:5",
        f"/MT:{threads}",
        "/XJ",
        "/NP",
        "/BYTES",
        "/NFL",
        "/NDL",
    ]

    if dry_run:
        command.append("/L")

    for pattern in exclude_patterns:
        command.extend(["/XF", pattern])

    logger.info(
        "Running robocopy (%s metadata): %s -> %s",
        metadata_level,
        source,
        destination,
    )

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        return False, 0, 0, [f"Failed to launch robocopy: {exc}"]

    output = completed.stdout + completed.stderr
    files, nbytes = _parse_robocopy_summary(output)

    errors: list[str] = []
    if completed.returncode > ROBOCOPY_SUCCESS_MAX:
        errors.append(f"robocopy exit code {completed.returncode}")
        for line in output.splitlines():
            if "ERROR" in line.upper() and "Manage Auditing" not in line:
                errors.append(line.strip())

    success = completed.returncode <= ROBOCOPY_SUCCESS_MAX
    if (
        not success
        and completed.returncode == 16
        and "Manage Auditing" in output
        and files > 0
    ):
        success = True
        errors.clear()
    if files == 0 and not dry_run and completed.returncode in (0, 1):
        try:
            src_count = sum(1 for p in source.rglob("*") if p.is_file())
        except OSError:
            src_count = -1
        if src_count > 0:
            success = False
            errors.append(f"robocopy copied 0 files but source has ~{src_count} file(s)")
        elif src_count == 0:
            success = True
        else:
            success = False
            errors.append("robocopy copied 0 files and could not read source")

    logger.info(
        "robocopy finished: code=%d files=%d bytes=%d",
        completed.returncode,
        files,
        nbytes,
    )
    return success, files, nbytes, errors


def _parse_robocopy_summary(output: str) -> tuple[int, int]:
    files = 0
    nbytes = 0

    for line in output.splitlines():
        line = line.strip()
        match = re.search(
            r"Files\s*:\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)",
            line,
            re.I,
        )
        if match:
            copied = int(match.group(2))
            total = int(match.group(1))
            files = copied if copied > 0 else total
        match_bytes = re.search(
            r"Bytes\s*:\s*([\d,\.]+)\s+([\d,\.]+)",
            line,
            re.I,
        )
        if match_bytes:
            raw = match_bytes.group(2).replace(",", "").replace(".", "")
            if raw.isdigit():
                nbytes = int(raw)

    return files, nbytes
