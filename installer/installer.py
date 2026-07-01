"""CloudToHDD Backup — Windows installer (creates shortcuts)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

APP_NAME = "CloudToHDD Backup"
INSTALL_DIR = Path(os.environ.get("LOCALAPPDATA", "")) / "CloudToHDD Backup"


def _bundle_path(name: str) -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / name
    return Path(__file__).resolve().parent.parent / "dist" / name


def _create_shortcut(target: Path, shortcut_path: Path, description: str) -> None:
    shortcut_path.parent.mkdir(parents=True, exist_ok=True)
    ps = f"""
$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut('{str(shortcut_path).replace("'", "''")}')
$Shortcut.TargetPath = '{str(target).replace("'", "''")}'
$Shortcut.WorkingDirectory = '{str(target.parent).replace("'", "''")}'
$Shortcut.Description = '{description.replace("'", "''")}'
$Shortcut.Save()
"""
    subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
        check=True,
        capture_output=True,
        text=True,
    )


def install_app() -> None:
    app_src = _bundle_path("CloudToHDD-Backup.exe")
    config_src = _bundle_path("config.example.yaml")
    if not app_src.is_file():
        raise FileNotFoundError(f"Bundled app not found: {app_src}")

    INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    app_dest = INSTALL_DIR / "CloudToHDD-Backup.exe"
    shutil.copy2(app_src, app_dest)
    shutil.copy2(config_src, INSTALL_DIR / "config.example.yaml")
    if not (INSTALL_DIR / "config.yaml").exists():
        shutil.copy2(config_src, INSTALL_DIR / "config.yaml")

    desktop = Path(os.environ["USERPROFILE"]) / "Desktop" / f"{APP_NAME}.lnk"
    start_menu = (
        Path(os.environ["APPDATA"])
        / "Microsoft"
        / "Windows"
        / "Start Menu"
        / "Programs"
        / f"{APP_NAME}.lnk"
    )
    _create_shortcut(app_dest, desktop, APP_NAME)
    _create_shortcut(app_dest, start_menu, APP_NAME)


class InstallerUI(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"{APP_NAME} Setup")
        self.geometry("480x220")
        self.resizable(False, False)

        frame = ttk.Frame(self, padding=20)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text=APP_NAME, font=("Segoe UI", 16, "bold")).pack(anchor="w")
        ttk.Label(
            frame,
            text="Photo Backup from Cloud to Local HDD",
            font=("Segoe UI", 10),
        ).pack(anchor="w", pady=(4, 12))
        ttk.Label(frame, text=f"Install location:\n{INSTALL_DIR}", wraplength=420).pack(
            anchor="w", pady=(0, 16)
        )

        btn_row = ttk.Frame(frame)
        btn_row.pack(fill="x")
        ttk.Button(btn_row, text="Install", command=self._on_install).pack(side="left")
        ttk.Button(btn_row, text="Cancel", command=self.destroy).pack(side="right")

    def _on_install(self) -> None:
        try:
            install_app()
        except Exception as exc:
            messagebox.showerror("Install failed", str(exc), parent=self)
            return
        messagebox.showinfo(
            "Installed",
            f"{APP_NAME} was installed.\n\n"
            f"A shortcut was added to your Desktop and Start Menu.\n\n"
            f"Location:\n{INSTALL_DIR}",
            parent=self,
        )
        self.destroy()


def main() -> None:
    app = InstallerUI()
    app.mainloop()


if __name__ == "__main__":
    main()
