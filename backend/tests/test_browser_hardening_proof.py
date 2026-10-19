"""Review fixes for step 6 (#923): a control gets its tries back after the form changes, and the
proof screenshot is taken of the tab that shows the confirmation."""
from __future__ import annotations

import pytest

from tests.test_browser_agent import _ref, _site_resolver, _to_site, needs_chromium

PAGE = """<!doctype html><html><head><title>Proof - Acme</title></head><body>
<label for="nm">Full name</label><input id="nm">
<button type="button" id="next">Next</button>
<form onsubmit="event.preventDefault(); window.open('/thanks', '_blank');">
  <button type="submit">Submit application</button></form>
</body></html>"""
THANKS = "<html><head><title>Thanks</title></head><body><h1>Thank you for applying</h1></body></html>"


@pytest.fixture
async def proof_site():
    from aiohttp import web

    def page(html):
        async def handler(_):
            return web.Response(text=html, content_type="text/html")

        return handler

    app = web.Application()
    app.router.add_get("/apply", page(PAGE))
    app.router.add_get("/thanks", page(THANKS))
    runner = web.AppRunner(app)
    await runner.setup()
    tcp = web.TCPSite(runner, "127.0.0.1", 0)
    await tcp.start()
    yield tcp._server.sockets[0].getsockname()[1]  # noqa: SLF001
    await runner.cleanup()


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_a_dead_control_gets_its_tries_back_after_the_form_changes(proof_site):
    from src.services.browser_agent.agent import BrowserError, BrowserPool, run

    pool = BrowserPool(max_sessions=1, idle_s=600, resolve=_site_resolver(), open_connection=_to_site(proof_site))
    try:
        page = await run(pool, "u1", "open", url="http://jobs.example/apply")
        nxt = _ref(page, "Next")
        for _ in range(2):
            assert (await run(pool, "u1", "click", ref=nxt))["page_changed"] is False
        with pytest.raises(BrowserError):
            await run(pool, "u1", "click", ref=nxt)
        await run(pool, "u1", "type", ref=_ref(page, "Full name"), text="Ada")   # the form changed
        assert (await run(pool, "u1", "click", ref=nxt))["page_changed"] is False   # tried again, not refused
    finally:
        await pool.shutdown()


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_the_proof_is_the_tab_that_shows_the_confirmation(proof_site, monkeypatch):
    from src.services.browser_agent import agent
    from src.services.browser_agent.agent import BrowserPool, Gate, run

    shot_of: list[str] = []

    async def fake_save(page, gate):
        shot_of.append(page.url)
        return True

    monkeypatch.setattr(agent, "_save_proof", fake_save)

    async def go(form_url):
        return {"decision": "submit", "reason": "auto_when_sure", "detail": ""}

    async def mark():
        return None

    pool = BrowserPool(max_sessions=1, idle_s=600, resolve=_site_resolver(), open_connection=_to_site(proof_site))
    try:
        page = await run(pool, "u1", "open", url="http://jobs.example/apply")
        out = await run(pool, "u1", "click", ref=_ref(page, "Submit application"), gate=Gate(check=go, mark_attempt=mark))
        assert out["confirmation_seen"] is True and out["url"].endswith("/thanks")
        assert shot_of == [out["url"]]   # the new tab, not the form behind it
    finally:
        await pool.shutdown()
