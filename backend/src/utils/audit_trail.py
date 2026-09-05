"""DB-backed audit trail (docs/fable/05 C8).

Lived at ``src/services/audit_trail.py`` until slice 5 (#483). It moved here
because ``src/services/`` was the sourcing engine's house and that house is
gone — this is a SECURITY facility (logins, magic links, account mutations),
not part of the deleted era, and deleting it would have been a regression.

The audit logger (``job360.audit``) fires at every security-relevant event —
register, login ok/fail/locked, logout, magic-link request/consume, channel
CRUD, account mutations — but wrote only rotating JSON files. Rotation deletes
history, and files aren't queryable. This module tees the SAME records into the
``audit_log`` table (migration 0025), so no call site changes and every future
``get_audit_logger().info(...)`` is persisted automatically.

Design constraints (all deliberate):
- **Never block the event loop.** Records go through a stdlib ``QueueHandler``
  to a ``QueueListener`` daemon thread; the DB write (blocking psycopg via
  ``pgsync``) happens there. A Postgres hiccup can never stall a login.
- **Never raise into the request path.** Any failure in the writer is swallowed
  (after one self-heal attempt). Losing one audit row beats failing auth.
- **No PII beyond what the event needs.** The ``email`` extra (present on
  failed-login / magic-link records for the FILE log) is deliberately NOT
  persisted to the DB: DB rows are user_id-keyed so GDPR erasure can anonymise
  them; a raw email in a detail blob would dodge that.
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import queue
import time
from typing import Any, Optional

# One audit_log row: (event, status, user_id, client_ip, user_agent, detail).
# The middle three come out of LogRecord extras, so they stay `Any`.
_AuditRow = tuple[str, str, Any, Any, Any, Optional[str]]

# Attributes every LogRecord carries — everything else in record.__dict__ was
# passed via ``extra=`` and belongs in the audit row.
_STD_RECORD_ATTRS = frozenset(vars(logging.makeLogRecord({})).keys()) | {
    "message",
    "asctime",
    "taskName",
}

# extras promoted to real columns; the remainder (minus the PII denylist) goes
# into the JSON ``detail`` column.
_COLUMN_EXTRAS = ("event", "status", "user_id", "client_ip", "user_agent")
_DETAIL_DENYLIST = frozenset({"email"})

_queue: queue.Queue[logging.LogRecord] = queue.Queue(maxsize=10_000)
_listener: Optional[logging.handlers.QueueListener] = None


class _DBAuditHandler(logging.Handler):
    """Writes one ``audit_log`` row per audit record. Runs on the listener thread."""

    def __init__(self) -> None:
        super().__init__()
        self._ensured_paths: set[str] = set()

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _db_path() -> str:
        # Read DB_PATH dynamically (not from-import): test fixtures monkeypatch
        # settings.DB_PATH per test, and the per-path schema isolation must see
        # the CURRENT value at write time, not whatever was set at import.
        from src.core import settings

        return str(settings.DB_PATH)

    @staticmethod
    def _row_from(record: logging.LogRecord) -> _AuditRow:
        extras = {
            k: v for k, v in record.__dict__.items() if k not in _STD_RECORD_ATTRS
        }
        event = extras.pop("event", record.getMessage() or "unknown")
        status = extras.pop("status", "ok")
        user_id = extras.pop("user_id", None)
        client_ip = extras.pop("client_ip", None)
        user_agent = extras.pop("user_agent", None)
        detail_src = {
            k: v for k, v in extras.items() if k not in _DETAIL_DENYLIST
        }
        detail = json.dumps(detail_src, default=str) if detail_src else None
        return (str(event), str(status), user_id, client_ip, user_agent, detail)

    def _write(self, row: _AuditRow) -> None:
        from src.repositories import pgsync

        conn = pgsync.connect(self._db_path())
        try:
            conn.execute(
                "INSERT INTO audit_log (event, status, user_id, client_ip, user_agent, detail) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                row,
            )
            conn.commit()
        finally:
            conn.close()

    # -- Handler API ----------------------------------------------------------

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D102
        try:
            row = self._row_from(record)
            try:
                self._write(row)
            except Exception:
                # Self-heal once: the table may not exist yet (fresh test schema
                # where migration 0025 hasn't run). Create it and retry.
                path = self._db_path()
                if path in self._ensured_paths:
                    return
                self._ensured_paths.add(path)
                from pathlib import Path

                from src.repositories import pgsync

                ddl = (
                    Path(__file__).resolve().parents[2]
                    / "migrations"
                    / "0025_audit_log.up.sql"
                ).read_text()
                conn = pgsync.connect(path)
                try:
                    conn.executescript(ddl)
                    conn.commit()
                finally:
                    conn.close()
                self._write(row)
        except Exception:  # noqa: BLE001 — an audit failure must NEVER surface
            return


def install_db_audit_trail(audit_logger: logging.Logger) -> None:
    """Tee ``audit_logger`` records into the audit_log table. Idempotent."""
    global _listener
    if any(isinstance(h, logging.handlers.QueueHandler) for h in audit_logger.handlers):
        return
    audit_logger.addHandler(logging.handlers.QueueHandler(_queue))
    if _listener is None:
        _listener = logging.handlers.QueueListener(_queue, _DBAuditHandler())
        _listener.start()  # daemon thread — never blocks process exit


def flush(timeout: float = 5.0) -> None:
    """Wait until queued audit records have been written. Test helper."""
    deadline = time.monotonic() + timeout
    while not _queue.empty() and time.monotonic() < deadline:
        time.sleep(0.02)
    # The listener may still be mid-write on the last record; one more beat.
    time.sleep(0.05)
