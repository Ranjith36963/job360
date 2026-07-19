"""Shared dependencies for FastAPI routes."""
import os
import tempfile
from typing import AsyncIterator, cast

from src.core.settings import DB_PATH
from src.repositories.database import JobDatabase
from src.utils.logger import get_logger

logger = get_logger("api.dependencies")

_db: JobDatabase | None = None


async def init_db() -> JobDatabase:
    global _db
    if _db is None:
        _db = JobDatabase(str(DB_PATH))
        await _db.init_db()
        # Batch 2: apply additive migrations (users, sessions, user_feed,
        # notification_ledger, user_channels). Idempotent — safe to call on
        # every boot. See docs/plans/batch-2-plan.md Phase 0.
        #
        # H6 — migrations run INSIDE the request-serving process at boot. The
        # finding's fix is to move them to an explicit pre-deploy/release step.
        # That is a deploy-pipeline change (Railway release phase), not something
        # this repo can perform on its own — but it cannot happen at all while
        # the code unconditionally migrates on boot. This env switch is the
        # enabler: set RUN_MIGRATIONS_ON_BOOT=false once a release step owns
        # migrations, and boot stops racing it.
        #
        # Default stays TRUE, so behaviour is unchanged until someone
        # deliberately flips it — no silent change to a live deploy.
        #
        # Already mitigated today, which is why this is low-urgency: runner.up()
        # takes a Postgres advisory lock (migrations/runner.py) so two replicas
        # booting together serialise rather than corrupt, and backend/railway.json
        # sets healthcheckPath + restartPolicyType=ON_FAILURE, so a failed
        # migration keeps the OLD container serving instead of going live broken.
        if os.getenv("RUN_MIGRATIONS_ON_BOOT", "true").lower() not in ("0", "false", "no"):
            from migrations import runner

            await runner.up(str(DB_PATH))
        else:
            logger.info(
                "db_migrations_skipped_on_boot",
                extra={"event": "db_migrations_skipped_on_boot", "reason": "RUN_MIGRATIONS_ON_BOOT=false"},
            )
    return _db


async def get_db() -> JobDatabase:
    """The boot singleton — schema owner. Used at boot + by tests for data setup.
    Routes should depend on ``get_request_db`` instead (per-request connection)."""
    if _db is None:
        await init_db()
    # cast: init_db() always assigns the module-global; mypy cannot narrow it.
    return cast(JobDatabase, _db)


async def get_request_db() -> AsyncIterator[JobDatabase]:
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


async def close_db() -> None:
    global _db
    if _db is not None:
        await _db.close()
        _db = None


def save_upload_to_temp(content: bytes, suffix: str) -> str:
    fd, path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "wb") as f:
        f.write(content)
    return path
