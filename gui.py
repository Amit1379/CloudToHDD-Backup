#!/usr/bin/env python3
"""Launch CloudToHDD Backup GUI."""

from src.paths import ensure_app_config
from src.gui.app import run_gui

if __name__ == "__main__":
    ensure_app_config()
    run_gui()
