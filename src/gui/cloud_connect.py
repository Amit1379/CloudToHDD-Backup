"""Cloud account connection dialog for the GUI."""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from src.cloud_connect import (
    CLOUD_SERVICES,
    connect_sync_folder,
    connect_takeout_folder,
    finalize_rclone_connection,
    get_connection_status,
    launch_rclone_sign_in,
    launch_rclone_sign_in_legacy_console,
)
from src.paths import apply_window_icon


class CloudConnectDialog(ctk.CTkToplevel):
    def __init__(self, master, on_connected=None) -> None:
        super().__init__(master)
        self.on_connected = on_connected
        self.title("Connect Cloud Accounts")
        self.geometry("620x520")
        self.minsize(520, 400)
        apply_window_icon(self)
        self.transient(master)
        self.grab_set()

        ctk.CTkLabel(
            self,
            text="Connect your cloud accounts",
            font=ctk.CTkFont(size=18, weight="bold"),
        ).pack(padx=16, pady=(16, 4), anchor="w")

        ctk.CTkLabel(
            self,
            text="Choose sync folder (desktop app) or sign in online (direct cloud backup).",
            font=ctk.CTkFont(size=12),
            text_color="gray70",
            wraplength=560,
            justify="left",
        ).pack(padx=16, pady=(0, 12), anchor="w")

        scroll = ctk.CTkScrollableFrame(self)
        scroll.pack(fill="both", expand=True, padx=16, pady=8)

        self._status_labels: dict[str, ctk.CTkLabel] = {}
        for key, spec in CLOUD_SERVICES.items():
            self._build_service_card(scroll, key, spec)

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(fill="x", padx=16, pady=16)
        ctk.CTkButton(btn_row, text="Refresh status", command=self._refresh_all).pack(side="left", padx=4)
        ctk.CTkButton(btn_row, text="Close", command=self.destroy).pack(side="right", padx=4)

        self._refresh_all()

    def _build_service_card(self, parent, key: str, spec) -> None:
        card = ctk.CTkFrame(parent)
        card.pack(fill="x", pady=6)

        ctk.CTkLabel(card, text=spec.label, font=ctk.CTkFont(size=14, weight="bold")).pack(
            anchor="w", padx=12, pady=(10, 0)
        )
        status = ctk.CTkLabel(card, text="Checking…", font=ctk.CTkFont(size=11), text_color="gray70", wraplength=540, justify="left")
        status.pack(anchor="w", padx=12, pady=4)
        self._status_labels[key] = status

        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(anchor="w", padx=8, pady=(0, 10))

        if key == "google_photos":
            ctk.CTkButton(
                row, text="Set Takeout folder…", width=150,
                command=lambda: self._takeout_folder(key),
            ).pack(side="left", padx=4)
            ctk.CTkButton(
                row, text="Sign in with Google", width=150,
                command=lambda: self._sign_in(key),
            ).pack(side="left", padx=4)
        else:
            ctk.CTkButton(
                row, text="Use sync folder", width=130,
                command=lambda: self._sync_folder(key),
            ).pack(side="left", padx=4)
            ctk.CTkButton(
                row, text="Sign in online", width=130,
                command=lambda: self._sign_in(key),
            ).pack(side="left", padx=4)
            ctk.CTkButton(
                row, text="Console sign-in", width=110,
                font=ctk.CTkFont(size=11),
                command=lambda: self._sign_in_console(key),
            ).pack(side="left", padx=4)

    def _refresh_all(self) -> None:
        for key in CLOUD_SERVICES:
            st = get_connection_status(key)
            label = self._status_labels[key]
            if st.connected:
                text = f"Connected ({st.method}): {st.detail}"
                color = "#2fa572"
            else:
                text = st.detail
                color = "#e67e22" if "sign-in" in st.detail.lower() else "gray70"
            label.configure(text=text, text_color=color)

    def _notify(self, result) -> None:
        if result.success and not result.needs_user_action:
            messagebox.showinfo("Connected", result.message)
            self._refresh_all()
            if self.on_connected:
                self.on_connected()
        elif result.success and result.needs_user_action:
            messagebox.showinfo("Continue in browser", result.message)
            self._refresh_all()
        else:
            messagebox.showwarning("Not connected", result.message)
            self._refresh_all()

    def _sync_folder(self, key: str) -> None:
        folder = filedialog.askdirectory(title=f"Select {CLOUD_SERVICES[key].label} sync folder (optional)")
        result = connect_sync_folder(key, Path(folder) if folder else None)
        self._notify(result)

    def _takeout_folder(self, key: str) -> None:
        folder = filedialog.askdirectory(title="Select Google Photos Takeout download folder")
        if folder:
            result = connect_takeout_folder(key, Path(folder))
            self._notify(result)

    def _sign_in(self, key: str) -> None:
        result = launch_rclone_sign_in(key)
        if result.success:
            messagebox.showinfo("Connected", result.message)
            self._refresh_all()
            if self.on_connected:
                self.on_connected()
        elif result.needs_user_action:
            messagebox.showinfo("Continue", result.message)
            self._refresh_all()
        else:
            messagebox.showerror("Error", result.message)

    def _sign_in_console(self, key: str) -> None:
        result = launch_rclone_sign_in_legacy_console(key)
        if result.success:
            messagebox.showinfo(
                "Sign in",
                result.message + "\n\nAfter signing in, click 'Refresh status'.",
            )
            self._refresh_all()
        else:
            messagebox.showerror("Error", result.message)

    def check_pending_signins(self) -> None:
        """Refresh and finalize any completed rclone OAuth."""
        for key in CLOUD_SERVICES:
            st = get_connection_status(key)
            if st.method == "rclone" or not st.connected:
                finalize_rclone_connection(key)
        self._refresh_all()
