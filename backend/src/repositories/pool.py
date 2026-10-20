"""Bounded async connection pool for the per-request DB path.

Why this exists
---------------
`get_request_db` used to call `psycopg.AsyncConnection.connect(DSN)` on EVERY
HTTP request: a full TCP + auth handshake, one use, then thrown away. That is
slow (handshake latency on the critical path of every call) and unbounded (N
concurrent requests open N real Postgres backends — a connection storm with no
ceiling).

This pool opens a small set of connections once and hands them out repeatedly,
and never lets more than `max_size` exist at a time. Connections are configured
to be byte-for-byte equivalent to `pg._open_raw` (autocommit + the same row
factory), so a pooled connection is indistinguishable from a freshly opened one
to every call site. One deliberate difference: each pooled connection also gets
a ``statement_timeout`` (``settings.DB_STATEMENT_TIMEOUT_MS``) when it is opened.

Production only. The schema-per-test isolation (`TEST_MODE`) opens a fresh
`JobDatabase` per test and tears its schema down afterwards; pooling there would
be both pointless and wrong. `get_request_db` therefore only reaches for the
pool when `pg.TEST_MODE` is False.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Awaitable, Callable, Optional

import psycopg
from psycopg_pool import AsyncConnectionPool

from src.core import settings
from src.repositories import pg


def _pool_kwargs() -> dict[str, Any]:
    """Connection kwargs matching `pg._open_raw` exactly.

    A pooled connection must behave identically to one opened the old way:
    autocommit on, and the same row factory so `Row`-shaped results come back.
    """
    return {"autocommit": True, "row_factory": pg._row_factory}


def _statement_timeout_configurer(
    timeout_ms: int,
) -> Callable[[psycopg.AsyncConnection[Any]], Awaitable[None]]:
    """The pool's ``configure`` callback: run once on every NEW pooled connection.

    It sets a session-level ``statement_timeout`` so one runaway query cannot
    hold a pooled connection (and so a request slot) forever. ``set_config``
    instead of ``SET`` because ``SET`` takes no bind parameter. The connection is
    autocommit, so the setting sticks for the life of the connection and the
    pool's rollback-on-return does not undo it. Only pooled connections get it:
    migrations and boot DDL open their own via ``pg.connect``.
    """

    async def _configure(conn: psycopg.AsyncConnection[Any]) -> None:
        await conn.execute(
            "SELECT set_config('statement_timeout', %s, false)", (f"{timeout_ms}ms",)
        )

    return _configure


def build_pool(
    dsn: str,
    *,
    min_size: int,
    max_size: int,
    statement_timeout_ms: Optional[int] = None,
) -> AsyncConnectionPool[Any]:
    """Construct (but do not open) a pool. Caller does `await pool.open()`.

    psycopg_pool 3.2+ requires `open=False` at construction and an explicit
    `open()` — opening inside `__init__` is deprecated because it hides an
    `await` behind a synchronous call. ``statement_timeout_ms`` defaults to
    ``settings.DB_STATEMENT_TIMEOUT_MS``.
    """
    if statement_timeout_ms is None:
        statement_timeout_ms = settings.DB_STATEMENT_TIMEOUT_MS
    return AsyncConnectionPool(
        dsn,
        min_size=min_size,
        max_size=max_size,
        kwargs=_pool_kwargs(),
        configure=_statement_timeout_configurer(statement_timeout_ms),
        open=False,
    )


def stats(pool: AsyncConnectionPool[Any]) -> dict[str, int]:
    """Occupancy numbers for /api/readyz (no DSN, no counters).

    ``size`` is how many connections exist now, ``idle`` how many sit in the
    pool, ``used`` how many requests hold one, ``waiting`` how many wait for one.
    """
    raw = pool.get_stats()
    size = raw.get("pool_size", 0)
    idle = raw.get("pool_available", 0)
    return {
        "size": size,
        "idle": idle,
        "used": size - idle,
        "waiting": raw.get("requests_waiting", 0),
        "max": raw.get("pool_max", 0),
    }


@asynccontextmanager
async def acquire(
    pool: AsyncConnectionPool[Any], *, timeout: Optional[float] = None
) -> AsyncIterator[pg.Connection]:
    """Borrow a connection, wrapped in the aiosqlite-shaped `pg.Connection`.

    `pool.connection()` returns the raw connection to the pool on exit (with a
    rollback reset). We wrap it so callers get the same API as `pg.connect()`,
    and we deliberately do NOT close it — the pool owns its lifecycle.
    """
    kw: dict[str, Any] = {} if timeout is None else {"timeout": timeout}
    async with pool.connection(**kw) as raw:
        yield pg.Connection(raw)


# --- App-wide singleton ---------------------------------------------------
_pool: Optional[AsyncConnectionPool[Any]] = None


def _env_size(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


async def get_pool() -> AsyncConnectionPool[Any]:
    """The process-wide pool on `pg.DEFAULT_DSN`, built and opened once."""
    global _pool
    if _pool is None:
        _pool = build_pool(
            pg.DEFAULT_DSN,
            min_size=_env_size("DB_POOL_MIN_SIZE", 1),
            max_size=_env_size("DB_POOL_MAX_SIZE", 10),
        )
        await _pool.open()
        await _pool.wait()
    return _pool


async def close_pool() -> None:
    """Tear the pool down (app shutdown / test cleanup)."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
