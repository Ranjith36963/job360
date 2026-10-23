"""The hand-off view (docs/product/BROWSER_AGENT.md): when Job360's browser stops on a human check, the
USER finishes it in the assistant's own browser, on the filled form, from the website only.

* The filled form is kept open for the user (HANDOFF_HOLD_SECONDS), past the idle sweep; Done lets it go.
* The view acts on the assistant's browser (the form the assistant filled), never opens a page and never
  keeps a sign-in; with no browser open it says the form has closed.
* A CAPTCHA stop on a named application holds the browser and tells the user to finish it here.
* Like the sign-in view, a token (an assistant) can never drive it.
"""
from __future__ import annotations

import socket
import time

import pytest

from tests.test_browser_agent import _ref, _site_resolver, _to_site, needs_chromium
from tests.test_browser_hardening_live import two_page_site  # noqa: F401 - the fixture


class _Ctx:
    closed = False

    async def close(self):
        self.closed = True


async def _no_dns(host, port=0, family=socket.AF_UNSPEC):
    return []


@pytest.mark.asyncio
async def test_a_held_form_outlives_the_idle_sweep_until_the_hold_ends():
    from src.services.browser_agent.agent import BrowserPool, Session, hold_for_user

    pool = BrowserPool(max_sessions=1, idle_s=600, resolve=_no_dns)
    ctx = _Ctx()
    s = Session(context=ctx, page=None, last_used=time.monotonic() - 3600)
    pool._sessions = {"u1": s}  # noqa: SLF001
    assert hold_for_user(pool, "u1") is True
    await pool.reap()
    assert "u1" in pool._sessions and not ctx.closed  # noqa: SLF001 - idle, but held for the user
    s.held_until = time.monotonic() - 1  # the hold ran out
    await pool.reap()
    assert ctx.closed
    assert hold_for_user(pool, "u1") is False  # nothing open: nothing to hold
    assert hold_for_user(None, "u1") is False


@pytest.mark.asyncio
async def test_the_finish_view_needs_an_open_form_and_has_no_start_or_keep():
    from src.services.browser_agent.agent import BrowserError, BrowserPool, Session, hold_for_user, live

    pool = BrowserPool(max_sessions=1, idle_s=600, resolve=_no_dns)
    with pytest.raises(BrowserError) as closed:
        await live(pool, "u1", "shot", finish=True)
    assert closed.value.reason == "no_form"
    for action in ("start", "keep", "cancel"):
        with pytest.raises(BrowserError) as bad:
            await live(pool, "u1", action, url="https://jobs.acme.com/", finish=True)
        assert bad.value.reason == "bad_action"

    s = Session(context=_Ctx(), page=None)
    pool._sessions = {"u1": s}  # noqa: SLF001
    hold_for_user(pool, "u1")
    assert (await live(pool, "u1", "done", finish=True)) == {"status": "ok", "done": True}
    assert s.held_until is None  # back on the idle clock
    assert "u1" in pool._sessions  # noqa: SLF001 - Done never closes the assistant's browser


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_the_user_finishes_on_the_assistants_filled_form(two_page_site):  # noqa: F811
    from src.services.browser_agent.agent import BrowserPool, live, run

    pool = BrowserPool(max_sessions=1, idle_s=600, resolve=_site_resolver(), open_connection=_to_site(two_page_site))
    try:
        page = await run(pool, "u1", "open", url="http://jobs.example/job")
        await run(pool, "u1", "type", ref=_ref(page, "First name"), text="Gra")  # the field keeps focus
        shot = await live(pool, "u1", "shot", finish=True)
        assert shot["title"] == "Job - Acme" and shot["image"].startswith("data:image/jpeg;base64,")
        await live(pool, "u1", "type", text="ce", finish=True)
        seen = await run(pool, "u1", "look")
        assert next(e for e in seen["elements"] if e["name"].startswith("First name"))["value"] == "Grace"
        assert len(pool._sessions) == 1  # noqa: SLF001 - the same browser, not a second one
    finally:
        await pool.shutdown()


@pytest.mark.asyncio
async def test_a_captcha_stop_holds_the_form_and_says_finish_it_here(authenticated_async_context, monkeypatch):
    import contextlib
    import json

    from src.api.dependencies import get_request_db
    from src.core import settings
    from src.services.browser_agent import agent

    monkeypatch.setattr(settings, "BROWSER_AGENT_ENABLED", True)
    pool = agent.BrowserPool(max_sessions=1, idle_s=600, resolve=_no_dns)

    async def fake_run(p, user_id, action, **kw):
        pool._sessions[user_id] = agent.Session(context=_Ctx(), page=None)  # noqa: SLF001 - the open form
        return {"status": "needs_you", "reason": "captcha", "detail": "x", "url": "https://jobs.acme.com/apply/7"}

    monkeypatch.setattr(agent, "run", fake_run)
    monkeypatch.setattr(agent, "get_pool", lambda: pool)

    async with authenticated_async_context() as client:
        brought = await client.post("/api/jobs/bring", json={
            "title": "Engineer", "company": "Acme", "location": "London",
            "description": "Build services. " * 10, "apply_url": "https://jobs.acme.com/apply/7",
        })
        app_id = brought.json()["application_id"]
        out = (await client.post("/api/browser", json={"action": "click", "ref": "e1_ab", "application_id": app_id})).json()
        assert out["blocked_recorded"] is True
        user_id = authenticated_async_context.fixture_user_id
        assert pool._sessions[user_id].held_until is not None  # noqa: SLF001
        async with contextlib.asynccontextmanager(get_request_db)() as db:
            cur = await db._db.execute(
                "SELECT payload FROM application_events WHERE user_id = ? AND application_id = ? "
                "AND event_type = 'blocked'", (user_id, app_id),
            )
            (row,) = [json.loads(r["payload"]) for r in await cur.fetchall()]
        assert row["reason"] == "captcha" and "Finish it here" in row["detail"]

        done = (await client.post("/api/browser/live", json={"action": "done", "mode": "finish"})).json()
        assert done == {"status": "ok", "done": True}
        bad = await client.post("/api/browser/live", json={"action": "shot", "mode": "drive"})
        assert bad.status_code == 422


@pytest.mark.asyncio
async def test_a_token_can_never_drive_the_finish_view(authenticated_async_context):
    async with authenticated_async_context() as client:
        token = (await client.post("/api/tokens", json={"name": "agent"})).json()["token"]
    from httpx import ASGITransport, AsyncClient

    from src.api.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    ) as agent:
        resp = await agent.post("/api/browser/live", json={"action": "click", "x": 10, "y": 10, "mode": "finish"})
        assert resp.status_code == 403
