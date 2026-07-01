"""Interactive first-run setup wizard."""

from __future__ import annotations

import os
from pathlib import Path

import click
import yaml
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .device_detection import detect_android_storage, detect_iphone_dcim, detect_usb_devices
from .utils import (
    detect_google_drive_folder,
    detect_icloud_folder,
    detect_onedrive_folder,
    find_rclone_executable,
)

console = Console()


def run_wizard(config_path: Path, example_path: Path) -> Path:
    console.print(
        Panel.fit(
            "[bold cyan]CloudToHDD Backup Setup Wizard[/bold cyan]\n"
            "Photo Backup from Cloud to Local HDD\n"
            "Consolidate iPhone, Android, Google Photos, Google Drive & OneDrive\n"
            "to your local hard drive.",
            border_style="cyan",
        )
    )

    if not config_path.exists() and example_path.exists():
        config_path.write_text(example_path.read_text(encoding="utf-8"), encoding="utf-8")

    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    backup = config.setdefault("backup", {})
    providers = config.setdefault("providers", {})

    backup["layout"] = "digital_archive"
    backup.setdefault("disk_space_margin", 2.0)
    backup.setdefault("require_100_percent", True)

    # Step 1: Destination (external HDD)
    default_dest = "D:\\CloudToHDD_Backup"
    current_dest = os.path.expandvars(backup.get("destination_root", default_dest))
    console.print("\n[bold]Step 1:[/bold] Backup destination (external HDD)")
    console.print("  Recommended: D:\\CloudToHDD_Backup on NTFS external drive")
    dest = click.prompt("  Destination path", default=current_dest)
    backup["destination_root"] = dest.replace("/", "\\")

    # Step 2: USB devices
    console.print("\n[bold]Step 2:[/bold] USB devices (connect phones now if available)")
    usb_devices = detect_usb_devices()
    table = Table(show_header=True)
    table.add_column("Device")
    table.add_column("Status")
    table.add_column("Path")
    for label, path_fn in [
        ("iPhone (DCIM)", lambda: detect_iphone_dcim()),
        ("Android (MTP)", lambda: detect_android_storage()),
    ]:
        path = path_fn()
        if path:
            table.add_row(label, "[green]Connected[/green]", str(path))
        else:
            table.add_row(label, "[yellow]Not connected[/yellow]", "Connect USB & retry later")
    for dev in usb_devices:
        table.add_row(dev.device_type, dev.name, str(dev.storage_root))
    console.print(table)

    # Step 3: Cloud services
    console.print("\n[bold]Step 3:[/bold] Cloud services detected")
    cloud_table = Table(show_header=True)
    cloud_table.add_column("Service")
    cloud_table.add_column("Status")
    cloud_table.add_column("Path")
    for label, path in [
        ("OneDrive", detect_onedrive_folder()),
        ("Google Drive", detect_google_drive_folder()),
        ("iCloud Drive", detect_icloud_folder()),
        ("rclone", find_rclone_executable()),
    ]:
        if path:
            cloud_table.add_row(label, "[green]Found[/green]", str(path))
        else:
            cloud_table.add_row(label, "[yellow]Not found[/yellow]", "-")
    console.print(cloud_table)

    # Step 3b: Connect cloud accounts in-app
    console.print("\n[bold]Step 3b:[/bold] Connect cloud accounts (optional)")
    console.print("  Sign in to Google, OneDrive, or iCloud from inside this tool.")
    if click.confirm("  Open cloud connection setup now?", default=True):
        from .cloud_connect import connect_online, connect_sync_folder, get_connection_status

        for key, label in [
            ("onedrive", "OneDrive"),
            ("google_drive", "Google Drive"),
            ("google_photos", "Google Photos"),
            ("icloud", "iCloud Drive"),
        ]:
            st = get_connection_status(key, config)
            if st.connected:
                console.print(f"  [green]{label} already connected[/green]")
                continue
            if not click.confirm(f"  Connect {label}?", default=key in ("onedrive", "google_photos")):
                continue
            if key == "google_photos":
                default_takeout = str(Path(dest) / "03_GooglePhotos" / "_downloads")
                takeout = click.prompt("  Takeout download folder", default=default_takeout)
                from .cloud_connect import connect_takeout_folder
                connect_takeout_folder(key, Path(takeout))
            else:
                mode = click.prompt(
                    f"  {label}: [1] Sign in online (browser)  [2] Use desktop sync folder",
                    default="1",
                )
                if mode.strip() == "2":
                    connect_sync_folder(key)
                else:
                    console.print(f"  [dim]Browser will open for {label} sign-in...[/dim]")
                    connect_online(key)

    # Step 4: Enable sources
    console.print("\n[bold]Step 4:[/bold] Enable backup sources")
    source_defaults = {
        "iphone": bool(detect_iphone_dcim()),
        "android": bool(detect_android_storage()),
        "google_photos": True,
        "google_drive": bool(detect_google_drive_folder()) or bool(find_rclone_executable()),
        "onedrive": bool(detect_onedrive_folder()),
        "icloud": False,
    }
    labels = {
        "iphone": "iPhone (USB → 01_iPhone)",
        "android": "Android (USB → 02_Android)",
        "google_photos": "Google Photos (Takeout → 03_GooglePhotos)",
        "google_drive": "Google Drive (→ 04_GoogleDrive)",
        "onedrive": "OneDrive (→ 05_OneDrive)",
        "icloud": "iCloud Drive (optional)",
    }
    for key, label in labels.items():
        cfg = providers.setdefault(key, {})
        enabled = click.confirm(f"  {label}?", default=source_defaults.get(key, False))
        cfg["enabled"] = enabled

    # Step 5: Google Photos Takeout folder
    gp = providers.setdefault("google_photos", {})
    if gp.get("enabled"):
        console.print("\n[bold]Step 5:[/bold] Google Photos Takeout")
        console.print("  Export at [link=https://takeout.google.com]takeout.google.com[/link] (Photos only)")
        default_takeout = str(Path(dest) / "03_GooglePhotos" / "_downloads")
        takeout = click.prompt("  Download folder for Takeout ZIPs", default=default_takeout)
        gp["takeout_download_folder"] = takeout.replace("/", "\\")
        gp["method"] = "takeout"
        gp["auto_extract"] = True

    # Step 6: Safety
    console.print("\n[bold]Step 6:[/bold] Safety settings")
    backup["verify_checksums"] = click.confirm("  Verify every file after copy?", default=True)
    backup["require_100_percent"] = click.confirm("  Require 100% completeness?", default=True)
    backup["disk_space_margin"] = 2.0

    config_path.write_text(yaml.dump(config, default_flow_style=False, sort_keys=False), encoding="utf-8")
    console.print(f"\n[green]Saved {config_path}[/green]")
    console.print("\nNext steps:")
    console.print("  1. python main.py inventory   — scan all source sizes")
    console.print("  2. python main.py run --dry-run — preview")
    console.print("  3. python main.py run           — full backup")
    return config_path
