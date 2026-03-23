from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path("data/logs")


def setup_logging() -> logging.Logger:
    """Configure application logging with console and file output."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("kaidan")
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    # Console
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    # File (rotating)
    fh = RotatingFileHandler(
        LOG_DIR / "kaidan.log", maxBytes=10 * 1024 * 1024, backupCount=5
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    return logger


def get_logger(name: str = "kaidan") -> logging.Logger:
    return logging.getLogger(name)
