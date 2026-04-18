"""Shared dependencies for FastAPI routes."""
import tempfile
import os

from src.repositories.database import JobDatabase
from src.core.settings import DB_PATH

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
    if _db is None:
        await init_db()
    return _db


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
