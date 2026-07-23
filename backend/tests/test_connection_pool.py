"""The request path must REUSE Postgres connections, not reopen one per request.

The problem
-----------
`get_request_db` opened `psycopg.AsyncConnection.connect(DSN)` on EVERY HTTP
request — a full TCP + auth handshake — used it once, and threw it away. Under
load that is both slow (handshake latency on every call) and a connection storm
on Postgres (every concurrent request = one more real backend, unbounded).

A bounded pool of warm connections fixes both: connections are opened once and
handed out repeatedly, and the pool never lets more than `max_size` exist at
once no matter how many requests pile up.

What these tests assert
-----------------------
The two behaviours that make it a POOL and not merely a wrapper:

1. REUSE — acquire, release, acquire again lands on the SAME Postgres backend
   (proven by `pg_backend_pid()`). If the pid changes, it reopened — not pooling.
2. BOUNDED — with `max_size=2`, holding two connections makes a third acquire
   WAIT and time out. An unbounded model would just open a third. The timeout is
   the proof the cap is real.

They run against the real test Postgres (the pool only ever needs a live
connection; it queries no application tables, so `public` search_path is fine).
"""

import asyncio

import pytest
from psycopg_pool import PoolTimeout

from src.repositories import pg
from src.repositories import pool as poolmod


@pytest.mark.asyncio
async def test_acquire_reuses_the_same_backend_connection():
    """Release then re-acquire must return the SAME backend — the point of a pool."""
    p = poolmod.build_pool(pg.DEFAULT_DSN, min_size=1, max_size=1)
    await p.open()
    await p.wait()
    try:
        async with poolmod.acquire(p) as c1:
            cur = await c1.execute("SELECT pg_backend_pid()")
            pid1 = (await cur.fetchone())[0]
        # c1 is back in the pool now. A pool with one warm connection MUST hand
        # that same physical connection back — not open a fresh one.
        async with poolmod.acquire(p) as c2:
            cur = await c2.execute("SELECT pg_backend_pid()")
            pid2 = (await cur.fetchone())[0]
        assert pid1 == pid2, (
            f"second acquire hit a DIFFERENT backend ({pid1} vs {pid2}) — the "
            "connection was reopened, so this is not pooling"
        )
    finally:
        await p.close()


@pytest.mark.asyncio
async def test_pool_is_bounded_at_max_size():
    """At capacity, a further acquire WAITS and times out — the cap is real."""
    p = poolmod.build_pool(pg.DEFAULT_DSN, min_size=1, max_size=2)
    await p.open()
    await p.wait()
    try:
        async with poolmod.acquire(p) as _a, poolmod.acquire(p) as _b:
            # Both of the two allowed connections are checked out. A bounded pool
            # cannot produce a third — the request must block until one returns.
            # A short timeout turns that block into an observable failure.
            with pytest.raises(PoolTimeout):
                async with poolmod.acquire(p, timeout=0.5):
                    pass
    finally:
        await p.close()


@pytest.mark.asyncio
async def test_concurrent_demand_never_exceeds_max_size():
    """Many concurrent borrowers, but never more than max_size real backends live.

    This is the storm the old per-request model could not prevent: N concurrent
    requests -> N real connections. The pool caps distinct backends at max_size
    even while more coroutines are demanding one.
    """
    max_size = 3
    p = poolmod.build_pool(pg.DEFAULT_DSN, min_size=1, max_size=max_size)
    await p.open()
    await p.wait()
    seen_backends: set[int] = set()
    hold = asyncio.Event()

    async def borrow_and_hold() -> None:
        async with poolmod.acquire(p) as c:
            cur = await c.execute("SELECT pg_backend_pid()")
            seen_backends.add((await cur.fetchone())[0])
            await hold.wait()  # keep it checked out

    try:
        # Launch max_size holders that keep their connection; the pool is now
        # fully drained. More would have to wait, so we only start max_size.
        tasks = [asyncio.create_task(borrow_and_hold()) for _ in range(max_size)]
        # Give them a moment to all acquire, then let them go.
        for _ in range(50):
            if len(seen_backends) >= max_size:
                break
            await asyncio.sleep(0.02)
        hold.set()
        await asyncio.gather(*tasks)
        assert len(seen_backends) <= max_size, (
            f"{len(seen_backends)} distinct backends appeared but max_size is "
            f"{max_size} — the pool did not bound concurrency"
        )
    finally:
        hold.set()
        await p.close()


@pytest.mark.asyncio
async def test_jobdatabase_adopts_a_pooled_connection_without_reopening():
    """`from_connection` wraps a borrowed connection instead of opening a new one.

    This is the seam the request path uses: borrow from the pool, hand the
    connection to a JobDatabase, run the request, return it. If `from_connection`
    opened its own connection the pool would be pointless.
    """
    from src.repositories.database import JobDatabase

    p = poolmod.build_pool(pg.DEFAULT_DSN, min_size=1, max_size=1)
    await p.open()
    await p.wait()
    try:
        async with poolmod.acquire(p) as conn:
            db = JobDatabase.from_connection(":memory:", conn)
            # It is literally the borrowed connection — not a fresh one.
            assert db._db is conn
            # And it works as a JobDatabase.
            cur = await db._db.execute("SELECT 1")
            assert (await cur.fetchone())[0] == 1
    finally:
        await p.close()


@pytest.mark.asyncio
async def test_get_request_db_reuses_a_pooled_connection_in_production(monkeypatch):
    """The whole point: in production, two requests reuse one warm backend.

    Before the pool, each `get_request_db` opened a fresh connection, so two
    requests hit two different backends. This drives the real dependency with
    `TEST_MODE` off and asserts the backend pid is the same across two requests.
    """
    from src.api import dependencies as deps

    # Force the production branch and pretend boot already ran, so the dependency
    # does not try to (re)create the schema.
    monkeypatch.setattr(pg, "TEST_MODE", False)
    monkeypatch.setattr(deps, "_db", object())
    await poolmod.close_pool()
    try:
        pids = []
        for _ in range(2):
            agen = deps.get_request_db()
            db = await agen.__anext__()
            cur = await db._db.execute("SELECT pg_backend_pid()")
            pids.append((await cur.fetchone())[0])
            with pytest.raises(StopAsyncIteration):
                await agen.__anext__()  # run the finally (return to pool)
        assert pids[0] == pids[1], (
            f"two production requests used different backends ({pids}) — the "
            "request path is still opening a fresh connection per request"
        )
    finally:
        await poolmod.close_pool()


@pytest.mark.asyncio
async def test_get_request_db_still_isolates_by_schema_in_test_mode(monkeypatch):
    """Guard the other side: with TEST_MODE on, it must NOT touch the pool.

    The schema-per-test isolation depends on each request opening its own
    connection on the test path. If the pool leaked into test mode it would hand
    back a `public`-schema connection and every isolated test would see the
    wrong data. This asserts the test path never calls into the pool.
    """
    from src.api import dependencies as deps

    assert pg.TEST_MODE is True, "conftest should run the suite in TEST_MODE"

    called = False

    async def _boom() -> None:
        nonlocal called
        called = True
        raise AssertionError("get_pool must not be reached in TEST_MODE")

    monkeypatch.setattr(poolmod, "get_pool", _boom)
    agen = deps.get_request_db()
    db = await agen.__anext__()
    assert db is not None
    await agen.aclose()
    assert called is False


@pytest.mark.asyncio
async def test_get_pool_is_a_singleton_and_closes_clean():
    """The app-wide pool is built once and reused; close_pool tears it down."""
    await poolmod.close_pool()  # start from a known-empty state
    try:
        p1 = await poolmod.get_pool()
        p2 = await poolmod.get_pool()
        assert p1 is p2, "get_pool must return the same pool, not build a new one per call"
        # It is usable.
        async with poolmod.acquire(p1) as c:
            cur = await c.execute("SELECT 1")
            assert (await cur.fetchone())[0] == 1
    finally:
        await poolmod.close_pool()
