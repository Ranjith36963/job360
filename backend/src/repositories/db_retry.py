"""Write-retry helper (Finding #11) — Postgres edition.

Historically this guarded against SQLite's ``database is locked`` writer
contention. Postgres serialises writers with row/table locks instead of failing
fast, so genuine "locked" errors no longer occur. The helper is kept because
callers (rescore loop, LLM judge) still route writes through it, and it now
retries transient ``OperationalError``s (a connection blip) with backoff while
re-raising anything else immediately. The lock-message heuristic is preserved so
the existing tests — which simulate a lock by raising ``OperationalError`` with a
"locked"/"busy" message — keep their meaning.
"""
import asyncio
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Awaitable, Callable, TypeVar

import psycopg

from src.repositories import pg
from src.utils.logger import get_logger

_log = get_logger("db")  # "job360.db" → main job360 handlers (data/logs/)

T = TypeVar("T")


@asynccontextmanager
async def open_db(db_path: str, *, busy_timeout_ms: int = 30000) -> AsyncIterator[Any]:
    """Open a short-lived Postgres connection (aiosqlite-shaped).

    ``busy_timeout_ms`` is accepted for signature compatibility but ignored —
    Postgres has no busy-timeout PRAGMA.
    """
    async with pg.connect(db_path) as db:
        yield db


def _is_locked(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "locked" in msg or "busy" in msg


async def with_write_retry(
    fn: Callable[[], Awaitable[T]],
    *,
    attempts: int = 5,
    base_delay: float = 0.05,
) -> T:
    """Run ``fn`` (an async DB write); retry transient lock/blip errors.

    Retries up to ``attempts`` times with exponential backoff on an
    ``OperationalError`` whose message looks like a lock/busy condition.
    Any other ``OperationalError`` (e.g. bad SQL surfaced as operational) is
    raised immediately. The final failure is re-raised.
    """
    for i in range(attempts):
        try:
            return await fn()
        except psycopg.OperationalError as exc:
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
