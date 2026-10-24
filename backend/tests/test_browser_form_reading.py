"""Reading a Lever form (live failure on Palantir's form, 2026-10-24): a 1,500-option <select> no longer
eats the page text, a bare textarea is named by its question, and each Yes / No knows its question."""
from __future__ import annotations

import pytest

from tests.test_browser_agent import _site_resolver, _to_site, needs_chromium

# The shapes of Lever's form: a university list, then custom questions with no <label> and no aria.
LEVER = """<!doctype html><html><head><title>Apply - Palantir</title></head><body><form>
<li class="application-question"><div class="application-label"><div class="text">University</div></div>
<div class="application-field"><select name="cards[u1][field0]"><option value="">Select...</option>
""" + "".join(f"<option>University of Somewhere number {i}</option>" for i in range(1500)) + """
</select></div></li>
<li class="application-question"><div class="application-label"><div class="text">
Why do you want to work at Palantir?</div></div>
<div class="application-field"><textarea name="cards[f526][field0]"></textarea></div></li>
<li class="application-question"><div class="application-label"><div class="text">
Are you legally authorized to work in the UK?</div></div>
<div class="application-field"><ul>
<li><label><input type="radio" name="cards[a1][field1]" value="Yes"><span>Yes</span></label></li>
<li><label><input type="radio" name="cards[a1][field1]" value="No"><span>No</span></label></li>
</ul></div></li>
<li class="application-question"><div class="application-label"><div class="text">
Will you now or in the future require sponsorship?</div></div>
<div class="application-field"><ul>
<li><label><input type="radio" name="cards[a2][field2]" value="Yes"><span>Yes</span></label></li>
<li><label><input type="radio" name="cards[a2][field2]" value="No"><span>No</span></label></li>
</ul></div></li>
<p>I'm not a robot</p>
</form></body></html>"""


@pytest.fixture
async def lever_site():
    from aiohttp import web

    async def page(_):
        return web.Response(text=LEVER, content_type="text/html")

    app = web.Application()
    app.router.add_get("/apply", page)
    runner = web.AppRunner(app)
    await runner.setup()
    tcp = web.TCPSite(runner, "127.0.0.1", 0)
    await tcp.start()
    yield tcp._server.sockets[0].getsockname()[1]  # noqa: SLF001
    await runner.cleanup()


async def _open(pool):
    from src.services.browser_agent.agent import run

    return await run(pool, "u1", "open", url="http://jobs.example/apply")


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_a_huge_select_does_not_eat_the_page_text(lever_site):
    from src.services.browser_agent.agent import BrowserPool

    pool = BrowserPool(max_sessions=1, idle_s=600, resolve=_site_resolver(), open_connection=_to_site(lever_site))
    try:
        page = await _open(pool)
        text = page["text"]
        assert "Why do you want to work at Palantir?" in text
        assert "Will you now or in the future require sponsorship?" in text
        assert "University of Somewhere number" not in text   # the options are in the element list only
        assert page["status"] == "needs_you" and page["reason"] == "captcha"   # text below the list still counts
    finally:
        await pool.shutdown()


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_the_captcha_check_reads_past_a_huge_select(lever_site):
    from src.services.browser_agent.agent import BrowserPool, _page_captcha, run

    pool = BrowserPool(max_sessions=1, idle_s=600, resolve=_site_resolver(), open_connection=_to_site(lever_site))
    try:
        await _open(pool)
        assert await _page_captcha(pool._sessions["u1"].page)  # noqa: SLF001
        assert (await run(pool, "u1", "look"))["reason"] == "captcha"
    finally:
        await pool.shutdown()


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_an_unlabelled_textarea_is_named_by_its_question_and_choices_carry_theirs(lever_site):
    from src.services.browser_agent.agent import BrowserPool

    pool = BrowserPool(max_sessions=1, idle_s=600, resolve=_site_resolver(), open_connection=_to_site(lever_site))
    try:
        els = (await _open(pool))["elements"]
        area = next(e for e in els if e["tag"] == "textarea")
        assert area["name"] == "Why do you want to work at Palantir?"
        assert next(e for e in els if e["tag"] == "select")["name"] == "University"
        radios = [e for e in els if e["type"] == "radio"]
        assert [e["name"] for e in radios] == ["Yes", "No", "Yes", "No"]
        assert [e["group"] for e in radios] == ["Are you legally authorized to work in the UK?"] * 2 + [
            "Will you now or in the future require sponsorship?"
        ] * 2
    finally:
        await pool.shutdown()


GATE = """<!doctype html><html><head><title>Apply</title></head><body><form>
<div class="q"><div class="text">Cover note</div><textarea name="cards[x][field0]"></textarea></div>
<div class="row"><span>Ready?</span><button type="submit">Submit application</button></div>
</form></body></html>"""


@pytest.fixture
async def gate_site():
    from aiohttp import web

    async def page(_):
        return web.Response(text=GATE, content_type="text/html")

    app = web.Application()
    app.router.add_get("/apply", page)
    runner = web.AppRunner(app)
    await runner.setup()
    tcp = web.TCPSite(runner, "127.0.0.1", 0)
    await tcp.start()
    yield tcp._server.sockets[0].getsockname()[1]  # noqa: SLF001
    await runner.cleanup()


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_a_button_beside_question_text_is_still_named_by_what_it_shows(gate_site):
    from src.services.browser_agent.agent import BrowserError, BrowserPool, run

    pool = BrowserPool(max_sessions=1, idle_s=600, resolve=_site_resolver(), open_connection=_to_site(gate_site))
    try:
        els = (await _open(pool))["elements"]
        area = next(e for e in els if e["tag"] == "textarea")
        assert area["name"] == "Cover note"
        assert (await run(pool, "u1", "type", ref=area["ref"], text="Hello"))["status"] == "ok"
        button = next(e for e in els if e["tag"] == "button")
        assert button["name"] == "Submit application"
        with pytest.raises(BrowserError) as gated:   # the gate still reads it as the final submit
            await run(pool, "u1", "click", ref=button["ref"])
        assert gated.value.reason == "submit_needs_application"
    finally:
        await pool.shutdown()
