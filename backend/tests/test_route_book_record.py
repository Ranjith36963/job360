"""Routes PR C1: the route book books what each visit of Job360's browser met, closed values and the host only.

Every value is checked against its closed set, a user's rows older than 30 days are pruned on the next write,
the table is exported and erased with the account, and the browser gate books an ``open``'s outcome (a 403 is
``bot_block``) but never a refusal of the address guard. No host ever reaches a log line."""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.repositories.database import JobDatabase
from src.services.browser_agent import agent, route_book
from src.services.browser_agent.outcome import transport_of

JOB = "https://jobs.acme.com/apply/7?ref=private-token"
NOW = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)


async def _user(db: JobDatabase) -> str:
    uid = str(uuid.uuid4())
    await db._conn.execute(
        "INSERT INTO users (id, email, password_hash) VALUES (?, ?, ?)", (uid, f"{uid}@example.com", "h"),
    )
    return uid


async def _rows(db: JobDatabase, uid: str) -> list[dict]:
    cur = await db._db.execute(
        "SELECT site_kind, site_key, tenant_host, task, browser, outcome, probe, occurred_at "
        "FROM browser_route_attempts WHERE user_id = ? ORDER BY id", (uid,),
    )
    return [dict(r) for r in await cur.fetchall()]


@pytest.fixture
async def book(migrated_db_path):
    db = JobDatabase(migrated_db_path)
    await db.init_db()
    try:
        yield db, await _user(db)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_row_holds_only_closed_values_and_the_host(book):
    db, uid = book
    assert await route_book.record(db, uid, JOB, "fill", "job360", "bot_block", now=NOW) is True
    (row,) = await _rows(db, uid)
    assert row == {
        "site_kind": "company", "site_key": "host:jobs.acme.com", "tenant_host": "jobs.acme.com", "task": "fill",
        "browser": "job360", "outcome": "bot_block", "probe": False, "occurred_at": NOW,
    }
    assert "private-token" not in repr(row) and "/apply" not in repr(row)


@pytest.mark.asyncio
@pytest.mark.parametrize(("task", "browser", "outcome"), [
    ("submit", "job360", "ok"),
    ("read", "chrome", "ok"),
    ("read", "job360", "blocked"),
    ("read", "job360", ""),
])
async def test_a_closed_set_violation_raises_and_writes_nothing(book, task, browser, outcome):
    db, uid = book
    with pytest.raises(route_book.RouteBookValueError):
        await route_book.record(db, uid, JOB, task, browser, outcome, now=NOW)
    assert await _rows(db, uid) == []


@pytest.mark.asyncio
async def test_a_link_with_no_host_books_nothing(book):
    db, uid = book
    assert await route_book.record(db, uid, "not a link", "read", "job360", "ok", now=NOW) is False
    assert await _rows(db, uid) == []


@pytest.mark.asyncio
async def test_rows_past_30_days_are_pruned_on_the_next_write(book):
    db, uid = book
    await route_book.record(db, uid, JOB, "read", "job360", "ok", now=NOW - timedelta(days=31))
    await route_book.record(db, uid, JOB, "read", "job360", "login_wall", now=NOW - timedelta(days=29))
    await route_book.record(db, uid, JOB, "read", "job360", "captcha", now=NOW)
    assert [r["outcome"] for r in await _rows(db, uid)] == ["login_wall", "captcha"]


@pytest.mark.asyncio
async def test_pruning_touches_only_the_writing_user(book):
    db, uid = book
    other = await _user(db)
    await route_book.record(db, other, JOB, "read", "job360", "ok", now=NOW - timedelta(days=31))
    await route_book.record(db, uid, JOB, "read", "job360", "ok", now=NOW)
    assert len(await _rows(db, other)) == 1


@pytest.mark.asyncio
async def test_a_failed_write_logs_no_host_and_returns(book, caplog):
    db, uid = book
    await db._db.execute("ALTER TABLE browser_route_attempts RENAME TO browser_route_attempts_gone")
    with caplog.at_level(logging.DEBUG):
        assert await route_book.record(db, uid, JOB, "read", "job360", "bot_block", now=NOW) is False
    assert "acme" not in caplog.text and all("acme" not in repr(r.__dict__) for r in caplog.records)
    assert any(getattr(r, "event", "") == "route_attempt_failed" for r in caplog.records)


@pytest.mark.asyncio
async def test_export_includes_the_rows_and_erasure_removes_them(book):
    db, uid = book
    await route_book.record(db, uid, JOB, "read", "job360", "bot_block", now=NOW)
    out = await db.export_user_data(uid)
    assert [r["outcome"] for r in out["browser_route_attempts"]] == ["bot_block"]
    await db.hard_delete_user(uid)
    assert await _rows(db, uid) == []


# ── The gate in api/routes/browser.py ────────────────────────────────────────────────────────────────────


async def _gate_rows(user_id: str) -> list[dict]:
    import contextlib

    from src.api.dependencies import get_request_db

    async with contextlib.asynccontextmanager(get_request_db)() as db:
        return await _rows(db, user_id)


@pytest.fixture
def browser_on(monkeypatch):
    from src.core import settings

    monkeypatch.setattr(settings, "BROWSER_AGENT_ENABLED", True)
    monkeypatch.setattr(route_book, "_seen", route_book.OrderedDict())
    return monkeypatch


@pytest.mark.asyncio
async def test_the_gate_books_bot_block_on_a_403_open(authenticated_async_context, browser_on, caplog):
    async def fake_run(p, user_id, action, **kw):
        return {"status": "ok", "url": JOB, "transport": transport_of(403, {}, JOB, JOB)}

    browser_on.setattr(agent, "run", fake_run)
    with caplog.at_level(logging.DEBUG):
        async with authenticated_async_context() as client:
            out = (await client.post("/api/browser", json={"action": "open", "url": JOB})).json()
    assert out["transport"]["status"] == 403
    (row,) = await _gate_rows(authenticated_async_context.fixture_user_id)
    assert (row["outcome"], row["task"], row["browser"]) == ("bot_block", "read", "job360")
    assert "jobs.acme.com" not in caplog.text


@pytest.mark.asyncio
async def test_the_gate_books_ok_once_per_session_and_site(authenticated_async_context, browser_on):
    async def fake_run(p, user_id, action, **kw):
        return {"status": "ok", "url": JOB, "transport": transport_of(200, {}, JOB, JOB)}

    browser_on.setattr(agent, "run", fake_run)
    async with authenticated_async_context() as client:
        for _ in range(3):
            await client.post("/api/browser", json={"action": "open", "url": JOB, "session": "s1"})
    assert [r["outcome"] for r in await _gate_rows(authenticated_async_context.fixture_user_id)] == ["ok"]


@pytest.mark.asyncio
async def test_the_gate_books_confirmed_once(authenticated_async_context, browser_on):
    async def fake_run(p, user_id, action, **kw):
        return {"status": "ok", "url": JOB, "confirmation_seen": True}

    browser_on.setattr(agent, "run", fake_run)
    async with authenticated_async_context() as client:
        await client.post("/api/browser", json={"action": "look"})
        await client.post("/api/browser", json={"action": "look"})
    assert [r["outcome"] for r in await _gate_rows(authenticated_async_context.fixture_user_id)] == ["confirmed"]


@pytest.mark.asyncio
async def test_an_address_guard_refusal_books_nothing(authenticated_async_context, browser_on):
    async def fake_run(p, user_id, action, **kw):  # the real open, refused by the address screen
        class Screen:
            async def screen(self, url):
                return SimpleNamespace(ok=False, reason="private address")

        return await agent._act(SimpleNamespace(screen=Screen()), SimpleNamespace(), action, url=kw["url"],
                                ref="", text="", on=True, doc_kind="", gate=None, files=None)

    browser_on.setattr(agent, "run", fake_run)
    async with authenticated_async_context() as client:
        out = (await client.post("/api/browser", json={"action": "open", "url": "http://10.0.0.5/x"})).json()
    assert out["reason"] == "blocked_address"
    assert await _gate_rows(authenticated_async_context.fixture_user_id) == []


@pytest.mark.asyncio
async def test_a_booking_failure_never_breaks_the_action(authenticated_async_context, browser_on):
    async def fake_run(p, user_id, action, **kw):
        return {"status": "ok", "url": JOB, "transport": transport_of(200, {}, JOB, JOB)}

    async def broken(*a, **k):
        raise RuntimeError("db down")

    browser_on.setattr(agent, "run", fake_run)
    browser_on.setattr(route_book, "record", broken)
    async with authenticated_async_context() as client:
        resp = await client.post("/api/browser", json={"action": "open", "url": JOB})
    assert resp.status_code == 200 and resp.json()["status"] == "ok"
