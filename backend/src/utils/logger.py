import json as json_mod
import logging
import sys
import uuid
from contextvars import ContextVar, Token
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


# Step-2 — per-request correlation id set by RequestIdMiddleware on every HTTP
# request. Defaults to ``None`` for non-HTTP contexts (CLI, background tasks).
_request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


def set_request_id(rid: str) -> Token:
    """Set the per-request id; returns the token needed to reset it."""

    return _request_id_var.set(rid)


def get_request_id() -> str | None:
    """Return the current request id, or ``None`` outside an HTTP request."""

    return _request_id_var.get()


from src.core.settings import LOGS_DIR  # noqa: E402  — after the ContextVar so importers see helpers


class _RunUuidFormatter(logging.Formatter):
    """Formatter that appends ``[run_uuid:...]`` when the contextvar is set."""

    def format(self, record: logging.LogRecord) -> str:  # noqa: D401 — short
        base = super().format(record)
        run_uuid = current_run_uuid()
        if run_uuid:
            return f"{base} [run_uuid:{run_uuid}]"
        return base


class JSONFormatter(logging.Formatter):
    # Standard LogRecord attributes — never re-emit these as extra fields.
    _SKIP: frozenset = frozenset({
        "name", "msg", "args", "created", "filename", "funcName",
        "levelname", "levelno", "lineno", "module", "msecs", "message",
        "pathname", "process", "processName", "relativeCreated",
        "thread", "threadName", "exc_info", "exc_text", "stack_info",
        "taskName", "asctime",
    })

    def format(self, record: logging.LogRecord) -> str:
        entry: dict = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "run_id": _RUN_ID,
        }
        run_uuid = current_run_uuid()
        if run_uuid:
            entry["run_uuid"] = run_uuid
        request_id = get_request_id()
        if request_id:
            entry["request_id"] = request_id
        # Capture any extra={...} fields passed at the call site.
        for k, v in record.__dict__.items():
            if k not in self._SKIP and not k.startswith("_") and k not in entry:
                entry[k] = v
        if record.exc_info and record.exc_info[0]:
            entry["exception"] = self.formatException(record.exc_info)
        return json_mod.dumps(entry, default=str)


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
    # Second handler — JSON lines for machine consumption.
    json_handler = RotatingFileHandler(
        LOGS_DIR / "job360.jsonl", maxBytes=5_000_000, backupCount=3
    )
    json_handler.setFormatter(JSONFormatter())
    logger.addHandler(json_handler)
    return logger


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"job360.{name}")


# ---------------------------------------------------------------------------
# Step-4 — dedicated audit logger
# ---------------------------------------------------------------------------

def setup_audit_logger() -> logging.Logger:
    """Configure and return the audit logger (job360.audit).

    Writes JSON lines to ``LOGS_DIR/audit.log`` with ``propagate=False``
    so audit records never appear in the main job360 handlers.  Idempotent
    — safe to call multiple times (e.g. from tests and from lifespan).
    """
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    audit = logging.getLogger("job360.audit")
    if audit.handlers:
        return audit
    audit.setLevel(logging.INFO)
    audit.propagate = False
    handler = RotatingFileHandler(
        LOGS_DIR / "audit.log", maxBytes=5_000_000, backupCount=5
    )
    handler.setFormatter(JSONFormatter())
    audit.addHandler(handler)
    return audit


def get_audit_logger() -> logging.Logger:
    """Return the audit logger; assumes setup_audit_logger() was already called."""

    return logging.getLogger("job360.audit")
