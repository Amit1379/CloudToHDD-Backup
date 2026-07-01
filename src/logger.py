"""Structured logging with rotation and console output."""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timedelta
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler


def setup_logging(level: str, log_dir: Path, keep_days: int) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("cloudtohdd")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.handlers.clear()

    log_file = log_dir / f"backup_{datetime.now().strftime('%Y%m%d')}.log"
    file_handler = TimedRotatingFileHandler(
        log_file,
        when="midnight",
        backupCount=max(keep_days, 1),
        encoding="utf-8",
    )
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")
    )
    logger.addHandler(file_handler)

    console = Console(stderr=True)
    rich_handler = RichHandler(
        console=console,
        show_time=True,
        show_path=False,
        markup=True,
        rich_tracebacks=True,
    )
    rich_handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(rich_handler)

    _purge_old_logs(log_dir, keep_days)
    return logger


def _purge_old_logs(log_dir: Path, keep_days: int) -> None:
    cutoff = datetime.now() - timedelta(days=keep_days)
    for log_path in log_dir.glob("backup_*.log*"):
        try:
            mtime = datetime.fromtimestamp(log_path.stat().st_mtime)
            if mtime < cutoff:
                log_path.unlink(missing_ok=True)
        except OSError:
            continue
