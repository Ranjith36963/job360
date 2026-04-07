import json as json_mod
import logging
import sys
import uuid
from pathlib import Path
from logging.handlers import RotatingFileHandler

_RUN_ID = uuid.uuid4().hex[:8]

from src.config.settings import LOGS_DIR


def setup_logging(log_level: str | None = None) -> logging.Logger:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("job360")
    if logger.handlers:
        if log_level:
            logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))
        return logger
    level = getattr(logging, log_level.upper(), logging.INFO) if log_level else logging.INFO
    logger.setLevel(level)
    fmt = logging.Formatter(
        f"%(asctime)s [%(levelname)s] %(name)s [run:{_RUN_ID}]: %(message)s",
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


class JSONFormatter(logging.Formatter):
    def format(self, record):
        entry = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "run_id": _RUN_ID,
        }
        if record.exc_info and record.exc_info[0]:
            entry["exception"] = self.formatException(record.exc_info)
        return json_mod.dumps(entry)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"job360.{name}")
