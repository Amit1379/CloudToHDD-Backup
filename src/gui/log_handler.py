"""Queue-based logging handler for GUI live log view."""

from __future__ import annotations

import logging
import queue
from typing import Callable


class QueueLogHandler(logging.Handler):
    def __init__(self, log_queue: queue.Queue):
        super().__init__()
        self.log_queue = log_queue
        self.setFormatter(
            logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", "%H:%M:%S")
        )

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.log_queue.put(self.format(record))
        except Exception:
            self.handleError(record)


def attach_gui_logging(log_queue: queue.Queue, level: str = "INFO") -> None:
    root_logger = logging.getLogger("cloudtohdd")
    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    handler = QueueLogHandler(log_queue)
    root_logger.addHandler(handler)
