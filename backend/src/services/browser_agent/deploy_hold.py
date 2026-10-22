"""Hold a deploy while a cloud-browser form is open (owner decision, 2026-10-22).

A deploy restarts the backend and wipes the in-process Playwright pool, losing a
half-filled form. Railway sends traffic to a new container only once its healthcheck
answers 200, and the old one keeps serving until then. So the NEW container answers 503
on ``GET /api/deployz`` while the OLD process still has a recently active browser,
for at most ``DEPLOY_HOLD_MAX_SECONDS`` after the new process booted.

The signal between the two processes is a Postgres session-level SHARED advisory lock
(two-key form, ``HOLD_LOCK_CLASS`` / ``HOLD_LOCK_KEY``; 360_007 is proof.py, 360_026 is
edits.py). The old process holds it on a dedicated connection while its pool is busy;
the new one asks for the exclusive lock with ``pg_try_advisory_lock``: refused means
some process is busy. A crash drops the connection, which releases the lock. No
migration. Every failure here answers "not busy": a deploy must never be held by a
broken signal.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

from src.repositories import pg

logger = logging.getLogger(__name__)

HOLD_LOCK_CLASS = 360_027  # int32 class id; the other advisory-lock users are listed above
HOLD_LOCK_KEY = 0
# Opening a connection or taking the lock must not stall a browser open or a healthcheck.
_DB_TIMEOUT_S = 5.0

_BOOT_AT = time.monotonic()


class Beacon:
    """Holds the shared lock on one dedicated connection while this process is busy."""

    def __init__(self) -> None:
        self._conn: Optional[Any] = None
        # sync() is called by the reap loop and by session creation: one at a time.
        self._lock = asyncio.Lock()

    @property
    def held(self) -> bool:
        return self._conn is not None

    async def sync(self, busy: bool) -> None:
        """Take the lock when busy, release it (by closing the connection) when not. Never raises."""
        try:
            async with self._lock:
                if busy and self._conn is None:
                    await asyncio.wait_for(self._open(), _DB_TIMEOUT_S)
                elif not busy and self._conn is not None:
                    await self._drop()
        except Exception as exc:  # noqa: BLE001 - the beacon must never break the browser
            logger.warning("deploy_hold beacon sync failed: %s", type(exc).__name__)

    async def _open(self) -> None:
        conn = await pg.connect()
        try:
            await conn.execute(
                "SELECT pg_advisory_lock_shared(?, ?)", (HOLD_LOCK_CLASS, HOLD_LOCK_KEY)
            )
        except BaseException:
            await _close_quietly(conn)
            raise
        self._conn = conn

    async def _drop(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            await _close_quietly(conn)

    async def close(self) -> None:
        """Release the lock (shutdown). Never raises."""
        try:
            async with self._lock:
                await self._drop()
        except Exception as exc:  # noqa: BLE001
            logger.warning("deploy_hold beacon close failed: %s", type(exc).__name__)


async def _close_quietly(conn: Any) -> None:
    try:
        await asyncio.wait_for(conn.close(), _DB_TIMEOUT_S)
    except Exception:  # noqa: BLE001 - a dead connection has dropped its lock already
        logger.debug("deploy_hold connection close failed", exc_info=True)


async def others_busy() -> bool:
    """True when some process holds the shared lock. A DB error answers False (deploy)."""
    try:
        async with pg.connect() as conn:
            cur = await asyncio.wait_for(
                conn.execute("SELECT pg_try_advisory_lock(?, ?)", (HOLD_LOCK_CLASS, HOLD_LOCK_KEY)),
                _DB_TIMEOUT_S,
            )
            row = await cur.fetchone()
            if bool(row[0]):
                await conn.execute("SELECT pg_advisory_unlock(?, ?)", (HOLD_LOCK_CLASS, HOLD_LOCK_KEY))
                return False
            return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("deploy_hold others_busy failed: %s", type(exc).__name__)
        return False


def seconds_since_boot() -> int:
    return int(time.monotonic() - _BOOT_AT)


async def hold_reason() -> Optional[str]:
    """``"browser_busy"`` while the hold window is open and another process is busy, else None."""
    from src.core import settings  # noqa: PLC0415 - live read, house style

    max_s = settings.DEPLOY_HOLD_MAX_SECONDS
    if max_s <= 0 or time.monotonic() - _BOOT_AT >= max_s:
        return None
    return "browser_busy" if await others_busy() else None
