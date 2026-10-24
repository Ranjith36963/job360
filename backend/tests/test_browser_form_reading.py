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


# Review of #1174: the text budget counts folded text (indentation is not words); a field named by the
# question above it still shows the safety checks its title and name attribute; a big group reads fast.
_IND = "\n" + " " * 24
REVIEW = {
    "/indented": "<!doctype html><html><body>" + "".join(
        f"{_IND}<div>{_IND}  <span>Q{i}</span>{_IND}</div>" for i in range(300)
    ) + "<p>I'm not a robot</p></body></html>",
    "/fields": """<!doctype html><html><body><form>
<div><div>Enter it below</div><input type="text" name="password" title="Password"></div>
<div><div>Your secret</div><input type="text" name="cards[s][field0]" title="Password"></div>
<div><div>Your code</div><input type="text" name="password"></div>
<div><div>Website</div><input type="text" title="Leave this field blank"></div>
<div><div>Full name</div><input type="text" name="cards[n][field0]"></div>
</form></body></html>""",
    "/parts": """<!doctype html><html><body><form>
<div><label>Do not fill this in<input type="text" name="website"></label></div>
<div><div>Your site</div><input type="text" title="Do not fill this in" name="website"></div>
<div><div>Enter it below</div><input type="text" name="password" title="Please read this note. Please read this note. Please read this note. Please read this note. Please read this note. Please read this note. Please read this note. Please read this note. Please read this note. Please read this note. Please read this note. "></div>
<div><div>Which exams did you pass</div><input type="text" title="Word limit 200" name="cards[e][field0]"></div>
</form></body></html>""",
    "/boxes": "<!doctype html><html><body><form><div><div>Skills</div><div>" + "".join(
        f"<div><label><input type=checkbox name='skills[]'>Skill {i}</label></div>" for i in range(2000)
    ) + "</div></div></form></body></html>",
}


@pytest.fixture
async def review_site():
    from aiohttp import web

    async def page(request):
        return web.Response(text=REVIEW[request.path], content_type="text/html")

    app = web.Application()
    for path in REVIEW:
        app.router.add_get(path, page)
    runner = web.AppRunner(app)
    await runner.setup()
    tcp = web.TCPSite(runner, "127.0.0.1", 0)
    await tcp.start()
    yield tcp._server.sockets[0].getsockname()[1]  # noqa: SLF001
    await runner.cleanup()


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_indentation_never_pushes_the_captcha_words_past_the_text_budget(review_site):
    from src.services.browser_agent.agent import BrowserPool, _page_captcha, run

    pool = BrowserPool(max_sessions=1, idle_s=600, resolve=_site_resolver(), open_connection=_to_site(review_site))
    try:
        page = await run(pool, "u1", "open", url="http://jobs.example/indented")
        assert "Q299" in page["text"]   # >6,000 characters of raw whitespace come before it
        assert page["status"] == "needs_you" and page["reason"] == "captcha"
        assert await _page_captcha(pool._sessions["u1"].page)  # noqa: SLF001
    finally:
        await pool.shutdown()


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_a_field_named_by_its_question_is_still_known_as_a_password_box(review_site):
    from src.services.browser_agent.agent import BrowserError, BrowserPool, run

    pool = BrowserPool(max_sessions=1, idle_s=600, resolve=_site_resolver(), open_connection=_to_site(review_site))
    try:
        page = await run(pool, "u1", "open", url="http://jobs.example/fields")
        by_name = {e["name"]: e for e in page["elements"]}
        assert set(by_name) == {"Enter it below", "Your secret", "Your code", "Website", "Full name"}
        assert all("hints" not in e for e in page["elements"])   # the look shows the question only
        names = ("Enter it below", "Your secret", "Your code")
        for name in names:
            with pytest.raises(BrowserError) as exc:
                await run(pool, "u1", "type", ref=by_name[name]["ref"], text="hunter2")
            assert exc.value.reason == "password_field", name
        rows = (await run(pool, "u1", "fill", fields=[{"ref": by_name[n]["ref"], "text": "x"} for n in names]))[
            "filled"]
        assert [r["reason"] for r in rows] == ["password_field"] * 3
    finally:
        await pool.shutdown()


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_a_trap_field_named_by_its_question_is_still_left_blank(review_site):
    from src.services.browser_agent.agent import BrowserError, BrowserPool, run

    pool = BrowserPool(max_sessions=1, idle_s=600, resolve=_site_resolver(), open_connection=_to_site(review_site))
    try:
        page = await run(pool, "u1", "open", url="http://jobs.example/fields")
        by_name = {e["name"]: e for e in page["elements"]}
        assert by_name["Website"].get("leave_blank") is True
        assert "leave_blank" not in by_name["Full name"]
        with pytest.raises(BrowserError) as exc:
            await run(pool, "u1", "type", ref=by_name["Website"]["ref"], text="https://example.com")
        assert exc.value.reason == "leave_blank"
        rows = (await run(pool, "u1", "fill", fields=[{"ref": by_name["Website"]["ref"], "text": "x"}]))["filled"]
        assert [r["reason"] for r in rows] == ["leave_blank"]
        typed = await run(pool, "u1", "type", ref=by_name["Full name"]["ref"], text="Ada Lovelace")
        assert typed["typed_matches"] is True
        values = await pool._sessions["u1"].page.evaluate(  # noqa: SLF001
            "() => Array.from(document.querySelectorAll('input')).map((i) => i.value)")
        assert values == ["", "", "", "", "Ada Lovelace"]
    finally:
        await pool.shutdown()


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_a_2000_box_group_is_named_by_its_question(review_site):
    from src.services.browser_agent.agent import BrowserPool, run

    pool = BrowserPool(max_sessions=1, idle_s=600, resolve=_site_resolver(), open_connection=_to_site(review_site))
    try:
        for page in (await run(pool, "u1", "open", url="http://jobs.example/boxes"), await run(pool, "u1", "look")):
            assert page["status"] == "ok", page.get("reason")
            boxes = [e for e in page["elements"] if e["type"] == "checkbox"]
            assert boxes and all(e["group"] == "Skills" for e in boxes)
    finally:
        await pool.shutdown()


async def _open_parts(pool):
    from src.services.browser_agent.agent import run

    els = (await run(pool, "u1", "open", url="http://jobs.example/parts"))["elements"]
    assert [e["name"] for e in els] == [
        "Do not fill this in", "Your site", "Enter it below", "Which exams did you pass",
    ]
    assert all("hints" not in e for e in els)
    return els


# Name, title and name attribute are each checked on their own (#1174 re-review): joined, "Do not fill
# this in" + "website" no longer ended the trap phrase, a 250-character title clipped name="password"
# away, and "pass" + "Word limit" spelled a password.
@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_a_do_not_fill_trap_is_caught_beside_its_name_attribute(review_site):
    from src.services.browser_agent.agent import BrowserError, BrowserPool, run

    pool = BrowserPool(max_sessions=1, idle_s=600, resolve=_site_resolver(), open_connection=_to_site(review_site))
    try:
        els = await _open_parts(pool)
        assert [bool(e.get("leave_blank")) for e in els] == [True, True, False, False]
        for e in els[:2]:
            with pytest.raises(BrowserError) as exc:
                await run(pool, "u1", "type", ref=e["ref"], text="https://example.com")
            assert exc.value.reason == "leave_blank", e["name"]
        rows = (await run(pool, "u1", "fill", fields=[{"ref": e["ref"], "text": "x"} for e in els[:2]]))["filled"]
        assert [r["reason"] for r in rows] == ["leave_blank", "leave_blank"]
    finally:
        await pool.shutdown()


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_a_long_title_never_hides_a_password_name_attribute(review_site):
    from src.services.browser_agent.agent import BrowserError, BrowserPool, run

    pool = BrowserPool(max_sessions=1, idle_s=600, resolve=_site_resolver(), open_connection=_to_site(review_site))
    try:
        els = await _open_parts(pool)
        with pytest.raises(BrowserError) as exc:
            await run(pool, "u1", "type", ref=els[2]["ref"], text="hunter2")
        assert exc.value.reason == "password_field"
        rows = (await run(pool, "u1", "fill", fields=[{"ref": els[2]["ref"], "text": "x"}]))["filled"]
        assert [r["reason"] for r in rows] == ["password_field"]
    finally:
        await pool.shutdown()


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_pass_and_a_word_limit_title_never_spell_a_password(review_site):
    from src.services.browser_agent.agent import BrowserPool, run

    pool = BrowserPool(max_sessions=1, idle_s=600, resolve=_site_resolver(), open_connection=_to_site(review_site))
    try:
        els = await _open_parts(pool)
        typed = await run(pool, "u1", "type", ref=els[3]["ref"], text="A-levels")
        assert typed["typed_matches"] is True
        values = await pool._sessions["u1"].page.evaluate(  # noqa: SLF001
            "() => Array.from(document.querySelectorAll('input')).map((i) => i.value)")
        assert values == ["", "", "", "A-levels"]
    finally:
        await pool.shutdown()
