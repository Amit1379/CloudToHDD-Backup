"""Phase 7 backup log — CSV and Excel export."""

from __future__ import annotations

import csv
import logging
from datetime import datetime
from pathlib import Path

from .providers.base import BackupResult
from .utils import ensure_dir

logger = logging.getLogger("cloudtohdd.backup_log")


def _format_gb(num_bytes: int) -> str:
    return f"{num_bytes / (1024**3):.2f}"


def export_backup_log(
    results: list[BackupResult],
    destination_root: Path,
    *,
    inventory_items: list | None = None,
) -> tuple[Path, Path | None]:
    """
    Write Backup_Log.csv and Backup_Log.xlsx (if openpyxl available) to Logs folder.
    Returns (csv_path, xlsx_path_or_none).
    """
    from .digital_archive import ARCHIVE_FOLDERS

    logs_dir = ensure_dir(destination_root / ARCHIVE_FOLDERS["logs"])
    date_str = datetime.now().strftime("%Y-%m-%d")

    rows: list[dict] = []
    for result in results:
        gps_note = "-"
        if result.gps_source_with_location > 0:
            gps_note = (
                f"{result.gps_preserved}/{result.gps_source_with_location} "
                f"({result.gps_preservation_percent:.0f}%)"
            )
        rows.append(
            {
                "Source": result.provider.replace("_", " ").title(),
                "Size_GB": _format_gb(result.bytes_copied),
                "File_Count": result.files_copied,
                "Photos_With_GPS": result.gps_source_with_location or "-",
                "GPS_Preserved": gps_note,
                "Date": date_str,
                "Status": "OK" if result.success else "FAILED",
                "Completeness": f"{result.completeness_percent:.1f}%",
                "Method": result.method,
                "Destination": result.destination,
            }
        )

    if inventory_items:
        for item in inventory_items:
            if any(r["Source"].lower().startswith(item.source.split("(")[0].strip().lower()) for r in rows):
                continue
            rows.append(
                {
                    "Source": item.source,
                    "Size_GB": _format_gb(item.size_bytes),
                    "File_Count": item.file_count,
                    "Photos_With_GPS": "-",
                    "GPS_Preserved": "-",
                    "Date": date_str,
                    "Status": item.status,
                    "Completeness": "-",
                    "Method": "inventory",
                    "Destination": item.path or "-",
                }
            )

    csv_path = logs_dir / "Backup_Log.csv"
    fieldnames = [
        "Source",
        "Size_GB",
        "File_Count",
        "Photos_With_GPS",
        "GPS_Preserved",
        "Date",
        "Status",
        "Completeness",
        "Method",
        "Destination",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    xlsx_path: Path | None = None
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font

        wb = Workbook()
        ws = wb.active
        ws.title = "Backup Log"
        ws.append(fieldnames)
        for cell in ws[1]:
            cell.font = Font(bold=True)
        for row in rows:
            ws.append([row[f] for f in fieldnames])
        for col in ws.columns:
            ws.column_dimensions[col[0].column_letter].width = 18
        xlsx_path = logs_dir / "Backup_Log.xlsx"
        wb.save(xlsx_path)
    except ImportError:
        logger.info("openpyxl not installed — CSV log only at %s", csv_path)

    return csv_path, xlsx_path
