"""Deploy hold: a new container answers 503 on /api/deployz while the old one has a busy browser.

Real Postgres. Advisory locks are database-wide, not per schema, so every test uses its own
random lock key.
"""
import asyncio
import random
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.main import app
from src.core import settings
from src.services.browser_agent import agent, deploy_hold
from src.services.browser_agent.agent import BrowserPool, Session


@pytest.fixture(autouse=True)
def _own_key(monkeypatch):
    monkeypatch.setattr(deploy_hold, "HOLD_LOCK_KEY", random.randint(1, 2_000_000_000))
    # These tests read the lock state straight after changing it: no probe cache here.
    monkeypatch.setattr(deploy_hold, "_PROBE_TTL_S", 0.0)
    monkeypatch.setattr(deploy_hold, "_probe_cache", None)


async def _deployz():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.get("/api/deployz")


@pytest.mark.asyncio
async def test_beacon_holds_the_lock_until_not_busy():
    b = deploy_hold.Beacon()
    try:
        assert await deploy_hold.others_busy() is False
        await b.sync(True)
        assert b.held
        assert await deploy_hold.others_busy() is True
        await b.sync(False)
        assert not b.held
        assert await deploy_hold.others_busy() is False
    finally:
        await b.close()


@pytest.mark.asyncio
async def test_a_dropped_connection_releases_the_lock():
    b = deploy_hold.Beacon()
    await b.sync(True)
    assert await deploy_hold.others_busy() is True
    await b._conn.close()  # a crash: the connection goes, no unlock is sent
    assert await deploy_hold.others_busy() is False
    b._conn = None


@pytest.mark.asyncio
async def test_a_killed_lock_connection_is_noticed_and_the_lock_retaken():
    from src.repositories import pg

    b = deploy_hold.Beacon()
    try:
        await b.sync(True)
        cur = await b._conn.execute("SELECT pg_backend_pid()")
        pid = (await cur.fetchone())[0]
        async with pg.connect() as other:  # Postgres restarts / kills the idle connection
            await other.execute("SELECT pg_terminate_backend(?)", (pid,))
        assert await deploy_hold.others_busy() is False   # the lock went with the connection
        assert b.held                                     # ...but the beacon still thinks it holds it
        await b.sync(True)                                # the next tick notices and retakes it
        assert await deploy_hold.others_busy() is True
    finally:
        await b.close()


@pytest.mark.asyncio
async def test_sync_never_raises_on_a_db_error():
    b = deploy_hold.Beacon()
    with patch("src.repositories.pg.connect", side_effect=RuntimeError("down")):
        await b.sync(True)
    assert not b.held


@pytest.mark.asyncio
async def test_a_flood_of_probes_costs_one_db_check(monkeypatch):
    import asyncio

    calls = []

    async def _probe():
        calls.append(1)
        return True

    monkeypatch.setattr(deploy_hold, "_PROBE_TTL_S", 2.0)
    monkeypatch.setattr(deploy_hold, "_probe", _probe)
    results = await asyncio.gather(*(deploy_hold.others_busy() for _ in range(50)))
    assert all(results) and len(calls) == 1


@pytest.mark.asyncio
async def test_others_busy_fails_toward_deploying():
    with patch("src.repositories.pg.connect", side_effect=RuntimeError("down")):
        assert await deploy_hold.others_busy() is False


@pytest.mark.asyncio
async def test_deployz_holds_while_busy_within_the_window(monkeypatch):
    monkeypatch.setattr(settings, "DEPLOY_HOLD_MAX_SECONDS", 600)
    b = deploy_hold.Beacon()
    try:
        await b.sync(True)
        resp = await _deployz()
        assert resp.status_code == 503
        body = resp.json()
        assert body["status"] == "holding" and body["reason"] == "browser_busy"
        assert isinstance(body["held_s"], int)
        assert set(body) == {"status", "reason", "held_s"}
        await b.sync(False)
        resp = await _deployz()
        assert resp.status_code == 200 and resp.json() == {"status": "ready"}
    finally:
        await b.close()


@pytest.mark.asyncio
async def test_deployz_releases_after_the_window_even_while_busy(monkeypatch):
    monkeypatch.setattr(settings, "DEPLOY_HOLD_MAX_SECONDS", 600)
    monkeypatch.setattr(deploy_hold, "_BOOT_AT", deploy_hold._BOOT_AT - 601)
    b = deploy_hold.Beacon()
    try:
        await b.sync(True)
        resp = await _deployz()
        assert resp.status_code == 200 and resp.json() == {"status": "ready"}
    finally:
        await b.close()


@pytest.mark.asyncio
async def test_deployz_max_zero_is_the_kill_switch(monkeypatch):
    monkeypatch.setattr(settings, "DEPLOY_HOLD_MAX_SECONDS", 0)
    b = deploy_hold.Beacon()
    try:
        await b.sync(True)
        resp = await _deployz()
        assert resp.status_code == 200
    finally:
        await b.close()


@pytest.mark.asyncio
async def test_deployz_db_failure_wins_over_the_hold(monkeypatch):
    monkeypatch.setattr(settings, "DEPLOY_HOLD_MAX_SECONDS", 600)

    async def _busy() -> bool:
        return True

    monkeypatch.setattr(deploy_hold, "others_busy", _busy)
    with patch("src.repositories.pg.connect", side_effect=RuntimeError("connection refused")):
        resp = await _deployz()
    assert resp.status_code == 503
    assert resp.json() == {"status": "not_ready", "reason": "db_error"}


def _pool_with(*sessions: Session) -> BrowserPool:
    pool = BrowserPool(max_sessions=2, idle_s=600, resolve=lambda *a, **k: None)  # type: ignore[arg-type]
    for i, s in enumerate(sessions):
        pool._sessions[f"u{i}"] = s
    return pool


@pytest.mark.asyncio
async def test_busy_count_respects_quiet_window_and_held_lock():
    old = Session(context=None, page=None, last_used=100.0)
    recent = Session(context=None, page=None, last_used=990.0)
    pool = _pool_with(old, recent)
    assert pool.busy_count(300, now=1000.0) == 1  # only the recent one
    assert pool.busy_count(5, now=1000.0) == 0
    assert pool.busy_count(2000, now=1000.0) == 2
    # An action in flight counts however long ago the session was last used.
    await old.lock.acquire()
    assert pool.busy_count(5, now=1000.0) == 1


@pytest.mark.asyncio
async def test_reap_forever_syncs_the_beacon(monkeypatch):
    calls: list[bool] = []

    class _Fake:
        async def sync(self, busy: bool) -> None:
            calls.append(busy)

    pool = _pool_with(Session(context=None, page=None))  # just used: busy
    monkeypatch.setattr(agent, "_pool", pool)
    monkeypatch.setattr(agent, "beacon", _Fake())

    # conftest mocks asyncio.sleep to instant; let one iteration run, then cancel.
    task = asyncio.create_task(agent.reap_forever(0))
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(_cancel_after_first(task, calls), 5)
    assert calls and calls[0] is True


async def _cancel_after_first(task: "asyncio.Task[None]", calls: list[bool]) -> None:
    while not calls:
        await asyncio.sleep(0)
    task.cancel()
    await task
