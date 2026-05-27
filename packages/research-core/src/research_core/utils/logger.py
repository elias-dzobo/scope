"""Centralized logging configuration for the project."""

from __future__ import annotations

import logging
import os
import sys

_CONFIGURED = False
_NOISY_LOGGERS = (
    "ddgs",
    "ddgs.ddgs",
    "httpx",
    "httpcore",
    "primp",
    "urllib3",
)


def configure_logging(level: str | None = None) -> None:
    """Configure global logging once for the whole process."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    raw_level = (level or os.getenv("LOG_LEVEL", "INFO")).upper()
    log_level = getattr(logging, raw_level, logging.INFO)

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )

    root_logger.handlers.clear()
    root_logger.addHandler(handler)

    for logger_name in _NOISY_LOGGERS:
        logging.getLogger(logger_name).setLevel(logging.WARNING)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a module logger."""
    return logging.getLogger(name)
