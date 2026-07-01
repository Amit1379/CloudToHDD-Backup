"""Inventory scanner for Phase 1 — sizes across all sources."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from .device_detection import detect_android_storage, detect_iphone_dcim, detect_usb_devices
from .takeout import count_files_and_size, find_takeout_zips
from .utils import (
    detect_google_drive_folder,
    detect_icloud_folder,
    detect_onedrive_folder,
    utc_now_iso,
)

logger = logging.getLogger("cloudtohdd.inventory")


@dataclass
class InventoryItem:
    source: str
    status: str
    path: str | None
    size_bytes: int
    file_count: int
    notes: str = ""


def _folder_inventory(name: str, path: Path | None, notes: str = "") -> InventoryItem:
    if not path or not path.exists():
        return InventoryItem(name, "not_found", None, 0, 0, notes or "Not detected")
    count, size = count_files_and_size(path)
    return InventoryItem(name, "found", str(path), size, count, notes)


def run_inventory(config: dict) -> list[InventoryItem]:
    providers = config.get("providers", {})
    items: list[InventoryItem] = []

    # iPhone
    iphone_cfg = providers.get("iphone", {})
    iphone_path = detect_iphone_dcim(iphone_cfg.get("usb_path", ""))
    items.append(
        _folder_inventory(
            "iPhone Photos (USB)",
            iphone_path,
            "Connect iPhone, unlock, tap Trust" if not iphone_path else "",
        )
    )

    # Android
    android_cfg = providers.get("android", {})
    android_path = detect_android_storage(android_cfg.get("usb_path", ""))
    items.append(
        _folder_inventory(
            "Android (USB)",
            android_path,
            "Enable File Transfer (MTP)" if not android_path else "",
        )
    )

    # Google Photos
    gp_cfg = providers.get("google_photos", {})
    takeout = gp_cfg.get("takeout_download_folder", "")
    takeout_path = Path(takeout) if takeout else None
    if takeout_path:
        if takeout_path.is_dir():
            zips = find_takeout_zips(takeout_path)
            zip_size = sum(z.stat().st_size for z in zips)
            count, extracted_size = count_files_and_size(takeout_path)
            items.append(
                InventoryItem(
                    "Google Photos (Takeout)",
                    "found" if zips or count else "waiting",
                    str(takeout_path),
                    max(zip_size, extracted_size),
                    len(zips) or count,
                    f"{len(zips)} ZIP archives" if zips else "Download Takeout ZIPs here",
                )
            )
        else:
            items.append(
                InventoryItem(
                    "Google Photos (Takeout)",
                    "configured",
                    str(takeout_path),
                    0,
                    0,
                    "Export at takeout.google.com, save ZIPs to this folder",
                )
            )
    else:
        items.append(
            InventoryItem(
                "Google Photos",
                "not_configured",
                None,
                0,
                0,
                "Export at takeout.google.com",
            )
        )

    # Google Drive
    gdrive_cfg = providers.get("google_drive", {})
    gdrive_path = Path(gdrive_cfg["sync_folder"]) if gdrive_cfg.get("sync_folder") else detect_google_drive_folder()
    items.append(_folder_inventory("Google Drive", gdrive_path))

    # OneDrive
    od_cfg = providers.get("onedrive", {})
    od_path = Path(od_cfg["sync_folder"]) if od_cfg.get("sync_folder") else detect_onedrive_folder()
    items.append(_folder_inventory("OneDrive", od_path))

    # iCloud (bonus)
    icloud_path = detect_icloud_folder()
    items.append(_folder_inventory("iCloud Drive", icloud_path))

    return items


def save_inventory_report(items: list[InventoryItem], destination_root: Path, fallback: Path | None = None) -> Path:
    from .digital_archive import ensure_archive_structure
    from .utils import resolve_writable_root

    root = resolve_writable_root(str(destination_root), fallback or Path("./logs"))
    logs_dir = ensure_archive_structure(root)["logs"]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = logs_dir / f"inventory_{timestamp}.json"
    json_path.write_text(
        json.dumps(
            {"timestamp": utc_now_iso(), "items": [asdict(i) for i in items]},
            indent=2,
        ),
        encoding="utf-8",
    )
    return json_path
