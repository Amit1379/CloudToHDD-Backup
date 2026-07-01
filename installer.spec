# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — CloudToHDD Backup Setup (installer with shortcuts)."""

from pathlib import Path

block_cipher = None
project_root = Path(SPECPATH)

# Built after cloudtohdd.spec — app exe must exist in dist/
app_exe = project_root / "dist" / "CloudToHDD-Backup.exe"
if not app_exe.is_file():
    raise SystemExit(f"Build the app first: dist/CloudToHDD-Backup.exe not found ({app_exe})")

datas = [
    (str(app_exe), "."),
    (str(project_root / "config.example.yaml"), "."),
]

a = Analysis(
    ["installer/installer.py"],
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
    hiddenimports=[],
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
    name="CloudToHDD-Backup-Setup",
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
)
