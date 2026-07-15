"""Shared dependencies for FastAPI routes."""
import os
import tempfile

from src.core.settings import DB_PATH
from src.repositories.database import JobDatabase

_db: JobDatabase | None = None


async def init_db() -> JobDatabase:
    global _db
    if _db is None:
        _db = JobDatabase(str(DB_PATH))
        await _db.init_db()
        # Batch 2: apply additive migrations (users, sessions, user_feed,
        # notification_ledger, user_channels). Idempotent — safe to call on
        # every boot. See docs/plans/batch-2-plan.md Phase 0.
        from migrations import runner

        await runner.up(str(DB_PATH))
    return _db


async def get_db() -> JobDatabase:
    """The boot singleton — schema owner. Used at boot + by tests for data setup.
    Routes should depend on ``get_request_db`` instead (per-request connection)."""
    if _db is None:
        await init_db()
    return _db


async def get_request_db():
    """Per-request DB connection dependency (docs/fable/02 — the P0 fix).

    Each request gets its OWN short-lived connection instead of the process-wide
    shared singleton. psycopg3 forbids using one async connection from two coroutines
    at once, so the shared connection could interleave/corrupt concurrent requests
    ("another operation is already in progress") and never recovered after a DB
    restart. A fresh connection per request is concurrency-safe and self-healing.
    Schema + migrations still run ONCE at boot via ``init_db()``; this only opens a
    connection (no DDL). FastAPI closes it after the response via the finally block.
    """
    if _db is None:
        await init_db()  # ensure schema + migrations applied once (idempotent)
    db = JobDatabase(str(DB_PATH))
    await db.connect()
    try:
        yield db
    finally:
        await db.close()


async def close_db():
    global _db
    if _db is not None:
        await _db.close()
        _db = None


def save_upload_to_temp(content: bytes, suffix: str) -> str:
    fd, path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "wb") as f:
        f.write(content)
    return path
