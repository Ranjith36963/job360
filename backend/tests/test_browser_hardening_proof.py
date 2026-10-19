"""Review fixes for step 6 (#923): a control gets its tries back after the form changes, the
proof screenshot is taken of the tab that shows the confirmation, a click whose effect is inside a
shadow root or a frame past the cap still counts, and "already submitted" is per application."""
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

# The button and the text it changes both live in an open shadow root (SmartRecruiters-style).
SHADOW = """<!doctype html><html><head><title>Shadow - Acme</title></head><body><div id="host"></div>
<script>
const root = document.getElementById('host').attachShadow({mode: 'open'});
root.innerHTML = '<p id="msg">Step 1</p><button type="button" id="go">Continue</button>';
root.getElementById('go').addEventListener('click', () => { root.getElementById('msg').textContent = 'Step 2'; });
</script></body></html>"""
# 20 hidden frames before the one that holds the form: the clicked control is in frame 21.
FRAMES = """<!doctype html><html><head><title>Frames - Acme</title></head><body>
""" + '<iframe style="display:none" src="/ad"></iframe>' * 20 + """
<iframe style="width:600px;height:200px" src="/inner"></iframe></body></html>"""
INNER = """<html><body><p id="m">Step 1</p>
<button type="button" onclick="document.getElementById('m').textContent='Step 2'">Continue</button></body></html>"""
# A form whose submit leaves the page as it is (no confirmation).
STAY = """<!doctype html><html><head><title>Stay - Acme</title></head><body>
<form onsubmit="event.preventDefault(); document.getElementById('n').textContent += '.';">
  <p id="n">sent</p><button type="submit">Submit application</button></form></body></html>"""


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
    app.router.add_get("/shadow", page(SHADOW))
    app.router.add_get("/frames", page(FRAMES))
    app.router.add_get("/inner", page(INNER))
    app.router.add_get("/ad", page("<p>ad</p>"))
    app.router.add_get("/stay", page(STAY))
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


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/shadow", "/frames"])
async def test_a_click_that_changes_a_shadow_root_or_a_late_frame_is_a_change(proof_site, path):
    from src.services.browser_agent.agent import BrowserPool, run

    pool = BrowserPool(max_sessions=1, idle_s=600, resolve=_site_resolver(), open_connection=_to_site(proof_site))
    try:
        page = await run(pool, "u1", "open", url=f"http://jobs.example{path}")
        out = await run(pool, "u1", "click", ref=_ref(page, "Continue"))
        assert out["page_changed"] is True
    finally:
        await pool.shutdown()


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_already_submitted_is_per_application_on_the_same_form_page(proof_site):
    from src.services.browser_agent.agent import BrowserError, BrowserPool, Gate, run

    asked: list[int] = []

    def gate_for(app_id):
        async def go(form_url):
            asked.append(app_id)
            return {"decision": "submit", "reason": "auto_when_sure", "detail": ""}

        async def mark():
            return None

        return Gate(check=go, mark_attempt=mark, application_id=app_id)

    pool = BrowserPool(max_sessions=1, idle_s=600, resolve=_site_resolver(), open_connection=_to_site(proof_site))
    try:
        page = await run(pool, "u1", "open", url="http://jobs.example/stay")
        submit = _ref(page, "Submit application")
        await run(pool, "u1", "click", ref=submit, gate=gate_for(1))
        with pytest.raises(BrowserError) as exc:
            await run(pool, "u1", "click", ref=submit, gate=gate_for(1))
        assert exc.value.reason == "already_submitted"
        await run(pool, "u1", "click", ref=submit, gate=gate_for(2))   # another job: its own gate decides
        assert asked == [1, 2]
    finally:
        await pool.shutdown()
