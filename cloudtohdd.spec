# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — single-file CloudToHDD Backup GUI."""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all

block_cipher = None
project_root = Path(SPECPATH)
icon_file = str(project_root / "assets" / "cloudtohdd.ico")

datas = [
    (str(project_root / "config.example.yaml"), "."),
    (str(project_root / "scripts"), "scripts"),
    (str(project_root / "assets" / "cloudtohdd.ico"), "."),
    (str(project_root / "assets" / "cloudtohdd-icon.png"), "."),
]

ctk_datas, ctk_binaries, ctk_hiddenimports = collect_all("customtkinter")

hiddenimports = list(ctk_hiddenimports) + [
    "PIL._tkinter_finder",
    "pillow_heif",
    "yaml",
    "openpyxl",
    "click",
    "rich",
    "rich.logging",
    "cryptography",
    "watchdog",
]

a = Analysis(
    ["gui.py"],
    pathex=[str(project_root)],
    binaries=ctk_binaries,
    datas=datas + ctk_datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="CloudToHDD-Backup",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_file,
)
