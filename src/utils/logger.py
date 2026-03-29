import logging
import re
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler

from src.config.settings import LOGS_DIR

# Patterns to redact from log output
_PII_PATTERNS = [
    (re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'), '[EMAIL_REDACTED]'),
    (re.compile(r'(?:api[_-]?key|token|password|secret)["\s:=]+["\']?[\w-]{8,}', re.IGNORECASE), '[KEY_REDACTED]'),
]


class PIISanitizingFormatter(logging.Formatter):
    """Formatter that redacts PII patterns (emails, API keys) from log messages."""

    def format(self, record):
        msg = super().format(record)
        for pattern, replacement in _PII_PATTERNS:
            msg = pattern.sub(replacement, msg)
        return msg


class _FlushFilter(logging.Filter):
    """Flush stdout after every log record (ensures subprocess output reaches pipe)."""

    def filter(self, record):
        sys.stdout.flush()
        return True


def setup_logging(log_level: str | None = None) -> logging.Logger:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("job360")
    if logger.handlers:
        if log_level:
            logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))
        return logger
    level = getattr(logging, log_level.upper(), logging.INFO) if log_level else logging.INFO
    logger.setLevel(level)
    fmt = PIISanitizingFormatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    console.addFilter(_FlushFilter())
    logger.addHandler(console)
    file_handler = RotatingFileHandler(
        LOGS_DIR / "job360.log", maxBytes=5_000_000, backupCount=3
    )
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)
    return logger


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"job360.{name}")
