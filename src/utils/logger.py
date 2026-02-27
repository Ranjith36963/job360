import logging
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler

from src.config.settings import LOGS_DIR


def setup_logging() -> logging.Logger:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("job360")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    logger.addHandler(console)
    file_handler = RotatingFileHandler(
        LOGS_DIR / "job360.log", maxBytes=5_000_000, backupCount=3
    )
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)
    return logger


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"job360.{name}")
