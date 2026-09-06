"""Shared dependencies for FastAPI routes."""
import os
import tempfile
from typing import AsyncIterator, cast

from src.core.settings import DB_PATH
from src.repositories import pg, pool
from src.repositories.database import JobDatabase
from src.utils.logger import get_logger

logger = get_logger("api.dependencies")

_db: JobDatabase | None = None


async def init_db() -> JobDatabase:
    global _db
    if _db is None:
        _db = JobDatabase(str(DB_PATH))
        await _db.init_db()
        # Batch 2: apply additive migrations (users, sessions, and the tables
        # that have followed since). Idempotent — safe to call on every boot.
        # See docs/product/plans/batch-2-plan.md Phase 0.
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

    Each request gets its OWN connection instead of one process-wide shared one.
    psycopg3 forbids using a single async connection from two coroutines at once,
    so a shared connection could interleave/corrupt concurrent requests ("another
    operation is already in progress") and never recovered after a DB restart. A
    per-request connection is concurrency-safe and self-healing. Schema +
    migrations still run ONCE at boot via ``init_db()``; this opens no DDL.

    Production borrows that connection from a bounded pool instead of opening a
    fresh one every request. The correctness guarantee is identical (each request
    still gets its own connection for the duration of the request — the pool never
    hands the same connection to two requests at once), but the TCP + auth
    handshake is paid once at startup instead of on every call, and the number of
    real Postgres backends is capped at the pool's ``max_size``.

    Test mode keeps opening a fresh connection per request. The schema-per-test
    isolation depends on it: each test's connection selects that test's schema
    from ``DB_PATH`` (``pg.schema_for_path``), whereas a pooled connection is
    fixed to ``public``. Routing test requests through the pool would hand every
    isolated test the wrong schema.
    """
    if _db is None:
        await init_db()  # ensure schema + migrations applied once (idempotent)

    if pg.TEST_MODE:
        # Per-test schema isolation — one fresh connection per request, closed
        # after. Unchanged from the original P0 fix.
        db = JobDatabase(str(DB_PATH))
        await db.connect()
        try:
            yield db
        finally:
            await db.close()
    else:
        # Production — borrow a warm connection from the pool and return it after
        # the response. ``acquire`` returns the connection to the pool on exit, so
        # we must NOT close the JobDatabase (it does not own the connection).
        request_pool = await pool.get_pool()
        async with pool.acquire(request_pool) as conn:
            yield JobDatabase.from_connection(str(DB_PATH), conn)


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
