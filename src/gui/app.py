"""CloudToHDD Backup — graphical desktop UI."""

from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk
import yaml

from src.backup_engine import BackupEngine
from src.gui.cloud_connect import CloudConnectDialog
from src.device_detection import detect_android_storage, detect_iphone_dcim, get_android_detection, get_iphone_detection
from src.gui.log_handler import attach_gui_logging
from src.inventory import run_inventory, save_inventory_report
from src.logger import setup_logging
from src.cloud_connect import (
    CLOUD_SERVICES,
    connect_sync_folder,
    connect_takeout_folder,
    finalize_rclone_connection,
    get_all_connection_statuses,
    get_connection_status,
    launch_rclone_sign_in,
)
from src.cloud_pin_status import (
    PIN_INSTRUCTIONS,
    open_folder_in_explorer,
    request_pin_all_windows,
    resolve_cloud_folder,
    save_pin_report,
    scan_cloud_pin_progress,
)
from src.utils import (
    detect_google_drive_folder,
    detect_icloud_folder,
    detect_onedrive_folder,
    find_rclone_executable,
    resolve_writable_root,
)

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "config.yaml"
EXAMPLE_CONFIG = ROOT / "config.example.yaml"

PROVIDER_INFO = {
    "iphone": ("iPhone (USB)", "01_iPhone", "Connect via USB, tap Trust"),
    "android": ("Android (USB)", "02_Android", "Enable File Transfer (MTP)"),
    "google_photos": ("Google Photos", "03_GooglePhotos", "Takeout ZIPs or rclone"),
    "google_drive": ("Google Drive", "04_GoogleDrive", "Sync folder or rclone"),
    "onedrive": ("OneDrive", "05_OneDrive", "Sync folder or rclone"),
    "icloud": ("iCloud Drive", "(optional)", "Windows iCloud app"),
}


def format_bytes(num: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num < 1024:
            return f"{num:.1f} {unit}" if unit != "B" else f"{num} B"
        num /= 1024
    return f"{num:.1f} PB"


class CloudToHDDApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.title("CloudToHDD Backup")
        self.geometry("1100x720")
        self.minsize(900, 600)

        self.log_queue: queue.Queue[str] = queue.Queue()
        self._busy = False
        self._provider_vars: dict[str, ctk.BooleanVar] = {}
        self._status_labels: dict[str, ctk.CTkLabel] = {}

        self._ensure_config()
        self._build_ui()
        self._poll_log_queue()
        self.refresh_status()

    def _finalize_cloud_connections(self) -> None:
        from src.cloud_connect import CLOUD_SERVICES, finalize_rclone_connection

        for key in CLOUD_SERVICES:
            finalize_rclone_connection(key)

    def _ensure_config(self) -> None:
        if not CONFIG_PATH.exists() and EXAMPLE_CONFIG.exists():
            CONFIG_PATH.write_text(EXAMPLE_CONFIG.read_text(encoding="utf-8"), encoding="utf-8")

    def _load_config(self) -> dict:
        if CONFIG_PATH.exists():
            return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
        return {}

    def _save_config(self, config: dict) -> None:
        CONFIG_PATH.write_text(yaml.dump(config, default_flow_style=False, sort_keys=False), encoding="utf-8")

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._build_sidebar()
        self._build_main_panel()

    def _build_sidebar(self) -> None:
        sidebar = ctk.CTkFrame(self, width=220, corner_radius=0)
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_rowconfigure(15, weight=1)

        ctk.CTkLabel(
            sidebar,
            text="CloudToHDD",
            font=ctk.CTkFont(size=22, weight="bold"),
        ).grid(row=0, column=0, padx=16, pady=(20, 4), sticky="w")

        ctk.CTkLabel(
            sidebar,
            text="Photo Backup from Cloud to Local HDD",
            font=ctk.CTkFont(size=12),
            text_color="gray70",
        ).grid(row=1, column=0, padx=16, pady=(0, 8), sticky="w")

        ctk.CTkButton(
            sidebar,
            text="Connect Cloud Accounts",
            command=self.open_cloud_connect,
            height=38,
            fg_color="#8e44ad",
            hover_color="#7d3c98",
        ).grid(row=2, column=0, padx=12, pady=(0, 10), sticky="ew")

        buttons = [
            ("▶  Run Backup", self.run_backup, "#2fa572"),
            ("👁  Preview (Dry Run)", self.run_dry_run, "#3b8ed0"),
            ("📋  Inventory", self.run_inventory, None),
            ("✓  Verify Backup", self.run_verify, None),
            ("📍  Verify GPS", self.run_verify_gps, None),
            ("🛡  Safety Certificate", self.run_certify, "#c0392b"),
            ("☁  OneDrive Pin Status", self.run_pin_status, "#e67e22"),
            ("🔍  Detect Devices", self.refresh_status, None),
            ("📂  Open Logs", self.open_logs_folder, None),
        ]

        for idx, (text, cmd, color) in enumerate(buttons, start=3):
            kwargs = {"command": cmd, "height": 36, "anchor": "w"}
            if color:
                kwargs["fg_color"] = color
            btn = ctk.CTkButton(sidebar, text=text, **kwargs)
            btn.grid(row=idx, column=0, padx=12, pady=4, sticky="ew")

        ctk.CTkLabel(sidebar, text="", ).grid(row=14, column=0)
        ctk.CTkButton(sidebar, text="Save Settings", command=self.save_settings, height=32).grid(
            row=15, column=0, padx=12, pady=(4, 16), sticky="ew"
        )

    def _build_main_panel(self) -> None:
        main = ctk.CTkFrame(self, fg_color="transparent")
        main.grid(row=0, column=1, sticky="nsew", padx=12, pady=12)
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(3, weight=1)

        dest_frame = ctk.CTkFrame(main)
        dest_frame.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        dest_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(dest_frame, text="Backup destination:", font=ctk.CTkFont(weight="bold")).grid(
            row=0, column=0, padx=12, pady=12, sticky="w"
        )
        config = self._load_config()
        default_dest = config.get("backup", {}).get("destination_root", "D:\\CloudToHDD_Backup")
        self.dest_var = ctk.StringVar(value=default_dest)
        self.dest_entry = ctk.CTkEntry(dest_frame, textvariable=self.dest_var, height=36)
        self.dest_entry.grid(row=0, column=1, padx=8, pady=12, sticky="ew")
        ctk.CTkButton(dest_frame, text="Browse…", width=90, command=self.browse_destination).grid(
            row=0, column=2, padx=(0, 12), pady=12
        )

        providers_frame = ctk.CTkFrame(main)
        providers_frame.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        providers_frame.grid_columnconfigure((0, 1), weight=1)

        ctk.CTkLabel(
            providers_frame,
            text="Sources to backup",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).grid(row=0, column=0, columnspan=2, padx=12, pady=(12, 8), sticky="w")

        config_providers = config.get("providers", {})
        row = 1
        col = 0
        for key, (label, folder, hint) in PROVIDER_INFO.items():
            enabled = config_providers.get(key, {}).get("enabled", key in ("onedrive", "google_photos"))
            var = ctk.BooleanVar(value=enabled)
            self._provider_vars[key] = var

            card = ctk.CTkFrame(providers_frame)
            card.grid(row=row, column=col, padx=8, pady=6, sticky="ew")

            ctk.CTkCheckBox(card, text=label, variable=var, font=ctk.CTkFont(weight="bold")).pack(
                anchor="w", padx=10, pady=(8, 0)
            )
            ctk.CTkLabel(card, text=folder, font=ctk.CTkFont(size=11), text_color="gray60").pack(
                anchor="w", padx=10
            )
            status = ctk.CTkLabel(card, text="Checking…", font=ctk.CTkFont(size=11), text_color="gray70")
            status.pack(anchor="w", padx=10, pady=(0, 4))
            self._status_labels[key] = status

            if key in CLOUD_SERVICES:
                ctk.CTkButton(
                    card,
                    text="Connect…",
                    width=90,
                    height=24,
                    font=ctk.CTkFont(size=11),
                    command=lambda k=key: self._quick_connect(k),
                ).pack(anchor="w", padx=10, pady=(0, 8))
            else:
                ctk.CTkLabel(card, text="", height=8).pack()

            col += 1
            if col > 1:
                col = 0
                row += 1

        opts_frame = ctk.CTkFrame(main)
        opts_frame.grid(row=2, column=0, sticky="ew", pady=(0, 10))

        backup_cfg = config.get("backup", {})
        meta_cfg = backup_cfg.get("preserve_metadata", {})

        self.verify_checksums_var = ctk.BooleanVar(value=backup_cfg.get("verify_checksums", True))
        self.verify_gps_var = ctk.BooleanVar(value=meta_cfg.get("verify_gps", True))
        self.require_100_var = ctk.BooleanVar(value=backup_cfg.get("require_100_percent", True))

        ctk.CTkCheckBox(opts_frame, text="Verify file checksums", variable=self.verify_checksums_var).pack(
            side="left", padx=12, pady=10
        )
        ctk.CTkCheckBox(opts_frame, text="Verify photo GPS location", variable=self.verify_gps_var).pack(
            side="left", padx=12, pady=10
        )
        ctk.CTkCheckBox(opts_frame, text="Require 100% complete", variable=self.require_100_var).pack(
            side="left", padx=12, pady=10
        )

        log_frame = ctk.CTkFrame(main)
        log_frame.grid(row=3, column=0, sticky="nsew")
        log_frame.grid_columnconfigure(0, weight=1)
        log_frame.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(log_frame, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 0))
        header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(header, text="Activity log", font=ctk.CTkFont(weight="bold")).grid(
            row=0, column=0, sticky="w"
        )
        self.progress = ctk.CTkProgressBar(header, mode="indeterminate")
        self.progress.grid(row=0, column=1, sticky="e", padx=8)
        self.progress.grid_remove()

        self.log_box = ctk.CTkTextbox(log_frame, font=ctk.CTkFont(family="Consolas", size=12))
        self.log_box.grid(row=1, column=0, sticky="nsew", padx=8, pady=8)
        self.log_box.configure(state="disabled")

        self.status_bar = ctk.CTkLabel(main, text="Ready", anchor="w", text_color="gray60")
        self.status_bar.grid(row=4, column=0, sticky="ew", pady=(4, 0))

    def log(self, message: str) -> None:
        self.log_box.configure(state="normal")
        self.log_box.insert("end", message + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _poll_log_queue(self) -> None:
        while True:
            try:
                msg = self.log_queue.get_nowait()
                self.log(msg)
            except queue.Empty:
                break
        self.after(150, self._poll_log_queue)

    def _set_busy(self, busy: bool, status: str = "") -> None:
        self._busy = busy
        if busy:
            self.progress.grid()
            self.progress.start()
            self.status_bar.configure(text=status or "Working…")
        else:
            self.progress.stop()
            self.progress.grid_remove()
            self.status_bar.configure(text=status or "Ready")

    def _apply_settings_to_config(self) -> dict:
        config = self._load_config()
        backup = config.setdefault("backup", {})
        backup["destination_root"] = self.dest_var.get().strip()
        backup["layout"] = "digital_archive"
        backup["verify_checksums"] = self.verify_checksums_var.get()
        backup["require_100_percent"] = self.require_100_var.get()
        backup.setdefault("block_unavailable_sources", False)
        meta = backup.setdefault("preserve_metadata", {})
        meta["verify_gps"] = self.verify_gps_var.get()
        meta.setdefault("enabled", True)
        meta.setdefault("level", "full")

        providers = config.setdefault("providers", {})
        for key, var in self._provider_vars.items():
            providers.setdefault(key, {})["enabled"] = var.get()

        self._save_config(config)
        return config

    def save_settings(self) -> None:
        self._apply_settings_to_config()
        self.log("Settings saved.")
        messagebox.showinfo("Saved", "Settings saved to config.yaml")

    def browse_destination(self) -> None:
        path = filedialog.askdirectory(title="Select backup destination folder")
        if path:
            self.dest_var.set(path)

    def open_cloud_connect(self) -> None:
        CloudConnectDialog(self, on_connected=self.refresh_status)

    def _quick_connect(self, provider_key: str) -> None:
        """Open cloud connect dialog focused on one provider."""
        dialog = CloudConnectDialog(self, on_connected=self.refresh_status)
        dialog.after(100, dialog.lift)

    def refresh_status(self) -> None:
        if self._busy:
            return

        def work() -> None:
            config = self._load_config()
            detections = {
                "iphone": detect_iphone_dcim(),
                "android": detect_android_storage(),
                "google_photos": None,
                "google_drive": detect_google_drive_folder(),
                "onedrive": detect_onedrive_folder(),
                "icloud": detect_icloud_folder(),
            }
            gp_folder = config.get("providers", {}).get("google_photos", {}).get(
                "takeout_download_folder", ""
            )
            if gp_folder:
                detections["google_photos"] = Path(gp_folder)

            cloud_status = {s.provider: s for s in get_all_connection_statuses(config)}
            self._finalize_cloud_connections()
            cloud_status = {s.provider: s for s in get_all_connection_statuses(config)}

            self.after(0, lambda: self._update_status_labels(detections, cloud_status))
            self.after(0, lambda: self._set_busy(False, "Ready — devices scanned"))

        self._set_busy(True, "Detecting devices...")
        threading.Thread(target=work, daemon=True).start()

    def _update_status_labels(self, detections: dict, cloud_status: dict | None = None) -> None:
        cloud_status = cloud_status or {}
        iphone_det = get_iphone_detection()
        android_det = get_android_detection()
        if iphone_det.is_ready:
            detections["iphone"] = Path(iphone_det.dcim_path)
        elif iphone_det.is_connected:
            detections["iphone"] = None  # handled below
        if android_det.is_ready:
            detections["android"] = Path(android_det.storage_path)
        elif android_det.is_connected:
            detections["android"] = None  # handled below

        for key, path in detections.items():
            label = self._status_labels.get(key)
            if not label:
                continue
            if key == "iphone" and iphone_det.is_connected and not iphone_det.is_ready:
                label.configure(
                    text=f"! Connected — unlock & Trust ({iphone_det.status})",
                    text_color="#e67e22",
                )
                continue
            if key == "android" and android_det.is_connected and not android_det.is_ready:
                label.configure(
                    text=f"! Connected — unlock & MTP ({android_det.status})",
                    text_color="#e67e22",
                )
                continue
            if key in CLOUD_SERVICES:
                st = cloud_status.get(key) or get_connection_status(key)
                if st.connected:
                    text = f"✓ {st.method}: {st.detail[:60]}"
                    color = "#2fa572"
                else:
                    text = f"○ {st.detail[:70]}"
                    color = "#e67e22"
                label.configure(text=text, text_color=color)
                continue
            if path and Path(path).exists() if path else False:
                text = f"✓ Found: {path}"
                color = "#2fa572"
            elif key == "google_photos":
                text = "○ Click Connect to set up Google Photos"
                color = "gray70"
            else:
                text = "○ Not connected / not found"
                color = "#c0392b" if key in ("iphone", "android") else "gray70"
            label.configure(text=text, text_color=color)

        rclone = find_rclone_executable()
        if rclone:
            self.log(f"rclone available: {rclone}")

    def _setup_engine_logging(self, config: dict) -> BackupEngine:
        log_cfg = config.get("logging", {})
        setup_logging(
            log_cfg.get("level", "INFO"),
            Path(log_cfg.get("log_dir", "./logs")),
            log_cfg.get("keep_days", 30),
        )
        attach_gui_logging(self.log_queue, log_cfg.get("level", "INFO"))
        return BackupEngine(CONFIG_PATH)

    def _run_task(self, label: str, func) -> None:
        if self._busy:
            messagebox.showwarning("Busy", "Please wait for the current task to finish.")
            return
        try:
            config = self._apply_settings_to_config()
        except Exception as exc:
            messagebox.showerror("Settings error", str(exc))
            return

        def work() -> None:
            try:
                result_msg = func(config)
                self.log_queue.put(result_msg or f"{label} completed.")
                self.after(0, lambda: self._set_busy(False, "Done"))
                if result_msg and "FAILED" in result_msg:
                    self.after(0, lambda: messagebox.showwarning("Finished with issues", result_msg[:500]))
                elif result_msg and "Backup complete" in result_msg:
                    self.after(0, lambda: messagebox.showinfo("Success", result_msg[:500]))
            except Exception as exc:
                self.log_queue.put(f"ERROR: {exc}")
                self.after(0, lambda: self._set_busy(False, "Error"))
                self.after(0, lambda: messagebox.showerror("Error", str(exc)))

        self._set_busy(True, label)
        threading.Thread(target=work, daemon=True).start()

    def run_backup(self) -> None:
        def task(_config: dict) -> str:
            engine = self._setup_engine_logging(_config)
            results = engine.run()
            return self._format_results(results, dry_run=False)

        self._run_task("Running backup…", task)

    def run_dry_run(self) -> None:
        def task(config: dict) -> str:
            engine = self._setup_engine_logging(config)
            engine.backup_cfg["dry_run"] = True
            results = engine.run()
            return self._format_results(results, dry_run=True)

        self._run_task("Running preview (dry run)…", task)

    def run_inventory(self) -> None:
        def task(config: dict) -> str:
            items = run_inventory(config)
            total = sum(i.size_bytes for i in items)
            dest = config.get("backup", {}).get("destination_root", ".")
            report = save_inventory_report(items, Path(dest), fallback=ROOT / "logs")
            lines = [f"Inventory — total estimated: {format_bytes(total)}", f"Report: {report}", ""]
            for item in items:
                lines.append(
                    f"  {item.source}: {item.status} | {format_bytes(item.size_bytes)} | {item.file_count} files"
                )
            return "\n".join(lines)

        self._run_task("Running inventory…", task)

    def run_verify(self) -> None:
        def task(config: dict) -> str:
            engine = self._setup_engine_logging(config)
            reports = engine.verify_only()
            if not reports:
                return "No sources available to verify."
            lines = ["Verification results:"]
            for r in reports:
                status = "PASS" if r["passed"] else "FAIL"
                lines.append(
                    f"  {r['provider']}: {status} | {r['completeness_percent']:.1f}% | "
                    f"{r['verified_ok']}/{r['total_source_files']} files"
                )
            return "\n".join(lines)

        self._run_task("Verifying backup…", task)

    def run_verify_gps(self) -> None:
        def task(config: dict) -> str:
            engine = self._setup_engine_logging(config)
            reports = engine.verify_gps_only()
            if not reports:
                return "No photo sources available for GPS check."
            lines = ["GPS (EXIF) verification:"]
            for r in reports:
                status = "PASS" if r["passed"] else "FAIL"
                lines.append(
                    f"  {r['provider']}: {status} | {r['gps_preserved']}/{r['source_with_gps']} "
                    f"with GPS ({r['preservation_percent']:.0f}%)"
                )
            lines.append("See Logs\\gps_report_*.json for details.")
            return "\n".join(lines)

        self._run_task("Verifying photo GPS…", task)

    def run_certify(self) -> None:
        def task(config: dict) -> str:
            engine = self._setup_engine_logging(config)
            cert = engine.certify()
            lines = ["Safety certificate (before deleting source):", ""]
            for p in cert.providers:
                status = "SAFE" if p.safe_to_delete_source else "NOT SAFE"
                lines.append(f"  {p.provider}: {status}")
                for c in p.checks:
                    mark = "✓" if c.passed else "✗"
                    lines.append(f"    {mark} {c.name}: {c.detail}")
            lines.append("")
            if cert.global_safe_to_delete:
                lines.append("CERTIFIED — automated checks passed.")
                lines.append("Still spot-check important files before deleting cloud data.")
            else:
                lines.append("NOT CERTIFIED — DO NOT DELETE SOURCE.")
                lines.append("See Logs\\SAFETY_CERTIFICATE_LATEST.txt")
            return "\n".join(lines)

        self._run_task("Running safety certification…", task)

    def run_pin_status(self) -> None:
        if self._busy:
            messagebox.showwarning("Busy", "Please wait for the current task to finish.")
            return

        open_now = messagebox.askyesno(
            "OneDrive Pin Status",
            "Scan OneDrive for cloud-only vs locally downloaded files?\n\n"
            "Open OneDrive folder in Explorer first?",
        )

        def work() -> None:
            try:
                config = self._load_config()
                folder = resolve_cloud_folder("onedrive", config)
                if not folder:
                    self.after(
                        0,
                        lambda: messagebox.showerror(
                            "Not found", "OneDrive folder not found. Is OneDrive installed?"
                        ),
                    )
                    return

                if open_now:
                    open_folder_in_explorer(folder)

                exclude = config.get("providers", {}).get("onedrive", {}).get("exclude_patterns", [])
                dest = resolve_writable_root(
                    config.get("backup", {}).get("destination_root", "."),
                    Path(os.environ.get("USERPROFILE", ".")) / "CloudToHDD_Backup",
                )
                logs_dir = dest / "Logs"

                report = scan_cloud_pin_progress(folder, service="onedrive", exclude_patterns=exclude)
                report_path = save_pin_report(report, logs_dir)

                lines = [
                    "OneDrive local download status:",
                    f"  Folder: {folder}",
                    f"  Files local: {report.local_files:,} / {report.total_files:,} ({report.percent_files_local:.1f}%)",
                    f"  Data local: {format_bytes(report.local_bytes)} / {format_bytes(report.local_bytes + report.cloud_bytes)} ({report.percent_bytes_local:.1f}%)",
                    f"  Cloud-only files: {report.cloud_files:,}",
                    f"  Ready for backup: {'YES' if report.ready_for_backup else 'NO'}",
                    f"  Report: {report_path}",
                ]
                if not report.ready_for_backup:
                    lines.extend(["", PIN_INSTRUCTIONS])
                msg = "\n".join(lines)
                self.log_queue.put(msg)

                def show_result() -> None:
                    if report.ready_for_backup:
                        messagebox.showinfo("OneDrive Ready", msg[:1200])
                    else:
                        action = messagebox.askyesnocancel(
                            "OneDrive Not Ready",
                            msg[:900] + "\n\n"
                            "Request OneDrive to download ALL files now?\n"
                            "(~193 GB, runs in background)",
                        )
                        if action is True:
                            self._request_onedrive_pin()
                        elif action is False:
                            self.run_pin_status()

                self.after(0, show_result)
            except Exception as exc:
                self.log_queue.put(f"ERROR: {exc}")
                self.after(0, lambda: messagebox.showerror("Error", str(exc)))
            finally:
                self.after(0, lambda: self._set_busy(False, "Done"))

        self._set_busy(True, "Scanning OneDrive pin status...")
        threading.Thread(target=work, daemon=True).start()

    def _request_onedrive_pin(self) -> None:
        def work() -> None:
            try:
                config = self._load_config()
                folder = resolve_cloud_folder("onedrive", config)
                if not folder:
                    self.after(0, lambda: messagebox.showerror("Not found", "OneDrive folder not found."))
                    return
                result = request_pin_all_windows(folder)
                self.log_queue.put(result.message)
                self.after(
                    0,
                    lambda: messagebox.showinfo("Pin requested", result.message[:1200])
                    if result.success
                    else messagebox.showerror("Pin failed", result.message[:1200]),
                )
            except Exception as exc:
                self.after(0, lambda: messagebox.showerror("Error", str(exc)))
            finally:
                self.after(0, lambda: self._set_busy(False, "Done"))

        self._set_busy(True, "Requesting OneDrive download...")
        threading.Thread(target=work, daemon=True).start()

    def _format_results(self, results, *, dry_run: bool) -> str:
        lines = []
        if not results:
            return "FAILED — no backup sources ran. Enable at least one source.\n"
        all_ok = all(r.success for r in results)
        if dry_run:
            lines.append("Dry run complete — no files were changed.\n")
        elif all_ok:
            lines.append("Backup complete!\n")
        else:
            lines.append("Backup finished with errors — no data was copied for failed sources.\n")

        for r in results:
            status = "OK" if r.success else "FAILED"
            if r.files_copied == 0 and r.files_skipped == 0 and r.files_failed > 0:
                completeness = "not accessible"
            else:
                completeness = f"{r.completeness_percent:.0f}%"
            lines.append(
                f"  {r.provider}: {status} | {completeness} | "
                f"{format_bytes(r.bytes_copied)} | method={r.method}"
            )
            if r.gps_source_with_location > 0:
                lines.append(
                    f"    GPS: {r.gps_preserved}/{r.gps_source_with_location} preserved "
                    f"({r.gps_preservation_percent:.0f}%)"
                )
            if r.safe_to_delete_source is True:
                lines.append("    Safety: CERTIFIED safe to delete source")
            elif r.safe_to_delete_source is False:
                lines.append("    Safety: NOT certified — do not delete source")

        if not all_ok:
            lines.insert(0, "FAILED — fix issues below and run backup again.\n")
            for r in results:
                if not r.success and r.errors:
                    lines.append(f"\n  [{r.provider}] {r.errors[0]}")
        return "\n".join(lines)

    def open_logs_folder(self) -> None:
        config = self._load_config()
        dest = Path(config.get("backup", {}).get("destination_root", str(ROOT / "logs")))
        logs = dest / "Logs"
        if not logs.exists():
            logs = ROOT / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        os.startfile(str(logs))


def run_gui() -> None:
    app = CloudToHDDApp()
    app.mainloop()


if __name__ == "__main__":
    run_gui()
