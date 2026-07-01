#!/usr/bin/env python3
"""CloudToHDD Backup — photo backup from cloud and phones to local HDD."""

from __future__ import annotations

import sys
from pathlib import Path

import click
import yaml
from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn
from rich.table import Table

from src.backup_engine import BackupEngine
from src.device_detection import (
    detect_android_storage,
    detect_iphone_dcim,
    detect_usb_devices,
    get_android_detection,
    get_iphone_detection,
)
from src.inventory import run_inventory, save_inventory_report
from src.logger import setup_logging
from src.utils import (
    detect_google_drive_folder,
    detect_icloud_folder,
    detect_onedrive_folder,
    find_rclone_executable,
    resolve_writable_root,
)
from src.cloud_pin_status import (
    PIN_INSTRUCTIONS,
    open_folder_in_explorer,
    request_pin_all_windows,
    resolve_cloud_folder,
    save_pin_report,
    scan_cloud_pin_progress,
)
from src.cloud_connect import (
    CLOUD_SERVICES,
    connect_online,
    connect_sync_folder,
    connect_takeout_folder,
    get_all_connection_statuses,
)

from src.wizard import run_wizard
from src.paths import app_root, config_path, ensure_app_config, example_config_path

console = Console()
ROOT = app_root()
DEFAULT_CONFIG = config_path()
EXAMPLE_CONFIG = example_config_path()

ALL_PROVIDERS = [
    "iphone",
    "android",
    "google_photos",
    "google_drive",
    "onedrive",
    "icloud",
]


def _format_bytes(num: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num < 1024:
            return f"{num:.1f} {unit}" if unit != "B" else f"{num} {unit}"
        num /= 1024
    return f"{num:.1f} PB"


def _load_config() -> dict:
    if DEFAULT_CONFIG.exists():
        return yaml.safe_load(DEFAULT_CONFIG.read_text(encoding="utf-8")) or {}
    return {}


def _setup_log(config: dict) -> None:
    log_cfg = config.get("logging", {})
    setup_logging(
        log_cfg.get("level", "INFO"),
        Path(log_cfg.get("log_dir", "./logs")),
        log_cfg.get("keep_days", 30),
    )


def _print_summary(results) -> None:
    table = Table(title="Backup Summary")
    table.add_column("Source")
    table.add_column("Method")
    table.add_column("Copied")
    table.add_column("Skipped")
    table.add_column("Failed")
    table.add_column("Complete")
    table.add_column("Size")
    table.add_column("Status")

    for result in results:
        if result.success:
            status = "[green]OK[/green]"
        elif result.files_failed > 0:
            status = "[red]FAILED[/red]"
        elif not result.verification_passed:
            status = "[red]INCOMPLETE[/red]"
        else:
            status = f"[yellow]{result.completeness_percent:.1f}%[/yellow]"
        table.add_row(
            result.provider,
            result.method,
            str(result.files_copied),
            str(result.files_skipped),
            str(result.files_failed),
            f"{result.completeness_percent:.1f}%",
            _format_bytes(result.bytes_copied),
            status,
        )
    console.print(table)


def _print_gps_summary(results) -> None:
    gps_rows = [r for r in results if r.gps_images_scanned > 0 or r.gps_source_with_location > 0]
    if not gps_rows:
        return

    table = Table(title="Photo Location (EXIF GPS) Check")
    table.add_column("Source")
    table.add_column("Photos scanned")
    table.add_column("Had GPS")
    table.add_column("GPS preserved")
    table.add_column("GPS lost")
    table.add_column("Status")

    for result in gps_rows:
        if result.gps_verification_passed:
            status = "[green]OK[/green]"
        elif result.gps_source_with_location == 0:
            status = "[dim]no GPS data[/dim]"
        else:
            status = "[red]LOST[/red]"
        table.add_row(
            result.provider,
            str(result.gps_images_scanned),
            str(result.gps_source_with_location),
            f"{result.gps_preserved} ({result.gps_preservation_percent:.0f}%)",
            str(result.gps_lost),
            status,
        )
    console.print(table)


def _do_certify() -> None:
    engine = BackupEngine(DEFAULT_CONFIG)
    _setup_log(engine.config)
    console.print("[bold]Running pre-delete safety certification...[/bold]")
    console.print("This checks every file, checksums, GPS, and cloud placeholders.\n")
    cert = engine.certify()
    table = Table(title="Safety Certificate — Before Deleting Source")
    table.add_column("Source")
    table.add_column("Safe to delete?")
    table.add_column("Files")
    table.add_column("Key checks")
    for p in cert.providers:
        safe = "[green]YES[/green]" if p.safe_to_delete_source else "[red]NO[/red]"
        checks = "; ".join(
            f"{'✓' if c.passed else '✗'} {c.name}" for c in p.checks[:4]
        )
        table.add_row(
            p.provider,
            safe,
            f"{p.dest_files} backed up",
            checks,
        )
    console.print(table)
    if cert.global_safe_to_delete:
        console.print(
            "\n[bold green]CERTIFIED[/bold green] — automated checks passed.\n"
            "Still recommended: spot-check photos/docs and keep a second copy 30 days."
        )
    else:
        console.print(
            "\n[bold red]NOT CERTIFIED — DO NOT DELETE SOURCE[/bold red]\n"
            "See Logs\\SAFETY_CERTIFICATE_LATEST.txt for details."
        )
        for b in cert.blockers[:8]:
            console.print(f"  [red]•[/red] {b}")


def _print_pin_report(report, *, report_path: Path | None = None) -> None:
    table = Table(title=f"{report.service.replace('_', ' ').title()} — Local Download Status")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    table.add_row("Folder", str(report.source_path))
    table.add_row("Files local", f"{report.local_files:,} / {report.total_files:,}")
    table.add_row("Files cloud-only", f"{report.cloud_files:,}")
    table.add_row("Files unreadable", f"{report.unreadable_files:,}")
    table.add_row("Local (by count)", f"{report.percent_files_local:.1f}%")
    table.add_row("Data local", _format_bytes(report.local_bytes))
    table.add_row("Data cloud-only", _format_bytes(report.cloud_bytes))
    table.add_row("Local (by size)", f"{report.percent_bytes_local:.1f}%")
    table.add_row("Scan time", f"{report.scan_seconds:.1f}s")
    if report.ready_for_backup:
        table.add_row("Ready for backup", "[green]YES[/green]")
    else:
        table.add_row("Ready for backup", "[red]NO[/red]")
    console.print(table)

    if report.cloud_examples:
        console.print("\n[dim]Cloud-only examples:[/dim]")
        for ex in report.cloud_examples[:8]:
            console.print(f"  - {ex}")

    if report.ready_for_backup:
        console.print("\n[bold green]All files are local — you can run the backup now.[/bold green]")
    else:
        console.print("\n[bold yellow]Action required before backup:[/bold yellow]")
        console.print(PIN_INSTRUCTIONS)

    if report_path:
        console.print(f"\nReport saved: {report_path}")


def _scan_with_progress(folder: Path, service: str, exclude: list[str]):
    progress = Progress(
        TextColumn("[bold cyan]Scanning"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
    )
    with progress:
        task = progress.add_task(service, total=1)

        def _cb(done, total, _path):
            progress.update(task, total=total, completed=done)

        return scan_cloud_pin_progress(
            folder, service=service, exclude_patterns=exclude, progress_callback=_cb
        )


def _do_pin_status(
    *,
    service: str = "onedrive",
    watch_seconds: int = 0,
    open_folder: bool = False,
) -> None:
    config = _load_config()
    folder = resolve_cloud_folder(service, config)
    if not folder:
        console.print(f"[red]No {service} folder found. Connect sync app or set sync_folder in config.yaml.[/red]")
        return

    exclude = config.get("providers", {}).get(service, {}).get("exclude_patterns", [])
    import os

    dest = resolve_writable_root(
        config.get("backup", {}).get("destination_root", "."),
        Path(os.environ.get("USERPROFILE", ".")) / "CloudToHDD_Backup",
    )
    logs_dir = dest / "Logs"

    if open_folder:
        open_folder_in_explorer(folder)
        console.print(f"[cyan]Opened[/cyan] {folder}")

    while True:
        console.print(f"\n[bold]Scanning {folder}...[/bold]")
        report = _scan_with_progress(folder, service, exclude)
        report_path = save_pin_report(report, logs_dir)
        _print_pin_report(report, report_path=report_path)

        if report.ready_for_backup or watch_seconds <= 0:
            break

        console.print(
            f"\n[dim]Waiting {watch_seconds}s — pin files locally, then rescanning... "
            f"(Ctrl+C to stop)[/dim]"
        )
        try:
            import time

            time.sleep(watch_seconds)
        except KeyboardInterrupt:
            console.print("\n[yellow]Watch stopped.[/yellow]")
            break


def _print_connection_status(config: dict | None = None) -> None:
    table = Table(title="Cloud Account Connections")
    table.add_column("Service")
    table.add_column("Connected")
    table.add_column("Method")
    table.add_column("Details")
    for st in get_all_connection_statuses(config):
        if st.connected:
            conn = "[green]Yes[/green]"
        else:
            conn = "[red]No[/red]"
        table.add_row(st.label, conn, st.method, st.detail[:70])
    console.print(table)


def _do_connect(provider: str | None = None, *, mode: str | None = None) -> None:
    """Interactive or direct cloud account connection."""
    config = _load_config()

    if provider is None:
        _print_connection_status(config)
        console.print(
            "\n[bold]Connect cloud accounts[/bold] — sign in without leaving the tool.\n"
            "  [cyan]python main.py connect onedrive --online[/cyan]     Sign in with Microsoft\n"
            "  [cyan]python main.py connect google-drive --online[/cyan] Sign in with Google\n"
            "  [cyan]python main.py connect icloud --online[/cyan]      Sign in with Apple ID\n"
            "  [cyan]python main.py connect onedrive --sync[/cyan]      Use OneDrive desktop folder\n"
            "  [cyan]python main.py gui[/cyan]                          Open GUI → Connect Cloud Accounts\n"
        )
        console.print("[bold]Quick connect:[/bold]")
        for key, spec in CLOUD_SERVICES.items():
            console.print(f"  {key:16} {spec.label}")
        choice = console.input("\nService name (or Enter to cancel): ").strip().lower().replace("-", "_")
        if not choice:
            return
        if choice not in CLOUD_SERVICES:
            console.print("[red]Unknown service.[/red]")
            return
        provider = choice
        if mode is None:
            console.print("\n  [1] Sign in online (browser) — recommended")
            console.print("  [2] Use desktop sync folder")
            if provider == "google_photos":
                console.print("  [3] Set Google Takeout download folder")
            pick = console.input("Choice [1]: ").strip() or "1"
            mode = {"1": "online", "2": "sync", "3": "takeout"}.get(pick, "online")

    provider = provider.replace("-", "_")
    if provider not in CLOUD_SERVICES:
        console.print(f"[red]Unknown provider: {provider}[/red]")
        return

    spec = CLOUD_SERVICES[provider]
    mode = mode or "online"

    if mode == "online":
        console.print(f"\n[bold]Opening browser to sign in to {spec.label}...[/bold]")
        console.print("[dim]Complete sign-in in your browser, then wait for this window.[/dim]\n")
        result = connect_online(provider)
    elif mode == "takeout":
        folder = click.prompt("Takeout download folder", default=str(Path(
            config.get("backup", {}).get("destination_root", "D:\\CloudToHDD_Backup")
        ) / "03_GooglePhotos" / "_downloads"))
        result = connect_takeout_folder(provider, Path(folder))
    else:
        detected = spec.detect_folder()
        default = str(detected) if detected else ""
        if default:
            console.print(f"Detected folder: {default}")
        folder = click.prompt("Sync folder path (Enter for auto-detect)", default=default, show_default=bool(default))
        result = connect_sync_folder(provider, Path(folder) if folder else None)

    if result.success:
        console.print(f"\n[green]{result.message}[/green]")
        _print_connection_status()
    else:
        console.print(f"\n[yellow]{result.message}[/yellow]")
        if result.needs_user_action:
            console.print("Complete the steps above, then run: [cyan]python main.py connect --status[/cyan]")


def _interactive_menu() -> None:
    console.print(
        Panel.fit(
            "[bold cyan]CloudToHDD Backup[/bold cyan]\n"
            "Photo Backup from Cloud to Local HDD",
            border_style="cyan",
        )
    )

    if not DEFAULT_CONFIG.exists():
        console.print("[yellow]First run — starting setup wizard...[/yellow]\n")
        run_wizard(DEFAULT_CONFIG, EXAMPLE_CONFIG)

    options = {
        "1": ("Run full backup", "run"),
        "2": ("Preview backup (dry-run)", "dry-run"),
        "3": ("Inventory all sources (Phase 1)", "inventory"),
        "4": ("Verify existing backup", "verify"),
        "5": ("Verify photo GPS / location", "verify-gps"),
        "6": ("Safety certificate (before delete)", "certify"),
        "7": ("Show status", "status"),
        "8": ("Detect USB & cloud devices", "detect"),
        "9": ("Setup wizard", "wizard"),
        "10": ("OneDrive / Drive download status", "pin-status"),
        "11": ("Connect Google / OneDrive / iCloud", "connect"),
        "0": ("Exit", "exit"),
    }

    while True:
        console.print()
        for key, (label, _) in options.items():
            console.print(f"  [bold]{key}[/bold]. {label}")
        choice = console.input("\n[cyan]Choose an option[/cyan] [1]: ").strip() or "1"
        action = options.get(choice, (None, None))[1]

        if action == "exit":
            break
        if action == "run":
            _do_run(dry_run=False)
        elif action == "dry-run":
            _do_run(dry_run=True)
        elif action == "inventory":
            _do_inventory()
        elif action == "verify":
            _do_verify()
        elif action == "verify-gps":
            _do_verify_gps()
        elif action == "certify":
            _do_certify()
        elif action == "status":
            _do_status()
        elif action == "detect":
            _do_detect()
        elif action == "wizard":
            run_wizard(DEFAULT_CONFIG, EXAMPLE_CONFIG)
        elif action == "pin-status":
            _do_pin_status()
        elif action == "connect":
            _do_connect()
        else:
            console.print("[red]Invalid choice.[/red]")


def _iphone_detect_row() -> tuple[str, str]:
    """Return (display_path, status_hint) for detect table."""
    detection = get_iphone_detection()
    if detection.is_ready:
        return (
            f"{detection.dcim_path} ({detection.dcim_file_count} files)",
            "ready",
        )
    if detection.is_connected:
        return (detection.message[:80], detection.status)
    return ("-", "not_found")


def _do_detect() -> None:
    table = Table(title="Device & Cloud Detection")
    table.add_column("Source", style="cyan")
    table.add_column("Path / Status")
    table.add_column("Status")

    iphone_path, iphone_hint = _iphone_detect_row()
    if iphone_hint == "ready":
        iphone_status = "[green]Ready[/green]"
    elif iphone_hint in ("connected_locked", "connected_no_dcim"):
        iphone_status = "[yellow]Connected — unlock & Trust[/yellow]"
    else:
        iphone_status = "[yellow]Not found[/yellow]"

    table.add_row("iPhone USB (DCIM)", iphone_path, iphone_status)

    android_det = get_android_detection()
    android_path = android_det.storage_path if android_det.is_ready else "-"
    if android_det.is_ready:
        android_status = "[green]Ready[/green]"
    elif android_det.is_connected:
        android_status = "[yellow]Connected — unlock & MTP[/yellow]"
    else:
        android_status = "[yellow]Not found[/yellow]"
    table.add_row("Android USB (MTP)", android_path, android_status)

    other_detections = [
        ("OneDrive", detect_onedrive_folder()),
        ("Google Drive", detect_google_drive_folder()),
        ("iCloud Drive", detect_icloud_folder()),
        ("rclone", find_rclone_executable()),
    ]
    for name, path in other_detections:
        if path:
            table.add_row(name, str(path), "[green]Found[/green]")
        else:
            table.add_row(name, "-", "[yellow]Not found[/yellow]")
    console.print(table)

    devices = detect_usb_devices()
    ready = [d for d in devices if d.dcim_path]
    if ready:
        console.print(f"\n[green]{len(ready)} USB device(s) ready for backup.[/green]")
    elif any(d.device_type == "iphone" for d in devices):
        console.print(
            "\n[yellow]iPhone is connected but not ready — unlock, tap Trust, then re-run detect.[/yellow]"
        )
    elif android_det.is_connected and not android_det.is_ready:
        console.print(
            "\n[yellow]Android is connected but not ready — unlock, enable File Transfer (MTP), "
            "then run: python main.py android-detect[/yellow]"
        )


def _do_inventory() -> None:
    config = _load_config()
    if not config:
        console.print("[red]No config.yaml — run wizard first.[/red]")
        return
    items = run_inventory(config)
    table = Table(title="Phase 1 — Inventory")
    table.add_column("Source")
    table.add_column("Status")
    table.add_column("Size")
    table.add_column("Files")
    table.add_column("Notes")
    total_size = 0
    for item in items:
        total_size += item.size_bytes
        table.add_row(
            item.source,
            item.status,
            _format_bytes(item.size_bytes),
            str(item.file_count),
            item.notes,
        )
    console.print(table)
    console.print(f"\n[bold]Total estimated:[/bold] {_format_bytes(total_size)}")

    dest = config.get("backup", {}).get("destination_root", "D:\\CloudToHDD_Backup")
    report_path = save_inventory_report(items, Path(dest), fallback=ROOT / "logs")
    console.print(f"Report saved: {report_path}")


def _do_status() -> None:
    engine = BackupEngine(DEFAULT_CONFIG)
    config = _load_config()
    table = Table(title="Provider Status")
    table.add_column("Source")
    table.add_column("Enabled")
    table.add_column("Connected")
    table.add_column("Method")
    table.add_column("Source Path")

    cloud_status = {s.provider: s for s in get_all_connection_statuses(config)}
    folder_map = {
        "iphone": "01_iPhone",
        "android": "02_Android",
        "google_photos": "03_GooglePhotos",
        "google_drive": "04_GoogleDrive",
        "onedrive": "05_OneDrive",
        "icloud": "(iCloud)",
    }
    for item in engine.list_providers():
        name = item["name"]
        if name in cloud_status:
            st = cloud_status[name]
            conn = "[green]Yes[/green]" if st.connected else "[red]No — run: python main.py connect[/red]"
        else:
            conn = "[green]Yes[/green]" if item["source"] else "[yellow]N/A[/yellow]"
        table.add_row(
            name,
            "Yes" if item["enabled"] else "No",
            conn,
            item["method"],
            item["source"] or "[red]unavailable[/red]",
        )
    console.print(table)
    console.print("\n[dim]Connect cloud accounts: python main.py connect  or  GUI -> Connect Cloud Accounts[/dim]")


def _do_verify() -> None:
    engine = BackupEngine(DEFAULT_CONFIG)
    _setup_log(engine.config)
    reports = engine.verify_only()
    if not reports:
        console.print("[yellow]No sources available to verify.[/yellow]")
        return
    table = Table(title="Verification Results")
    table.add_column("Source")
    table.add_column("Complete")
    table.add_column("Verified")
    table.add_column("Issues")
    table.add_column("Status")
    for r in reports:
        status = "[green]PASS[/green]" if r["passed"] else "[red]FAIL[/red]"
        table.add_row(
            r["provider"],
            f"{r['completeness_percent']:.1f}%",
            f"{r['verified_ok']}/{r['total_source_files']}",
            str(r["issues"]),
            status,
        )
    console.print(table)


def _do_verify_gps() -> None:
    engine = BackupEngine(DEFAULT_CONFIG)
    _setup_log(engine.config)
    reports = engine.verify_gps_only()
    if not reports:
        console.print("[yellow]No local photo sources available for GPS check.[/yellow]")
        return
    table = Table(title="EXIF GPS Verification")
    table.add_column("Source")
    table.add_column("Photos scanned")
    table.add_column("Had GPS")
    table.add_column("Preserved")
    table.add_column("Lost")
    table.add_column("Status")
    for r in reports:
        status = "[green]PASS[/green]" if r["passed"] else "[red]FAIL[/red]"
        if r["source_with_gps"] == 0:
            status = "[dim]no GPS in source[/dim]"
        table.add_row(
            r["provider"],
            str(r["images_scanned"]),
            str(r["source_with_gps"]),
            f"{r['gps_preserved']} ({r['preservation_percent']:.0f}%)",
            str(r["gps_lost"]),
            status,
        )
    console.print(table)
    console.print("\nDetailed report: <backup destination>\\Logs\\gps_report_*.json")


def _do_run(*, dry_run: bool) -> None:
    engine = BackupEngine(DEFAULT_CONFIG)
    if dry_run:
        engine.backup_cfg["dry_run"] = True
    _setup_log(engine.config)
    try:
        results = engine.run()
    except RuntimeError as exc:
        console.print(f"\n[red]{exc}[/red]")
        sys.exit(1)
    _print_summary(results)
    _print_gps_summary(results)
    if any(not r.success for r in results):
        console.print("\n[red]Some sources failed or are incomplete. Check Logs folder.[/red]")
        sys.exit(1)
    if dry_run:
        console.print("\n[green]Dry-run complete — no files changed.[/green]")
    else:
        console.print("\n[bold green]Backup complete![/bold green] See Logs\\Backup_Log.xlsx")


@click.group(invoke_without_command=True)
@click.pass_context
@click.version_option(package_name="cloudtohdd")
def cli(ctx) -> None:
    """CloudToHDD Backup — photos from cloud & phones to local HDD."""
    if ctx.invoked_subcommand is None:
        _interactive_menu()


@cli.command("detect")
def detect_cmd() -> None:
    """Detect USB phones and cloud sync folders."""
    _do_detect()


@cli.command("inventory")
@click.option("--config", "config_path", default=str(DEFAULT_CONFIG), show_default=True)
def inventory_cmd(config_path: str) -> None:
    """Phase 1 — scan sizes of all configured sources."""
    global DEFAULT_CONFIG
    DEFAULT_CONFIG = Path(config_path)
    _do_inventory()


@cli.command("status")
@click.option("--config", "config_path", default=str(DEFAULT_CONFIG), show_default=True)
def status_cmd(config_path: str) -> None:
    """Show configured providers and sources."""
    global DEFAULT_CONFIG
    DEFAULT_CONFIG = Path(config_path)
    _do_status()


@cli.command("wizard")
@click.option("--config", "config_path", default=str(DEFAULT_CONFIG), show_default=True)
def wizard_cmd(config_path: str) -> None:
    """Interactive CloudToHDD Backup setup wizard."""
    run_wizard(Path(config_path), EXAMPLE_CONFIG)


@cli.command("verify")
@click.option("--config", "config_path", default=str(DEFAULT_CONFIG), show_default=True)
def verify_cmd(config_path: str) -> None:
    """Verify backup completeness."""
    global DEFAULT_CONFIG
    DEFAULT_CONFIG = Path(config_path)
    _do_verify()


@cli.command("verify-gps")
@click.option("--config", "config_path", default=str(DEFAULT_CONFIG), show_default=True)
@click.option("--provider", "-p", multiple=True, type=click.Choice(ALL_PROVIDERS))
def verify_gps_cmd(config_path: str, provider: tuple[str, ...]) -> None:
    """Verify EXIF GPS location preserved in backed-up photos."""
    global DEFAULT_CONFIG
    DEFAULT_CONFIG = Path(config_path)
    engine = BackupEngine(DEFAULT_CONFIG)
    _setup_log(engine.config)
    reports = engine.verify_gps_only(list(provider) if provider else None)
    if not reports:
        console.print("[yellow]No local photo sources available for GPS check.[/yellow]")
        return
    table = Table(title="EXIF GPS Verification")
    table.add_column("Source")
    table.add_column("Photos scanned")
    table.add_column("Had GPS")
    table.add_column("Preserved")
    table.add_column("Lost")
    table.add_column("Status")
    for r in reports:
        status = "[green]PASS[/green]" if r["passed"] else "[red]FAIL[/red]"
        if r["source_with_gps"] == 0:
            status = "[dim]no GPS in source[/dim]"
        table.add_row(
            r["provider"],
            str(r["images_scanned"]),
            str(r["source_with_gps"]),
            f"{r['gps_preserved']} ({r['preservation_percent']:.0f}%)",
            str(r["gps_lost"]),
            status,
        )
    console.print(table)
    console.print("\nDetailed report: <backup destination>\\Logs\\gps_report_*.json")


@cli.group("connect")
@click.pass_context
def connect_group(ctx) -> None:
    """Connect Google, OneDrive, iCloud, or Google Photos inside the tool."""
    if ctx.invoked_subcommand is None:
        _do_connect()


@connect_group.command("status")
def connect_status_cmd() -> None:
    """Show cloud account connection status."""
    _print_connection_status()


def _connect_provider_cmd(provider: str, online: bool, use_sync: bool, takeout: bool) -> None:
    if sum([online, use_sync, takeout]) > 1:
        console.print("[red]Use only one of --online, --sync, or --takeout.[/red]")
        sys.exit(1)
    if takeout:
        mode = "takeout"
    elif use_sync:
        mode = "sync"
    else:
        mode = "online"
    _do_connect(provider, mode=mode)


@connect_group.command("onedrive")
@click.option("--online", is_flag=True, help="Sign in with Microsoft account (browser)")
@click.option("--sync", "use_sync", is_flag=True, help="Use OneDrive desktop sync folder")
def connect_onedrive_cmd(online: bool, use_sync: bool) -> None:
    """Connect Microsoft OneDrive."""
    _connect_provider_cmd("onedrive", online or not use_sync, use_sync, False)


@connect_group.command("google-drive")
@click.option("--online", is_flag=True, help="Sign in with Google account (browser)")
@click.option("--sync", "use_sync", is_flag=True, help="Use Google Drive desktop folder")
def connect_google_drive_cmd(online: bool, use_sync: bool) -> None:
    """Connect Google Drive."""
    _connect_provider_cmd("google_drive", online or not use_sync, use_sync, False)


@connect_group.command("google-photos")
@click.option("--takeout", is_flag=True, help="Set Google Takeout download folder")
@click.option("--online", is_flag=True, help="Open takeout.google.com export page")
def connect_google_photos_cmd(takeout: bool, online: bool) -> None:
    """Connect Google Photos (Takeout or sign-in)."""
    if takeout:
        _do_connect("google_photos", mode="takeout")
    else:
        _do_connect("google_photos", mode="online")


@connect_group.command("icloud")
@click.option("--online", is_flag=True, help="Sign in with Apple ID (browser)")
@click.option("--sync", "use_sync", is_flag=True, help="Use iCloud for Windows folder")
def connect_icloud_cmd(online: bool, use_sync: bool) -> None:
    """Connect iCloud Drive."""
    _connect_provider_cmd("icloud", online or not use_sync, use_sync, False)


@cli.command("android-detect")
def android_detect_cmd() -> None:
    """Diagnose Android USB connection and show how to fix detection."""
    detection = get_android_detection()
    console.print(Panel.fit("[bold]Android USB Detection[/bold]", border_style="cyan"))
    table = Table()
    table.add_column("Check")
    table.add_column("Result")
    table.add_row("Device", detection.device_name or "(not found)")
    table.add_row("Status", detection.status)
    table.add_row("Storage", detection.storage_path or "-")
    table.add_row("Folders", ", ".join(detection.folders) if detection.folders else "-")
    table.add_row("Files visible", str(detection.file_count) if detection.is_ready else "-")
    table.add_row("Message", detection.message)
    console.print(table)

    if detection.is_ready:
        console.print(f"\n[green]Ready to backup from:[/green] {detection.storage_path}")
    elif detection.is_connected:
        console.print("\n[bold yellow]Phone is plugged in but not ready. Do this on your Android:[/bold yellow]")
        console.print("  1. Unlock the phone")
        console.print("  2. Swipe down notification shade — tap USB notification")
        console.print("  3. Select [bold]File transfer / MTP[/bold] (not charging only)")
        console.print("  4. If prompted, allow USB debugging / file access")
        console.print("  5. Keep screen ON during backup")
        console.print("  6. Re-run: [cyan]python main.py android-detect[/cyan]")
    else:
        console.print("\n[yellow]No Android phone detected.[/yellow]")
        console.print("  - Use a data-capable USB cable (not charge-only)")
        console.print("  - Try a different USB port")
        console.print("  - On Samsung: install Smart Switch or use built-in MTP drivers")


@cli.command("iphone-detect")
def iphone_detect_cmd() -> None:
    """Diagnose iPhone USB connection and show how to fix detection."""
    detection = get_iphone_detection()
    console.print(Panel.fit("[bold]iPhone USB Detection[/bold]", border_style="cyan"))
    table = Table()
    table.add_column("Check")
    table.add_column("Result")
    table.add_row("Device", detection.device_name or "(not found)")
    table.add_row("Status", detection.status)
    table.add_row("Storage", detection.storage_name or "-")
    table.add_row("DCIM files", str(detection.dcim_file_count) if detection.is_ready else "-")
    table.add_row("Message", detection.message)
    console.print(table)

    if detection.is_ready:
        console.print(f"\n[green]Ready to backup from:[/green] {detection.dcim_path}")
    elif detection.is_connected:
        console.print("\n[bold yellow]iPhone is plugged in but not ready. Do this on your iPhone:[/bold yellow]")
        console.print("  1. Unlock the phone (enter passcode / Face ID)")
        console.print("  2. Keep the screen ON during backup")
        console.print('  3. If prompted: tap [bold]Trust This Computer[/bold]')
        console.print("  4. Settings > Privacy & Security > Apple Devices > allow this PC")
        console.print("  5. Open the Photos app once (grants media access)")
        console.print("  6. Re-run: [cyan]python main.py iphone-detect[/cyan]")
        console.print("\n[dim]Install Apple Devices app from Microsoft Store if drivers are missing.[/dim]")
    else:
        console.print("\n[yellow]No iPhone detected.[/yellow]")
        console.print("  - Use a data-capable USB cable (not charge-only)")
        console.print("  - Try a different USB port (USB 2.0 ports are more reliable)")
        console.print("  - Install [bold]Apple Devices[/bold] or iTunes from Microsoft Store")


@cli.command("pin-request")
@click.option("--config", "config_path", default=str(DEFAULT_CONFIG), show_default=True)
@click.option(
    "--service",
    "-s",
    type=click.Choice(["onedrive", "google_drive", "icloud"]),
    default="onedrive",
    show_default=True,
)
def pin_request_cmd(config_path: str, service: str) -> None:
    """Request cloud files to download locally (Always keep on this device)."""
    global DEFAULT_CONFIG
    DEFAULT_CONFIG = Path(config_path)
    config = _load_config()
    folder = resolve_cloud_folder(service, config)
    if not folder:
        console.print(f"[red]No {service} folder found.[/red]")
        sys.exit(1)

    if service == "google_drive":
        console.print(
            "[yellow]Auto pin-request is only supported for OneDrive on Windows.[/yellow]\n"
            "For Google Drive: right-click folder -> Available offline."
        )
        open_folder_in_explorer(folder)
        return

    console.print(f"[bold]Requesting local download for {folder}...[/bold]")
    console.print("[dim]This asks the cloud sync app to download all files in the background.[/dim]\n")
    result = request_pin_all_windows(folder, service=service)
    if result.success:
        console.print(f"[green]{result.message}[/green]")
        console.print(f"\n[cyan]Method used:[/cyan] {result.method}")
        if result.cloud_files_before:
            console.print(
                f"[cyan]Cloud-only files when requested:[/cyan] {result.cloud_files_before:,}"
            )
        console.print("\n[bold]Next steps:[/bold]")
        console.print("  1. Keep the cloud sync app open and wait for sync")
        console.print(f"  2. Run: python main.py pin-status --service {service} --watch 120")
        console.print("  3. When 100% local, run the backup again")
    else:
        console.print(f"[red]{result.message}[/red]")
        console.print(PIN_INSTRUCTIONS)
        sys.exit(1)


@cli.command("pin-status")
@click.option("--config", "config_path", default=str(DEFAULT_CONFIG), show_default=True)
@click.option(
    "--service",
    "-s",
    type=click.Choice(["onedrive", "google_drive", "icloud"]),
    default="onedrive",
    show_default=True,
)
@click.option("--watch", "watch_seconds", type=int, default=0, help="Rescan every N seconds until 100% local")
@click.option("--open", "open_folder", is_flag=True, help="Open sync folder in File Explorer")
def pin_status_cmd(config_path: str, service: str, watch_seconds: int, open_folder: bool) -> None:
    """Check how much cloud data is downloaded locally (pin progress)."""
    global DEFAULT_CONFIG
    DEFAULT_CONFIG = Path(config_path)
    _do_pin_status(service=service, watch_seconds=watch_seconds, open_folder=open_folder)


@cli.command("certify")
@click.option("--config", "config_path", default=str(DEFAULT_CONFIG), show_default=True)
@click.option("--provider", "-p", multiple=True, type=click.Choice(ALL_PROVIDERS))
def certify_cmd(config_path: str, provider: tuple[str, ...]) -> None:
    """Run pre-delete safety certification on completed backups."""
    global DEFAULT_CONFIG
    DEFAULT_CONFIG = Path(config_path)
    engine = BackupEngine(DEFAULT_CONFIG)
    _setup_log(engine.config)
    console.print("[bold]Running pre-delete safety certification...[/bold]\n")
    cert = engine.certify(list(provider) if provider else None)
    table = Table(title="Safety Certificate — Before Deleting Source")
    table.add_column("Source")
    table.add_column("Safe to delete?")
    table.add_column("Files")
    table.add_column("Checks passed")
    for p in cert.providers:
        safe = "[green]YES[/green]" if p.safe_to_delete_source else "[red]NO[/red]"
        passed = sum(1 for c in p.checks if c.passed)
        table.add_row(
            p.provider,
            safe,
            f"{p.dest_files}/{p.source_files}",
            f"{passed}/{len(p.checks)}",
        )
    console.print(table)
    if cert.global_safe_to_delete:
        console.print(
            "\n[bold green]CERTIFIED[/bold green] — automated checks passed.\n"
            "Still recommended: spot-check photos/docs and keep a second copy 30 days."
        )
    else:
        console.print(
            "\n[bold red]NOT CERTIFIED — DO NOT DELETE SOURCE[/bold red]\n"
            "See Logs\\SAFETY_CERTIFICATE_LATEST.txt for details."
        )
        for b in cert.blockers[:10]:
            console.print(f"  [red]•[/red] {b}")
        sys.exit(1)


@cli.command("run")
@click.option("--config", "config_path", default=str(DEFAULT_CONFIG), show_default=True)
@click.option("--provider", "-p", multiple=True, type=click.Choice(ALL_PROVIDERS))
@click.option("--dry-run", is_flag=True)
def run_cmd(config_path: str, provider: tuple[str, ...], dry_run: bool) -> None:
    """Run backup for all enabled sources."""
    global DEFAULT_CONFIG
    DEFAULT_CONFIG = Path(config_path)
    engine = BackupEngine(DEFAULT_CONFIG)
    if dry_run:
        engine.backup_cfg["dry_run"] = True
    _setup_log(engine.config)
    try:
        results = engine.run(list(provider) if provider else None)
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(1)
    _print_summary(results)
    _print_gps_summary(results)
    if any(not r.success for r in results):
        sys.exit(1)


@cli.command("gui")
def gui_cmd() -> None:
    """Open the graphical desktop interface."""
    from src.gui.app import run_gui

    run_gui()


@cli.command("init")
@click.option("--force", is_flag=True)
def init_cmd(force: bool) -> None:
    """Create config.yaml from CloudToHDD Backup template."""
    if DEFAULT_CONFIG.exists() and not force:
        console.print("[yellow]config.yaml exists. Use wizard or --force.[/yellow]")
        return
    DEFAULT_CONFIG.write_text(EXAMPLE_CONFIG.read_text(encoding="utf-8"), encoding="utf-8")
    console.print(f"[green]Created {DEFAULT_CONFIG}[/green]")


if __name__ == "__main__":
    cli()
