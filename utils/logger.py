"""Console + daily-rotating file logging."""
import logging
import os
from datetime import date

from config import Config

_FMT = "%(asctime)s | %(levelname)-7s | %(name)-12s | %(message)s"


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(Config.LOG_LEVEL.upper())

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter(_FMT))
    logger.addHandler(console)

    try:
        os.makedirs(Config.STATE_DIR, exist_ok=True)
        fh = logging.FileHandler(
            os.path.join(Config.STATE_DIR, f"{date.today().isoformat()}.log")
        )
        fh.setFormatter(logging.Formatter(_FMT))
        logger.addHandler(fh)
    except OSError:
        pass  # read-only filesystem (e.g. some containers) — console only

    logger.propagate = False
    return logger
