"""Audit 6.2 (owner rule 2026-11-18, "Claude is the intelligence"): CAPTCHA words in the page TEXT alone are a
hint on the look result, never a stop. A job ad for a bot-detection firm describes its product ("our checks
verify you are human ... flag unusual traffic") and was handed to the user's PC as a CAPTCHA. A real check on
the page (a CAPTCHA frame, or a widget / challenge element in the page) still stops exactly as before, and
Job360's browser never solves one.
"""
from __future__ import annotations

import contextlib
import json

import pytest

from tests.test_browser_agent import _ref, _site_resolver, _to_site, needs_chromium

FORM = """<label>Full name <input type="text" name="name" required></label>
<label>Email <input type="text" name="email" required></label>
<label>CV <input type="file" name="cv" required></label>
<button type="submit">Submit application</button>"""

# The ad: the firm's own product described in plain words, above an ordinary application form.
BOT_AD = f"""<!doctype html><html><head><title>Careers</title></head><body>
<h1>Backend Engineer</h1>
<p>We build CAPTCHA and bot-detection products. Our checks verify you are human in under a second and flag
unusual traffic.</p>{FORM}</body></html>"""

# The same words with a real widget in the page (no frame yet): still a stop.
WIDGET = f"""<!doctype html><html><head><title>Apply</title></head><body>
<p>Please verify you are human to continue.</p><div class="g-recaptcha" data-sitekey="k"></div>{FORM}</body></html>"""

# A picture of letters to copy: the image and its box are the challenge element.
PICTURE = """<!doctype html><html><head><title>Apply</title></head><body>
<p>Type the characters you see in the picture</p><img src="/captcha.png" alt="captcha">
<label>Code <input type="text" name="captcha_code"></label></body></html>"""

QUOTE = "products. Our checks verify you are human in under a second and flag"


@pytest.fixture
async def pool():
    from aiohttp import web

    from src.services.browser_agent.agent import BrowserPool

    def page(html):
        async def serve(_):
            return web.Response(text=html, content_type="text/html")
        return serve

    app = web.Application()
    for path, html in (("/ad", BOT_AD), ("/widget", WIDGET), ("/picture", PICTURE)):
        app.router.add_get(path, page(html))
    runner = web.AppRunner(app)
    await runner.setup()
    tcp = web.TCPSite(runner, "127.0.0.1", 0)
    await tcp.start()
    port = tcp._server.sockets[0].getsockname()[1]  # noqa: SLF001 - the test site's own port
    p = BrowserPool(max_sessions=1, idle_s=600, resolve=_site_resolver(), open_connection=_to_site(port))
    yield p
    await p.shutdown()
    await runner.cleanup()


def test_the_quote_is_the_pages_own_words_around_the_phrase():
    from src.services.browser_agent.safety import captcha_text_quote

    text = "We build CAPTCHA and bot-detection products. Our checks verify you are human in under a second and flag"
    assert captcha_text_quote(text) == QUOTE
    assert captcha_text_quote("Apply for Senior Engineer") == ""
    assert len(captcha_text_quote("Please verify you are human " + "x " * 400)) <= 200


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_captcha_words_in_an_ad_are_a_hint_and_the_form_still_works(pool):
    from src.services.browser_agent.agent import _page_captcha, run

    page = await run(pool, "u1", "open", url="http://jobs.example/ad")
    assert page["status"] == "ok", page
    assert page["application_form"] is True
    assert page["signals"] == [{"signal": "captcha_text", "quote": QUOTE}]
    assert await _page_captcha(pool._sessions["u1"].page) is False  # noqa: SLF001
    typed = await run(pool, "u1", "type", ref=_ref(page, "Full name"), text="Ada Lovelace")
    assert typed["status"] == "ok" and typed["typed_matches"] is True


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/widget", "/picture"])
async def test_a_real_challenge_element_still_stops(pool, path):
    from src.services.browser_agent.agent import BrowserError, _page_captcha, run

    page = await run(pool, "u1", "open", url=f"http://jobs.example{path}")
    assert (page["status"], page["reason"]) == ("needs_you", "captcha"), page
    assert "signals" not in page
    assert await _page_captcha(pool._sessions["u1"].page) is True  # noqa: SLF001
    with pytest.raises(BrowserError) as stop:
        await run(pool, "u1", "type", ref=page["elements"][0]["ref"], text="x")
    assert stop.value.reason == "captcha"


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_the_route_records_nothing_for_captcha_words_alone(pool, authenticated_async_context, monkeypatch):
    """The real route on the real ad: no ``blocked`` captcha, the job is not handed to the PC."""
    from src.api.dependencies import get_request_db
    from src.core import settings
    from src.services.browser_agent import agent

    monkeypatch.setattr(settings, "BROWSER_AGENT_ENABLED", True)
    monkeypatch.setattr(agent, "get_pool", lambda: pool)
    url = "http://jobs.example/ad"
    async with authenticated_async_context() as client:
        resp = await client.post("/api/jobs/bring", json={
            "title": "Backend Engineer", "company": "Acme", "location": "London",
            "apply_url": url, "description": "Build services. " * 10,
        })
        app_id = int(resp.json()["application_id"])
        out = (await client.post("/api/browser", json={"action": "open", "url": url, "application_id": app_id})).json()
        assert out["status"] == "ok", out
        assert "blocked_recorded" not in out
        assert [s["signal"] for s in out["signals"]] == ["captcha_text"]
        user_id = authenticated_async_context.fixture_user_id
        async with contextlib.asynccontextmanager(get_request_db)() as db:
            cur = await db._db.execute(
                "SELECT payload FROM application_events WHERE user_id = ? AND application_id = ? "
                "AND event_type = 'blocked'", (user_id, app_id),
            )
            assert [json.loads(r["payload"]) for r in await cur.fetchall()] == []
