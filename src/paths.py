"""Resolve application paths for development and PyInstaller frozen builds."""

from __future__ import annotations

import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_root() -> Path:
    """Writable folder next to the exe (config, logs)."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def bundle_root() -> Path:
    """Read-only bundled resources (scripts, default config template)."""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS"))
    return Path(__file__).resolve().parents[1]


def scripts_dir() -> Path:
    return bundle_root() / "scripts"


def config_path() -> Path:
    return app_root() / "config.yaml"


def example_config_path() -> Path:
    for candidate in (
        app_root() / "config.example.yaml",
        bundle_root() / "config.example.yaml",
    ):
        if candidate.is_file():
            return candidate
    return app_root() / "config.example.yaml"


def ensure_app_config() -> Path:
    """Create config.yaml beside the exe from the bundled template if missing."""
    dest = config_path()
    if dest.exists():
        return dest
    example = example_config_path()
    if example.is_file():
        dest.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
    return dest
