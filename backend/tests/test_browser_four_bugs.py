"""Four small fixes to the server browser's page reader (2026-11-03).

1. react-select's blank required twin input is not a field; the visible combobox is, and carries the mark.
2. A radio / checkbox group names its question (legend, aria-labelledby, label) and is required once.
3. look reports an HTTP error page (429, 4xx, 5xx) instead of "ok".
4. Workday's tips say the start window is slow to appear.
"""
from __future__ import annotations

import pytest

from src.services.browser_agent.sites import SITES, site_for
from tests.test_browser_agent import _site_resolver, _to_site, needs_chromium

TWIN = """<!doctype html><html><head><title>Greenhouse</title></head><body>
<div class="field"><label id="c-l" for="c">Country</label>
<div class="select__container"><div class="select__control"><div class="select__value-container">
<div class="select__input-container"><input class="select__input" id="c" role="combobox" aria-labelledby="c-l" value="">
</div></div></div>
<input name="country" required tabindex="-1" aria-hidden="true" value=""
       style="position:absolute;bottom:0;left:0;width:100%;height:0;opacity:0"></div></div>
<label>Nickname <input type="text" name="nick" tabindex="-1" aria-hidden="true" style="height:0"></label>
</body></html>"""

GROUPS = """<!doctype html><html><head><title>Questions</title></head><body>
<fieldset><legend>Do you have the right to work here?</legend>
<label><input type="radio" name="rtw" value="y" required> Yes</label>
<label><input type="radio" name="rtw" value="n" required> No</label></fieldset>
<div role="group" aria-labelledby="tl"><span id="tl">Which tools do you use?</span>
<label><input type="checkbox" name="t1" value="a"> Python</label>
<label><input type="checkbox" name="t2" value="b" aria-required="true"> Go</label></div>
<div class="field"><label class="label">How did you hear about us?</label>
<div class="options"><label><input type="checkbox" name="h" value="a"> Friend</label>
<label><input type="checkbox" name="h" value="b"> Ad</label></div></div>
<fieldset><legend>About you</legend>
<label><input type="radio" name="vet" value="y" required> Veteran yes</label>
<label><input type="radio" name="vet" value="n" required> Veteran no</label>
<label><input type="radio" name="dis" value="y"> Disability yes</label>
<label><input type="radio" name="dis" value="n"> Disability no</label></fieldset>
<label><input type="checkbox" name="consent" required> I agree to the privacy policy</label>
</body></html>"""

PLAIN = "<!doctype html><html><head><title>Hello</title></head><body><p>Hi there</p></body></html>"


@pytest.fixture
async def pool():
    from aiohttp import web

    from src.services.browser_agent.agent import BrowserPool

    def page(html, status=200):
        async def serve(_):
            return web.Response(text=html, content_type="text/html", status=status)
        return serve

    app = web.Application()
    for path, html, status in (
        ("/twin", TWIN, 200), ("/groups", GROUPS, 200), ("/ok", PLAIN, 200),
        ("/limited", PLAIN, 429), ("/gone", PLAIN, 404), ("/broken", PLAIN, 503), ("/walled", PLAIN, 403),
    ):
        app.router.add_get(path, page(html, status))
    runner = web.AppRunner(app)
    await runner.setup()
    tcp = web.TCPSite(runner, "127.0.0.1", 0)
    await tcp.start()
    port = tcp._server.sockets[0].getsockname()[1]  # noqa: SLF001 - the test site's own port
    p = BrowserPool(max_sessions=1, idle_s=600, resolve=_site_resolver(), open_connection=_to_site(port))
    yield p
    await p.shutdown()
    await runner.cleanup()


def test_workday_tips_say_the_start_window_is_slow():
    site = next(s for s in SITES if s.name == "workday")
    assert any("3-8 s" in t and "look again" in t for t in site.tips)
    assert site_for("https://acme.wd3.myworkdayjobs.com/en-US/x/job/1").name == "workday"


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_a_react_select_twin_is_not_listed_and_the_combobox_is_required(pool):
    from src.services.browser_agent.agent import run

    page = await run(pool, "u1", "open", url="http://jobs.example/twin")
    country = [e for e in page["elements"] if e["name"].startswith("Country")]
    assert len(country) == 1 and country[0]["role"] == "combobox"
    assert country[0]["required"] is True   # the twin's required mark moved to the control the person sees
    assert not [e for e in page["elements"] if e["name"] == "country"]
    assert any(e["name"].startswith("Nickname") for e in page["elements"])  # a plain hidden-ish field stays


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_choice_groups_name_their_question_and_are_required_once(pool):
    from src.services.browser_agent.agent import run

    page = await run(pool, "u1", "open", url="http://jobs.example/groups")
    by = {e["name"]: e for e in page["elements"] if e["type"] in ("radio", "checkbox")}
    for name in ("Yes", "No"):
        assert by[name]["group"].startswith("Do you have the right to work")
        assert by[name]["required"] is False and by[name]["group_required"] is True
    for name in ("Python", "Go"):
        assert by[name]["group"].startswith("Which tools do you use")
        assert by[name]["required"] is False and by[name]["group_required"] is True
    for name in ("Friend", "Ad"):
        assert by[name]["group"].startswith("How did you hear about us")
        assert by[name]["required"] is False and "group_required" not in by[name]
    # One box, two questions: each name group is required on its own (the reviewer's case, #1488).
    for name in ("Veteran yes", "Veteran no"):
        assert by[name]["required"] is False and by[name]["group_required"] is True
    for name in ("Disability yes", "Disability no"):
        assert by[name]["required"] is False and "group_required" not in by[name]
    consent = next(e for e in page["elements"] if e["name"].startswith("I agree"))
    assert consent["required"] is True and "group_required" not in consent


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
@pytest.mark.parametrize(("path", "code"), [("limited", 429), ("gone", 404), ("broken", 503)])
async def test_look_reports_an_http_error_page_like_open(pool, path, code):
    from src.services.browser_agent.agent import run

    opened = await run(pool, "u1", "open", url=f"http://jobs.example/{path}")
    assert opened["status"] == "error" and opened["reason"] == "http_error"
    assert opened["transport"]["status"] == code
    seen = await run(pool, "u1", "look")
    assert seen["status"] == "error" and seen["reason"] == "http_error" and str(code) in seen["detail"]
    assert seen["transport"]["status"] == code and seen["transport"]["final_host"] == "jobs.example"


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_look_on_a_good_page_is_still_ok_and_an_error_clears_on_the_next_page(pool):
    from src.services.browser_agent.agent import run

    await run(pool, "u1", "open", url="http://jobs.example/limited")
    good = await run(pool, "u1", "open", url="http://jobs.example/ok")
    assert good["status"] == "ok" and good["transport"]["status"] == 200
    again = await run(pool, "u1", "look")
    assert again["status"] == "ok" and "transport" not in again


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_a_site_403_stays_a_page_but_its_transport_is_reported(pool):
    from src.services.browser_agent.agent import run

    await run(pool, "u1", "open", url="http://jobs.example/walled")
    seen = await run(pool, "u1", "look")
    assert seen["status"] == "ok" and seen["transport"]["status"] == 403
