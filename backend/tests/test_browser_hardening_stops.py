"""Audit fixes for the hard stops (docs/product/BROWSER_AGENT.md).

* Enter is pressed only on a highlighted option: an open list that shows "No options" has nothing to
  pick, and Enter there submits the form. A submit that Enter sets off anyway is stopped and refused.
* Once a page shows a CAPTCHA, nothing on it is clicked, typed, ticked or keyed: the cloud browser
  must never tick the "I'm not a robot" box itself.
"""
from __future__ import annotations

import pytest

from tests.test_browser_agent import _ref, _site_resolver, _to_site, needs_chromium

NO_OPTIONS = """<!doctype html><html><head><title>Empty list - Acme</title></head><body>
<form onsubmit="event.preventDefault(); document.title='SUBMITTED';">
  <label for="loc">Location</label>
  <input id="loc" role="combobox" aria-expanded="true" aria-autocomplete="list" aria-controls="lb">
  <div id="lb" role="listbox"><div>No options</div></div>
  <button type="submit">Submit application</button>
</form></body></html>"""
# An option IS highlighted, but the site lets Enter through to the form (implicit submission).
SUBMITS_ANYWAY = """<!doctype html><html><head><title>Leaky list - Acme</title></head><body>
<form onsubmit="event.preventDefault(); document.title='SUBMITTED';">
  <label for="city">City</label>
  <input id="city" role="combobox" aria-expanded="true" aria-controls="lb2" aria-activedescendant="o1">
  <ul id="lb2" role="listbox"><li id="o1" role="option">Paris</li></ul>
  <button type="submit">Submit application</button>
</form></body></html>"""
CAPTCHA_PAGE = """<!doctype html><html><head><title>Apply - Acme</title></head><body>
<form><label for="nm">Full name</label><input id="nm"></form>
<iframe style="width:320px;height:90px" src="/recaptcha/api2/anchor?ar=1&k=abc&size=normal"></iframe>
</body></html>"""
ANCHOR = """<!doctype html><html><body>
<div role="checkbox" aria-checked="false" aria-label="Robot check" tabindex="0" style="width:28px;height:28px"
  onclick="this.setAttribute('aria-checked', 'true')"
  onkeydown="if (event.key===' ' || event.key==='Enter') this.setAttribute('aria-checked', 'true')"></div>
</body></html>"""
# The invisible kind, waiting (Ashby, Lever): the challenge frames are full size but parked in a hidden
# box off the page until they ask something. Nothing is asked yet, so the form is filled as usual.
WAITING_CHALLENGE = """<!doctype html><html><head><title>Apply - Waiting</title></head><body>
<form><label for="nm2">Full name</label><input id="nm2"></form>
<div style="visibility:hidden;position:absolute;top:-10000px;left:0;width:100%">
  <iframe style="width:400px;height:580px" src="/recaptcha/api2/bframe?hl=en&k=abc"></iframe></div>
<div style="visibility:hidden;position:absolute;top:-10000px">
  <iframe style="width:400px;height:600px" src="/hcaptcha.com/captcha/v1/x/static/hcaptcha.html#frame=challenge"></iframe>
</div>
<button type="button" onclick="document.querySelectorAll('div[style]').forEach((d) => d.removeAttribute('style'))">
  Show the challenge</button>
</body></html>"""
CHALLENGE = "<!doctype html><html><body><p>Select all images with a bus</p></body></html>"
PAGES = {"/no-options": NO_OPTIONS, "/submits-anyway": SUBMITS_ANYWAY, "/apply": CAPTCHA_PAGE,
         "/recaptcha/api2/anchor": ANCHOR, "/waiting": WAITING_CHALLENGE, "/recaptcha/api2/bframe": CHALLENGE,
         "/hcaptcha.com/captcha/v1/x/static/hcaptcha.html": CHALLENGE}


def _html(text):
    from aiohttp import web

    async def handler(_):
        return web.Response(text=text, content_type="text/html")

    return handler


@pytest.fixture
async def stops_site():
    from aiohttp import web

    app = web.Application()
    for path, html in PAGES.items():
        app.router.add_get(path, _html(html))
    runner = web.AppRunner(app)
    await runner.setup()
    tcp = web.TCPSite(runner, "127.0.0.1", 0)
    await tcp.start()
    yield tcp._server.sockets[0].getsockname()[1]  # noqa: SLF001
    await runner.cleanup()


@pytest.fixture
async def stops_pool(stops_site):
    from src.services.browser_agent.agent import BrowserPool

    pool = BrowserPool(max_sessions=1, idle_s=600, resolve=_site_resolver(), open_connection=_to_site(stops_site))
    yield pool
    await pool.shutdown()


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_enter_in_an_open_list_with_no_options_is_refused(stops_pool):
    from src.services.browser_agent.agent import BrowserError, run

    page = await run(stops_pool, "u1", "open", url="http://jobs.example/no-options")
    with pytest.raises(BrowserError) as exc:
        await run(stops_pool, "u1", "key", ref=_ref(page, "Location"), text="Enter")
    assert exc.value.reason == "enter_may_submit"
    assert (await run(stops_pool, "u1", "look"))["title"] == "Empty list - Acme"  # never submitted


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_a_submit_that_enter_sets_off_is_stopped_and_refused(stops_pool):
    from src.services.browser_agent.agent import BrowserError, run

    page = await run(stops_pool, "u1", "open", url="http://jobs.example/submits-anyway")
    with pytest.raises(BrowserError) as exc:
        await run(stops_pool, "u1", "key", ref=_ref(page, "City"), text="Enter")
    assert exc.value.reason == "enter_may_submit"
    assert (await run(stops_pool, "u1", "look"))["title"] == "Leaky list - Acme"  # never submitted


async def _robot_box_ticked(pool) -> str:
    page = pool._sessions["u1"].page  # noqa: SLF001
    frame = next(f for f in page.frames if "recaptcha/api2/anchor" in f.url)
    return await frame.evaluate("() => document.querySelector('[role=checkbox]').getAttribute('aria-checked')")


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_nothing_is_done_on_a_page_that_shows_a_captcha(stops_pool):
    from src.services.browser_agent.agent import BrowserError, run

    page = await run(stops_pool, "u1", "open", url="http://jobs.example/apply")
    assert page["status"] == "needs_you" and page["reason"] == "captcha"
    robot, name = _ref(page, "Robot check"), _ref(page, "Full name")
    for action, ref, text in (("click", robot, ""), ("tick", robot, ""), ("key", robot, "Enter"),
                              ("type", name, "Ada"), ("key", "", "Tab"), ("scroll", "", "down")):
        with pytest.raises(BrowserError) as exc:
            await run(stops_pool, "u1", action, ref=ref, text=text)
        assert exc.value.reason == "captcha", action
    assert await _robot_box_ticked(stops_pool) == "false"
    assert (await run(stops_pool, "u1", "look"))["reason"] == "captcha"  # reading the page still works


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_the_captcha_frame_itself_is_never_acted_on(stops_pool, monkeypatch):
    """Defence in depth: even if the page-wide check missed it, a control inside a CAPTCHA frame is refused."""
    from src.services.browser_agent import agent
    from src.services.browser_agent.agent import BrowserError, run

    page = await run(stops_pool, "u1", "open", url="http://jobs.example/apply")

    async def no_captcha(_page):
        return False

    monkeypatch.setattr(agent, "_page_captcha", no_captcha, raising=False)
    with pytest.raises(BrowserError) as exc:
        await run(stops_pool, "u1", "click", ref=_ref(page, "Robot check"))
    assert exc.value.reason == "captcha"
    assert await _robot_box_ticked(stops_pool) == "false"


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_a_hidden_challenge_frame_asks_nothing_until_it_is_shown(stops_pool):
    """Live on Ashby and Lever: every form loads its invisible CAPTCHA's challenge frame, hidden. Read as a
    CAPTCHA, it stopped every application on those sites before a field was typed."""
    from src.services.browser_agent.agent import BrowserError, run

    page = await run(stops_pool, "u1", "open", url="http://jobs.example/waiting")
    assert page["status"] == "ok", page.get("reason")
    assert not [e for e in page["elements"] if "bus" in (e.get("name") or "")]  # hidden frame: not read
    page = await run(stops_pool, "u1", "type", ref=_ref(page, "Full name"), text="Ada")
    assert page["status"] == "ok"
    page = await run(stops_pool, "u1", "click", ref=_ref(page, "Show the challenge"))
    assert page["status"] == "needs_you" and page["reason"] == "captcha"  # now it asks: the user's
    with pytest.raises(BrowserError) as exc:
        await run(stops_pool, "u1", "type", ref=_ref(page, "Full name"), text="Ada Lovelace")
    assert exc.value.reason == "captcha"
