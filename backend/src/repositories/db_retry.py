"""SQLite write-retry helper (Finding #11).

SQLite serialises writers; under concurrent writes (the search pipeline +
per-user re-score + the LLM judge all writing at once) a writer can get
``OperationalError: database is locked`` even with a generous ``busy_timeout``.

Before, the re-score loop and the judge caught that and *dropped the row*,
silently losing scored jobs / verdicts. ``with_write_retry`` retries the write
a few times with exponential backoff so a transient lock no longer loses data;
it only retries genuine lock errors (not, say, a bad-SQL OperationalError), and
re-raises if the lock never clears.
"""
import asyncio
import sqlite3
from contextlib import asynccontextmanager
from typing import Awaitable, Callable, TypeVar

import aiosqlite

from src.utils.logger import get_logger

_log = get_logger("db")  # "job360.db" → main job360 handlers (data/logs/)

T = TypeVar("T")


@asynccontextmanager
async def open_db(db_path: str, *, busy_timeout_ms: int = 30000):
    """Open an aiosqlite connection with ``busy_timeout`` already set.

    Finding #11 set ``busy_timeout=30000`` on the long-lived ``JobDatabase``
    connection, but the auth/channels request path opened raw
    ``aiosqlite.connect()`` connections with NO timeout — so under concurrent
    writes (e.g. a search pipeline running) a session/logout write hit
    ``database is locked`` and 500'd instead of waiting. This drop-in replacement
    makes every short-lived connection wait for the lock like the main one does.
    """
    async with aiosqlite.connect(db_path) as db:
        await db.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
        yield db


def _is_locked(exc: sqlite3.OperationalError) -> bool:
    msg = str(exc).lower()
    return "locked" in msg or "busy" in msg


async def with_write_retry(
    fn: Callable[[], Awaitable[T]],
    *,
    attempts: int = 5,
    base_delay: float = 0.05,
) -> T:
    """Run ``fn`` (an async DB write); retry on 'database is locked'.

    Retries up to ``attempts`` times with exponential backoff. Non-lock
    OperationalErrors (e.g. bad SQL) are raised immediately. The final lock
    failure is re-raised so callers can decide what to do.
    """
    for i in range(attempts):
        try:
            return await fn()
        except sqlite3.OperationalError as exc:
            if _is_locked(exc) and i < attempts - 1:
                _log.warning(
                    "db_write_retry",
                    extra={"event": "db_write_retry", "attempt": i + 1, "max_attempts": attempts, "error": str(exc)},
                )
                await asyncio.sleep(base_delay * (2**i))
                continue
            if _is_locked(exc):
                _log.error(
                    "db_write_failed",
                    extra={"event": "db_write_failed", "attempts": attempts, "error": str(exc)},
                )
            raise
    raise RuntimeError("unreachable")  # pragma: no cover
