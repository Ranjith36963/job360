import hashlib
import json as json_mod
import logging
import sys
import uuid
from contextvars import ContextVar, Token
from logging.handlers import RotatingFileHandler
from typing import Any, Optional

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


def set_request_id(rid: str) -> Token[str | None]:
    """Set the per-request id; returns the token needed to reset it."""

    return _request_id_var.set(rid)


def get_request_id() -> str | None:
    """Return the current request id, or ``None`` outside an HTTP request."""

    return _request_id_var.get()


from src.core.settings import LOGS_DIR  # noqa: E402  — after the ContextVar so importers see helpers


def _scrub(value: object) -> object:
    """Escape CR/LF (and other control chars) in a value destined for a log line.

    Non-strings pass through untouched so ``%d``/``%s`` formatting still works.
    """
    if not isinstance(value, str):
        return value
    if "\r" not in value and "\n" not in value:
        return value
    return value.replace("\r", "\\r").replace("\n", "\\n")


class CRLFScrubFilter(logging.Filter):
    """Neutralise log-injection (CodeQL py/log-injection) for EVERY call site.

    User-controlled values — emails, usernames, GitHub handles, channel
    credentials — reach ``logger.info("... %s", value)`` in ~30 places. A value
    containing a newline lets an attacker forge whole log lines ("…login
    status=success…"), poisoning the audit trail an operator reads during an
    incident.

    Fixing 30 call sites individually is unmaintainable and the 31st would slip
    through, so the escaping happens once here: attached to the ``job360``
    logger, this filter escapes CR/LF in the message and in every ``%``-style
    argument before any handler formats the record. The JSON handler was already
    safe (json.dumps escapes newlines); this closes the plain-text console/file
    handlers, which are what operators actually read.
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D401 — short
        if isinstance(record.msg, str):
            record.msg = _scrub(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: _scrub(v) for k, v in record.args.items()}
            else:
                record.args = tuple(_scrub(a) for a in record.args)
        return True  # never drop a record — this filter only sanitises


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
    _SKIP: frozenset[str] = frozenset({
        "name", "msg", "args", "created", "filename", "funcName",
        "levelname", "levelno", "lineno", "module", "msecs", "message",
        "pathname", "process", "processName", "relativeCreated",
        "thread", "threadName", "exc_info", "exc_text", "stack_info",
        "taskName", "asctime",
    })

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
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
    # File handlers are a CONVENIENCE, not the product — stdout is the channel
    # Railway (and any container host) actually captures. On 2026-07-30 the
    # worker's 04:00 refresh_catalog cron died HERE, before fetching a single
    # job: its image installs the package into site-packages, so LOGS_DIR
    # resolved to /usr/local/lib/python3.12/site-packages/data/logs and
    # mkdir() raised PermissionError. Three silent nights, empty catalog,
    # absence detector's issue #170. A log file must never take down the
    # pipeline it exists to describe.
    file_handlers: list[logging.Handler] = []
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            LOGS_DIR / "job360.log", maxBytes=5_000_000, backupCount=3
        )
        file_handler.setFormatter(fmt)
        logger.addHandler(file_handler)
        # Second handler — JSON lines for machine consumption.
        json_handler = RotatingFileHandler(
            LOGS_DIR / "job360.jsonl", maxBytes=5_000_000, backupCount=3
        )
        json_handler.setFormatter(JSONFormatter())
        logger.addHandler(json_handler)
        file_handlers = [file_handler, json_handler]
    except OSError as exc:
        # Never quiet about being quiet: say where file logs would have gone.
        console.handle(
            logging.LogRecord(
                "job360", logging.WARNING, __file__, 0,
                "file logging disabled (%s: %s) — continuing console-only",
                (LOGS_DIR, exc), None,
            )
        )
    # Log-injection guard. MUST be attached to the HANDLERS, not to the logger:
    # a logger-level filter only sees records logged directly on that logger,
    # while every finding lives on CHILD loggers (job360.api.auth, …) whose
    # records merely propagate to these handlers. Handler filters see them all.
    _scrubber = CRLFScrubFilter()
    for _h in (console, *file_handlers):
        _h.addFilter(_scrubber)
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
    audit = logging.getLogger("job360.audit")
    # Idempotence must key on OUR handler, not on "any handler present".
    # `job360.audit` is a process-global name: pytest's caplog attaches its
    # LogCaptureHandlers to it, and any library can too. The old guard
    # (`if audit.handlers`) saw a foreign handler and returned early — WITHOUT
    # installing the audit file/stdout handler, its CRLF scrubber, or the DB
    # tee. In that state audit records went wherever the foreign handler
    # pointed and the durable audit_log table got nothing.
    if any(getattr(h, "_job360_audit", False) for h in audit.handlers):
        return audit
    audit.setLevel(logging.INFO)
    audit.propagate = False
    # Same read-only-container rule as setup_logging above: the audit FILE is a
    # convenience copy. On OSError we fall back to stdout — the audit trail
    # keeps flowing (host log capture + the DB tee below), just not to a file.
    handler: logging.Handler
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            LOGS_DIR / "audit.log", maxBytes=5_000_000, backupCount=5
        )
    except OSError:
        handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())
    # The audit trail is the MOST injection-sensitive stream — it records login
    # attempts with attacker-supplied emails, and it is what an operator reads
    # during an incident. propagate=False means it never reaches the main
    # handlers' scrubber, so it needs its own.
    handler.addFilter(CRLFScrubFilter())
    # Idempotence marker (see guard above); setattr keeps mypy clean on the union type.
    setattr(handler, "_job360_audit", True)  # noqa: B010 — deliberate dynamic marker
    audit.addHandler(handler)
    # docs/fable/05 C8 — tee the same records into the audit_log DB table so
    # audit history survives file rotation and is queryable with SQL. Lazy
    # import: audit_trail pulls in the DB layer, which this low-level logging
    # module must not import at module scope.
    from src.services.audit_trail import install_db_audit_trail

    install_db_audit_trail(audit)
    return audit


def get_audit_logger() -> logging.Logger:
    """Return the audit logger; assumes setup_audit_logger() was already called."""

    return logging.getLogger("job360.audit")


def mask_email(email: Optional[str]) -> str:
    """Mask an address for logs: ``alice@example.com`` -> ``a***@example.com``.

    Audit M9 — plaintext emails were being written to on-disk logs (which are
    rotated, shipped and grepped, and long outlive the request). An address is
    personal data; the log only ever needs enough to correlate a support ticket,
    not to identify the person.

    Keeps the FIRST character + the domain: enough to answer "is this the same
    user as that other line?" and "did it go to the right provider?", while the
    local-part — the identifying half — is gone. The domain is retained
    deliberately: it carries the delivery-debugging value (bounces, DNS, spam
    filtering) with far less identifying power than the local-part.

    Degrades safely: None/empty -> "<none>", a non-address string -> fully
    masked rather than echoed, so a mis-passed value can't leak by accident.
    """
    if not email:
        return "<none>"
    local, sep, domain = str(email).partition("@")
    if not sep or not domain:
        # Not an address — never echo an unknown value into the log.
        return "***"
    if not local:
        return f"***@{domain}"
    return f"{local[0]}***@{domain}"


def mask_ip(ip: Optional[str]) -> str:
    """Mask a client IP for logs: ``203.0.113.7`` -> ``ip_3f2a9c1e0b7d``.

    M9 — raw client IPs were being written to on-disk rotating access logs
    (``data/logs/job360.log`` / ``.jsonl``), which are rotated, shipped and
    grepped, and long outlive the request. An IP is personal data under GDPR;
    the log only ever needs a stable token to answer "same caller as that
    other line?", not the actual address.

    Non-reversible: a truncated SHA-256 hash, prefixed so it reads as a token
    rather than a real value at a glance. Same input always yields the same
    token (stable for correlation); the raw IP cannot be recovered from it.

    Degrades safely: None/empty -> "<none>".
    """
    if not ip:
        return "<none>"
    digest = hashlib.sha256(str(ip).encode("utf-8")).hexdigest()[:12]
    return f"ip_{digest}"
