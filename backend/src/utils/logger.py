import json as json_mod
import logging
import sys
import uuid
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler

_RUN_ID = uuid.uuid4().hex[:8]

# Step-1 S1 — per-invocation correlation id. ``run_search`` calls
# :func:`set_run_uuid` once at the top so every subsequent log line in the
# same async task tree carries the same uuid. Defaults to ``None`` when no
# pipeline run is in flight (e.g. plain CLI subcommands like ``status``).
_run_uuid_var: ContextVar[str | None] = ContextVar("run_uuid", default=None)


def set_run_uuid(uuid_str: str) -> None:
    """Set the per-run correlation id for the current async context."""

    _run_uuid_var.set(uuid_str)


def current_run_uuid() -> str | None:
    """Read the per-run correlation id, or ``None`` outside a run."""

    return _run_uuid_var.get()


from src.core.settings import LOGS_DIR  # noqa: E402  — after the ContextVar so importers see helpers


class _RunUuidFormatter(logging.Formatter):
    """Formatter that appends ``[run_uuid:...]`` when the contextvar is set."""

    def format(self, record: logging.LogRecord) -> str:  # noqa: D401 — short
        base = super().format(record)
        run_uuid = current_run_uuid()
        if run_uuid:
            return f"{base} [run_uuid:{run_uuid}]"
        return base


def setup_logging(log_level: str | None = None) -> logging.Logger:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("job360")
    if logger.handlers:
        if log_level:
            logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))
        return logger
    level = getattr(logging, log_level.upper(), logging.INFO) if log_level else logging.INFO
    logger.setLevel(level)
    fmt = _RunUuidFormatter(
        f"%(asctime)s [%(levelname)s] %(name)s [run:{_RUN_ID}]: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    logger.addHandler(console)
    file_handler = RotatingFileHandler(LOGS_DIR / "job360.log", maxBytes=5_000_000, backupCount=3)
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
        run_uuid = current_run_uuid()
        if run_uuid:
            entry["run_uuid"] = run_uuid
        if record.exc_info and record.exc_info[0]:
            entry["exception"] = self.formatException(record.exc_info)
        return json_mod.dumps(entry)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"job360.{name}")
