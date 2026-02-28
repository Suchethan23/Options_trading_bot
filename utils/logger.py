"""
utils/logger.py
Structured, rotating file logger with console output.
"""
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path


def get_logger(name: str) -> logging.Logger:
    """
    Returns a named logger with rotating file + console handlers.
    Call once per module: logger = get_logger(__name__)
    """
    from config.settings import LOG_LEVEL, LOG_FILE

    logger = logging.getLogger(name)

    # Avoid adding duplicate handlers when called multiple times
    if logger.handlers:
        return logger

    logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))

    fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)-30s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # ── Console handler ──────────────────────────────────────────────────────
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    # ── Rotating file handler ────────────────────────────────────────────────
    log_path = Path(LOG_FILE)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fh = RotatingFileHandler(
        log_path,
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
        encoding="utf-8",
    )
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    return logger
