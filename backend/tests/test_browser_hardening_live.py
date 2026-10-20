"""Review fixes for the live sign-in view (slice 2, docs/product/BROWSER_AGENT.md).

* The live view is its own browser, never the one the assistant's token drives: an assistant
  cannot read what the user types there, start does not take over the assistant's half-filled
  form, and Cancel does not close the assistant's browser.
* A close waits for the action in flight; it never closes a browser mid-action.
* The page's 1.5 s picture refresh does not keep a live view open: only the user's own steps do,
  and a live view has a hard maximum lifetime.
"""
from __future__ import annotations

import asyncio
import socket
import time

import pytest

from tests.test_browser_agent import LOGIN, _ref, _site_resolver, _to_site, needs_chromium

JOB = """<!doctype html><html><head><title>Job - Acme</title></head><body>
<label for="fn">First name</label><input id="fn">
</body></html>"""


@pytest.fixture
async def two_page_site():
    from aiohttp import web

    async def job(_):
        return web.Response(text=JOB, content_type="text/html")

    async def login(_):
        return web.Response(text=LOGIN, content_type="text/html")

    app = web.Application()
    app.router.add_get("/job", job)
    app.router.add_get("/login", login)
    runner = web.AppRunner(app)
    await runner.setup()
    tcp = web.TCPSite(runner, "127.0.0.1", 0)
    await tcp.start()
    yield tcp._server.sockets[0].getsockname()[1]  # noqa: SLF001
    await runner.cleanup()


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_the_live_view_is_its_own_browser_not_the_assistants(two_page_site):
    from src.services.browser_agent.agent import BrowserPool, live, run

    pool = BrowserPool(max_sessions=2, idle_s=600, resolve=_site_resolver(), open_connection=_to_site(two_page_site))
    try:
        page = await run(pool, "u1", "open", url="http://jobs.example/job")
        await run(pool, "u1", "type", ref=_ref(page, "First name"), text="Grace")  # the assistant's half-filled form

        shot = await live(pool, "u1", "start", url="http://jobs.example/login")
        assert shot["title"] == "Sign in - Acme"
        await live(pool, "u1", "click", x=100, y=215)
        await live(pool, "u1", "type", text="ada@example.com")

        seen = await run(pool, "u1", "look")
        assert seen["title"] == "Job - Acme"  # start did not take over the assistant's page
        assert next(e for e in seen["elements"] if e["name"].startswith("First name"))["value"] == "Grace"
        assert "ada@example.com" not in str(seen)  # what the user typed is not the assistant's to read
        assert len(pool._sessions) == 2  # noqa: SLF001 - two browsers, each counted against the cap

        assert (await live(pool, "u1", "cancel"))["closed"] is True
        after = await run(pool, "u1", "look")  # Cancel closed the live view only
        assert after["status"] == "ok" and after["title"] == "Job - Acme"
    finally:
        await pool.shutdown()


@pytest.mark.asyncio
async def test_a_close_waits_for_the_action_in_flight():
    from src.services.browser_agent.agent import BrowserPool, Session

    class _Ctx:
        closed = False

        async def close(self):
            self.closed = True

    async def resolve(host, port=0, family=socket.AF_UNSPEC):
        return []

    pool = BrowserPool(max_sessions=1, idle_s=600, resolve=resolve)
    ctx = _Ctx()
    s = Session(context=ctx, page=None)
    pool._sessions = {"u1": s}  # noqa: SLF001
    await s.lock.acquire()  # an action is half-way through
    closing = asyncio.create_task(pool.close("u1"))
    for _ in range(5):
        await asyncio.sleep(0)
    assert not ctx.closed  # not closed under the action
    s.lock.release()
    assert await closing is True and ctx.closed


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_the_picture_refresh_does_not_keep_a_live_view_open(two_page_site):
    from src.services.browser_agent.agent import BrowserPool, live

    pool = BrowserPool(max_sessions=1, idle_s=600, resolve=_site_resolver(), open_connection=_to_site(two_page_site))
    try:
        await live(pool, "u1", "start", url="http://jobs.example/login")
        (s,) = pool._sessions.values()  # noqa: SLF001
        s.last_used = before = time.monotonic() - 100
        await live(pool, "u1", "shot")
        assert s.last_used == before  # a picture is not the user
        await live(pool, "u1", "scroll", dy=10)
        assert s.last_used > before  # a step of the user's is
    finally:
        await pool.shutdown()


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_a_live_view_closes_after_its_maximum_lifetime(two_page_site, monkeypatch):
    from src.services.browser_agent import agent
    from src.services.browser_agent.agent import BrowserPool, live

    monkeypatch.setattr(agent, "LIVE_MAX_SECONDS", 0.5, raising=False)
    pool = BrowserPool(max_sessions=1, idle_s=600, resolve=_site_resolver(), open_connection=_to_site(two_page_site))
    try:
        await live(pool, "u1", "start", url="http://jobs.example/login")
        await live(pool, "u1", "scroll", dy=10)  # however busy the user is
        await asyncio.sleep(0.6)
        with pytest.raises(agent.BrowserError) as exc:
            await live(pool, "u1", "click", x=10, y=10)
        assert exc.value.reason == "no_browser"
        assert pool._sessions == {}  # noqa: SLF001
    finally:
        await pool.shutdown()
