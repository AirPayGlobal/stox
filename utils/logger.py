"""
Centralised logging configuration.
"""
import logging
import os
from logging.handlers import TimedRotatingFileHandler

_LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
os.makedirs(_LOG_DIR, exist_ok=True)


def get_logger(name: str) -> logging.Logger:
    from config import Config

    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    level = getattr(logging, Config.LOG_LEVEL.upper(), logging.INFO)
    logger.setLevel(level)

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    # Rotating file handler — always writes to logs/stox.log, rotates at
    # midnight UTC and keeps 30 days of backups.  Using a fixed filename means
    # the API can always read the current log regardless of when the bot started.
    log_file = os.path.join(_LOG_DIR, "stox.log")
    fh = TimedRotatingFileHandler(
        log_file,
        when="midnight",
        interval=1,
        backupCount=30,
        utc=True,
    )
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    return logger
