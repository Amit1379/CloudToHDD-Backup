"""In-app connection for Google, OneDrive, iCloud, and Google Photos."""

from __future__ import annotations

import logging
import os
import platform
import subprocess
import webbrowser
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import yaml

from .utils import (
    detect_google_drive_folder,
    detect_icloud_folder,
    detect_onedrive_folder,
    find_rclone_executable,
)

logger = logging.getLogger("cloudtohdd.connect")

RCLONE_ZIP_URL = "https://downloads.rclone.org/rclone-current-windows-amd64.zip"


@dataclass
class CloudServiceSpec:
    key: str
    label: str
    rclone_type: str
    default_remote: str
    detect_folder: Callable[[], Path | None]
    install_url: str
    install_hint: str


@dataclass
class CloudConnectionStatus:
    provider: str
    label: str
    connected: bool
    method: str  # sync_folder | rclone | takeout | none
    detail: str
    folder_path: str | None = None
    rclone_remote: str | None = None


@dataclass
class ConnectResult:
    success: bool
    provider: str
    method: str
    message: str
    needs_user_action: bool = False


CLOUD_SERVICES: dict[str, CloudServiceSpec] = {
    "onedrive": CloudServiceSpec(
        key="onedrive",
        label="OneDrive",
        rclone_type="onedrive",
        default_remote="onedrive",
        detect_folder=detect_onedrive_folder,
        install_url="https://www.microsoft.com/microsoft-365/onedrive/download",
        install_hint="Install OneDrive for Windows, sign in, then click 'Use sync folder' again.",
    ),
    "google_drive": CloudServiceSpec(
        key="google_drive",
        label="Google Drive",
        rclone_type="drive",
        default_remote="gdrive",
        detect_folder=detect_google_drive_folder,
        install_url="https://www.google.com/drive/download/",
        install_hint="Install Google Drive for desktop, sign in, then click 'Use sync folder' again.",
    ),
    "google_photos": CloudServiceSpec(
        key="google_photos",
        label="Google Photos",
        rclone_type="gphotos",
        default_remote="gphotos",
        detect_folder=lambda: None,
        install_url="https://takeout.google.com/",
        install_hint="Export photos at takeout.google.com, or sign in with Google (rclone) below.",
    ),
    "icloud": CloudServiceSpec(
        key="icloud",
        label="iCloud Drive",
        rclone_type="iclouddrive",
        default_remote="icloud",
        detect_folder=detect_icloud_folder,
        install_url="https://www.icloud.com/icloud-for-windows/",
        install_hint="Install iCloud for Windows, enable iCloud Drive, then click 'Use sync folder' again.",
    ),
}


def ensure_rclone_installed() -> str:
    """Install rclone to %LOCALAPPDATA%\\rclone if missing. Returns path to rclone.exe."""
    existing = find_rclone_executable()
    if existing:
        return existing

    if platform.system() != "Windows":
        raise RuntimeError("Automatic rclone install is only supported on Windows.")

    install_dir = Path(os.environ.get("LOCALAPPDATA", "")) / "rclone"
    rclone_exe = install_dir / "rclone.exe"
    install_dir.mkdir(parents=True, exist_ok=True)

    zip_path = Path(os.environ.get("TEMP", ".")) / "rclone-download.zip"
    logger.info("Downloading rclone...")
    import urllib.request

    urllib.request.urlretrieve(RCLONE_ZIP_URL, zip_path)  # noqa: S310

    with zipfile.ZipFile(zip_path, "r") as zf:
        for member in zf.namelist():
            if member.endswith("rclone.exe"):
                zf.extract(member, install_dir)
                extracted = install_dir / member
                if extracted.is_file() and extracted != rclone_exe:
                    extracted.replace(rclone_exe)
                break

    zip_path.unlink(missing_ok=True)

    if not rclone_exe.is_file():
        raise RuntimeError("rclone download failed.")

    os.environ["PATH"] = str(install_dir) + os.pathsep + os.environ.get("PATH", "")
    logger.info("rclone installed at %s", rclone_exe)
    return str(rclone_exe)


def list_rclone_remotes(rclone_exe: str | None = None) -> list[str]:
    rclone = rclone_exe or find_rclone_executable()
    if not rclone:
        return []
    try:
        completed = subprocess.run(
            [rclone, "listremotes"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [line.strip() for line in completed.stdout.splitlines() if line.strip()]


def test_rclone_remote(remote_name: str, rclone_exe: str | None = None) -> tuple[bool, str]:
    rclone = rclone_exe or find_rclone_executable()
    if not rclone:
        return False, "rclone not installed"
    remote = remote_name if remote_name.endswith(":") else f"{remote_name}:"
    try:
        completed = subprocess.run(
            [rclone, "lsd", remote, "--max-depth", "1"],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    if completed.returncode == 0:
        return True, "Cloud account connected"
    err = (completed.stderr or completed.stdout or "Connection failed").strip()
    return False, err[:300]


def launch_rclone_sign_in(provider: str) -> ConnectResult:
    """Sign in via browser using rclone OAuth (opens default browser)."""
    return connect_online(provider)


def connect_online(provider: str) -> ConnectResult:
    """Open browser for cloud sign-in and save credentials in rclone."""
    spec = CLOUD_SERVICES.get(provider)
    if not spec:
        return ConnectResult(False, provider, "rclone", f"Unknown provider: {provider}")

    if provider == "google_photos":
        webbrowser.open("https://takeout.google.com/")
        return ConnectResult(
            False,
            provider,
            "takeout",
            "Google Photos direct API backup is limited.\n"
            "Opened takeout.google.com — export your photos, then use 'Set Takeout folder'.\n"
            "Alternatively try Sign in with Google (uses Drive API; may not include all Photos).",
            needs_user_action=True,
        )

    try:
        rclone = ensure_rclone_installed()
    except RuntimeError as exc:
        return ConnectResult(False, provider, "rclone", str(exc))

    config = _load_config()
    remote_name = config.get("providers", {}).get(provider, {}).get("rclone_remote", spec.default_remote)
    remotes = list_rclone_remotes(rclone)
    remote_token = f"{remote_name}:"

    if remote_token in remotes:
        ok, msg = test_rclone_remote(remote_name, rclone)
        if ok:
            _apply_rclone_config(provider, remote_name)
            return ConnectResult(True, provider, "rclone", f"{spec.label} already connected ({remote_name})")
        logger.info("Reconnecting rclone remote %s", remote_name)
        try:
            completed = subprocess.run(
                [rclone, "config", "reconnect", remote_token],
                capture_output=True,
                text=True,
                check=False,
                timeout=300,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return ConnectResult(False, provider, "rclone", str(exc))
        if completed.returncode != 0:
            err = (completed.stderr or completed.stdout or "Reconnect failed").strip()
            return ConnectResult(False, provider, "rclone", err[:400], needs_user_action=True)
    else:
        logger.info("Authorizing new rclone remote %s (%s)", remote_name, spec.rclone_type)
        auth_args = [rclone, "authorize", spec.rclone_type]
        if spec.rclone_type == "drive":
            auth_args.extend(["scope", "drive.readonly"])
        try:
            completed = subprocess.run(
                auth_args,
                capture_output=True,
                text=True,
                check=False,
                timeout=300,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return ConnectResult(False, provider, "rclone", str(exc))

        token = (completed.stdout or "").strip()
        if completed.returncode != 0 or not token.startswith("{"):
            err = (completed.stderr or completed.stdout or "Authorization failed").strip()
            return ConnectResult(
                False,
                provider,
                "rclone",
                f"Browser sign-in failed: {err[:300]}",
                needs_user_action=True,
            )

        create_cmd = [
            rclone,
            "config",
            "create",
            remote_name,
            spec.rclone_type,
            "config_token",
            token,
        ]
        if spec.rclone_type == "drive":
            create_cmd.extend(["scope", "drive.readonly"])
        try:
            created = subprocess.run(
                create_cmd,
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return ConnectResult(False, provider, "rclone", str(exc))
        if created.returncode != 0:
            err = (created.stderr or created.stdout or "config create failed").strip()
            return ConnectResult(False, provider, "rclone", err[:400])

    ok, msg = test_rclone_remote(remote_name, rclone)
    if ok:
        _apply_rclone_config(provider, remote_name)
        return ConnectResult(
            True,
            provider,
            "rclone",
            f"{spec.label} connected via cloud sign-in (remote: {remote_name})",
        )
    return ConnectResult(
        False,
        provider,
        "rclone",
        f"Sign-in incomplete: {msg}",
        needs_user_action=True,
    )


def launch_rclone_sign_in_legacy_console(provider: str) -> ConnectResult:
    """Open a console window for rclone OAuth sign-in (fallback)."""
    spec = CLOUD_SERVICES.get(provider)
    if not spec:
        return ConnectResult(False, provider, "rclone", f"Unknown provider: {provider}")

    try:
        rclone = ensure_rclone_installed()
    except RuntimeError as exc:
        return ConnectResult(False, provider, "rclone", str(exc))

    cfg = _load_config()
    remote_name = cfg.get("providers", {}).get(provider, {}).get("rclone_remote", spec.default_remote)
    remotes = list_rclone_remotes(rclone)
    remote_token = f"{remote_name}:"

    if remote_token in remotes:
        cmd = [rclone, "config", "reconnect", remote_token]
        title = f"CloudToHDD Backup - Reconnect {spec.label}"
        hint = f"Complete sign-in for existing remote '{remote_name}' in the window that opens."
    else:
        cmd = [rclone, "config", "create", remote_name, spec.rclone_type]
        title = f"CloudToHDD Backup - Connect {spec.label}"
        hint = (
            f"A command window will open. Follow prompts to sign in to {spec.label}.\n"
            "When finished, close the window and click Refresh in this tool."
        )

    if platform.system() == "Windows":
        subprocess.Popen(  # noqa: S603
            ["cmd", "/c", "start", title, "cmd", "/k", *cmd],
        )
    else:
        subprocess.Popen(cmd)  # noqa: S603

    return ConnectResult(
        True,
        provider,
        "rclone",
        hint,
        needs_user_action=True,
    )


def connect_sync_folder(provider: str, folder: Path | None = None) -> ConnectResult:
    """Use desktop sync folder (OneDrive / Google Drive / iCloud app)."""
    spec = CLOUD_SERVICES.get(provider)
    if not spec or provider == "google_photos":
        return ConnectResult(False, provider, "sync_folder", "Use Takeout or rclone for Google Photos.")

    path = folder or spec.detect_folder()
    if not path or not path.is_dir():
        webbrowser.open(spec.install_url)
        return ConnectResult(
            False,
            provider,
            "sync_folder",
            f"No {spec.label} folder found. Opened download page — {spec.install_hint}",
            needs_user_action=True,
        )

    _apply_sync_config(provider, path)
    return ConnectResult(
        True,
        provider,
        "sync_folder",
        f"{spec.label} connected via sync folder:\n{path}",
    )


def connect_takeout_folder(provider: str, folder: Path) -> ConnectResult:
    if provider != "google_photos":
        return ConnectResult(False, provider, "takeout", "Takeout folder only applies to Google Photos.")
    if not folder.is_dir():
        return ConnectResult(False, provider, "takeout", f"Folder not found: {folder}")

    config = _load_config()
    providers = config.setdefault("providers", {})
    gp = providers.setdefault("google_photos", {})
    gp["enabled"] = True
    gp["method"] = "takeout"
    gp["takeout_download_folder"] = str(folder)
    gp.setdefault("auto_extract", True)
    gp.setdefault("rclone_remote", "gphotos")
    _save_config(config)

    return ConnectResult(
        True,
        provider,
        "takeout",
        f"Google Photos Takeout folder set:\n{folder}\n\n"
        "Export at takeout.google.com and save ZIPs to this folder.",
    )


def get_connection_status(provider: str, config: dict | None = None) -> CloudConnectionStatus:
    spec = CLOUD_SERVICES.get(provider)
    if not spec:
        return CloudConnectionStatus(provider, provider, False, "none", "Unknown provider")

    config = config or _load_config()
    cfg = config.get("providers", {}).get(provider, {})
    if not cfg.get("enabled", False):
        folder = spec.detect_folder()
        if folder:
            return CloudConnectionStatus(
                provider,
                spec.label,
                False,
                "detected",
                f"Desktop app found ({folder}) — connect to enable backup",
                folder_path=str(folder),
            )
        return CloudConnectionStatus(
            provider,
            spec.label,
            False,
            "none",
            "Not connected — sign in below to enable backup",
        )

    # Google Photos Takeout
    if provider == "google_photos":
        takeout = cfg.get("takeout_download_folder", "")
        if takeout and Path(takeout).is_dir():
            return CloudConnectionStatus(
                provider, spec.label, True, "takeout", "Takeout download folder configured",
                folder_path=takeout,
            )
        remote = cfg.get("rclone_remote", spec.default_remote)
        ok, msg = test_rclone_remote(remote)
        if ok:
            return CloudConnectionStatus(
                provider, spec.label, True, "rclone", msg, rclone_remote=remote,
            )
        return CloudConnectionStatus(
            provider, spec.label, False, "none",
            "Connect Takeout folder or sign in with Google",
        )

    # Sync folder
    sync = cfg.get("sync_folder", "").strip()
    folder = Path(sync) if sync else spec.detect_folder()
    method = cfg.get("method", "auto")
    if folder and folder.is_dir() and method in ("sync_folder", "auto"):
        return CloudConnectionStatus(
            provider, spec.label, True, "sync_folder",
            f"Sync folder: {folder}", folder_path=str(folder),
        )

    # rclone
    remote = cfg.get("rclone_remote", spec.default_remote)
    if method in ("rclone", "auto"):
        ok, msg = test_rclone_remote(remote)
        if ok:
            return CloudConnectionStatus(
                provider, spec.label, True, "rclone", msg, rclone_remote=remote,
            )
        if f"{remote}:" in list_rclone_remotes():
            return CloudConnectionStatus(
                provider, spec.label, False, "rclone",
                f"Remote '{remote}' exists but needs sign-in — click Connect",
                rclone_remote=remote,
            )

    return CloudConnectionStatus(
        provider, spec.label, False, "none",
        f"Not connected — install {spec.label} app or sign in below",
    )


def get_all_connection_statuses(config: dict | None = None) -> list[CloudConnectionStatus]:
    config = config or _load_config()
    return [get_connection_status(key, config) for key in CLOUD_SERVICES]


_active_config_path: Path | None = None


def set_config_path(path: Path | str) -> None:
    """Use the same config file as BackupEngine / CLI --config."""
    global _active_config_path
    _active_config_path = Path(path)


def _config_path() -> Path:
    if _active_config_path is not None:
        return _active_config_path
    return Path(__file__).resolve().parents[1] / "config.yaml"


def _load_config() -> dict:
    path = _config_path()
    if path.exists():
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {}


def _save_config(config: dict) -> None:
    _config_path().write_text(yaml.dump(config, default_flow_style=False, sort_keys=False), encoding="utf-8")


def _apply_sync_config(provider: str, folder: Path) -> None:
    config = _load_config()
    providers = config.setdefault("providers", {})
    cfg = providers.setdefault(provider, {})
    cfg["enabled"] = True
    cfg["method"] = "auto"
    cfg["sync_folder"] = str(folder)
    cfg.setdefault("rclone_remote", CLOUD_SERVICES[provider].default_remote)
    _save_config(config)


def _apply_rclone_config(provider: str, remote_name: str) -> None:
    config = _load_config()
    providers = config.setdefault("providers", {})
    cfg = providers.setdefault(provider, {})
    cfg["enabled"] = True
    cfg["method"] = "rclone" if provider == "google_photos" else "auto"
    cfg["rclone_remote"] = remote_name
    _save_config(config)


def finalize_rclone_connection(provider: str) -> ConnectResult:
    """Call after user completes OAuth in external window."""
    spec = CLOUD_SERVICES[provider]
    config = _load_config()
    remote = config.get("providers", {}).get(provider, {}).get("rclone_remote", spec.default_remote)
    ok, msg = test_rclone_remote(remote)
    if ok:
        _apply_rclone_config(provider, remote)
        return ConnectResult(True, provider, "rclone", f"{spec.label} connected via cloud API ({remote})")
    return ConnectResult(
        False, provider, "rclone",
        f"Sign-in not complete yet: {msg}",
        needs_user_action=True,
    )
