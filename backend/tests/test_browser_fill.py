"""Action fill: many fields in one call (owner decision 2026-10-23, the ElevenLabs run).

One 12-question form took about 12 type / choose calls and ~40k tokens, because every call returns the
whole page. fill sets up to 40 fields in one call and returns ONE page plus a read-back per field. Every
rule of the single actions applies per field (password, honeypot, control characters, length, CAPTCHA
frame, never the final submit), and a refused field never stops the others.
"""
from __future__ import annotations

import pytest

from tests.test_browser_agent import _ref, _site_resolver, _to_site, needs_chromium

FORM = """<!doctype html><html><head><title>Fill - Acme</title></head><body>
<form onsubmit="event.preventDefault(); document.title='SUBMITTED';">
  <label for="nm">Full name</label><input id="nm" name="name">
  <label for="why">Why us?</label><textarea id="why"></textarea>
  <label for="pw">Password</label><input id="pw" type="password">
  <label for="hp">Leave this field blank</label><input id="hp" name="hp">
  <label for="city">City</label><input id="city">
  <label for="c">Country</label><select id="c"><option>Choose</option><option>United Kingdom</option></select>
  <label><input id="ok" type="checkbox"> Keep me posted</label>
  <div role="combobox" aria-label="Notice period" tabindex="0" aria-expanded="false" style="min-height:24px"
    onclick="document.getElementById('lb').hidden = false; this.setAttribute('aria-expanded', 'true')"></div>
  <ul id="lb" role="listbox" hidden>
    <li role="option" onclick="document.querySelector('[role=combobox]').textContent = this.textContent">One month</li>
    <li role="option" onclick="document.querySelector('[role=combobox]').textContent = this.textContent">Three months</li>
  </ul>
  <button type="submit">Submit application</button>
</form></body></html>"""


@pytest.fixture
async def fill_site():
    from aiohttp import web

    async def page(_):
        return web.Response(text=FORM, content_type="text/html")

    app = web.Application()
    app.router.add_get("/fill", page)
    runner = web.AppRunner(app)
    await runner.setup()
    tcp = web.TCPSite(runner, "127.0.0.1", 0)
    await tcp.start()
    yield tcp._server.sockets[0].getsockname()[1]  # noqa: SLF001
    await runner.cleanup()


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_fill_sets_every_kind_of_field_in_one_call_and_refuses_only_the_bad_ones(fill_site):
    from src.services.browser_agent.agent import BrowserPool, run

    pool = BrowserPool(max_sessions=1, idle_s=600, resolve=_site_resolver(), open_connection=_to_site(fill_site))
    try:
        page = await run(pool, "u1", "open", url="http://jobs.example/fill")
        fields = [
            {"ref": _ref(page, "Password"), "text": "hunter2"},              # never typed
            {"ref": _ref(page, "Full name"), "text": "Ada Lovelace"},
            {"ref": _ref(page, "Why us?"), "text": "Line one.\r\nLine two."},
            {"ref": _ref(page, "Leave this field blank"), "text": "bot"},    # a honeypot
            {"ref": _ref(page, "City"), "text": "London\tUK"},               # a control key
            {"ref": _ref(page, "Country"), "text": "United Kingdom"},
            {"ref": _ref(page, "Keep me posted"), "text": "yes"},
            {"ref": _ref(page, "Notice period"), "text": "One month"},
            {"ref": _ref(page, "Submit application"), "text": "yes"},       # never clicked
            {"ref": "e999_zz", "text": "x"},                                  # not on the page
        ]
        out = await run(pool, "u1", "fill", fields=fields)
        rows = out["filled"]
        assert [r["ref"] for r in rows] == [f["ref"] for f in fields]
        assert [r["reason"] for r in rows] == [
            "password_field", "", "", "leave_blank", "control_characters", "", "", "", "submit_control",
            "ref_not_found",
        ]
        assert [r["ok"] for r in rows] == [False, True, True, False, False, True, True, True, False, False]
        assert rows[1]["value"] == "Ada Lovelace" and rows[2]["value"] == "Line one.\nLine two."
        assert rows[5]["value"] == "United Kingdom" and rows[6]["value"] == "yes"
        assert "One month" in rows[7]["value"]
        # ONE page result: the elements and text of the page after the whole fill (its password box
        # makes it a sign-in page, as look always says).
        assert out["reason"] == "login_required" and out["elements"] and "<<page-text " in out["text"]
        tab = pool._sessions["u1"].page  # noqa: SLF001
        assert await tab.title() == "Fill - Acme"   # never submitted
        assert await tab.eval_on_selector("#pw", "el => el.value") == ""
        assert await tab.eval_on_selector("#hp", "el => el.value") == ""
        assert await tab.eval_on_selector("#city", "el => el.value") == ""
        assert await tab.eval_on_selector("#ok", "el => el.checked") is True
        # The submit guard came off: the gated submit later is not cancelled by a guard left on.
        assert await tab.evaluate("() => window.__j360guard") is None
    finally:
        await pool.shutdown()


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_fill_reads_back_a_value_the_page_changed_and_a_box_needs_yes_or_no(fill_site):
    from src.services.browser_agent.agent import BrowserPool, run

    pool = BrowserPool(max_sessions=1, idle_s=600, resolve=_site_resolver(), open_connection=_to_site(fill_site))
    try:
        page = await run(pool, "u1", "open", url="http://jobs.example/fill")
        tab = pool._sessions["u1"].page  # noqa: SLF001
        await tab.evaluate(
            "() => { const el = document.getElementById('nm');"
            " el.addEventListener('input', () => { el.value = el.value.toUpperCase(); }); }"
        )
        out = await run(pool, "u1", "fill", fields=[
            {"ref": _ref(page, "Full name"), "text": "Ada Lovelace"},
            {"ref": _ref(page, "Keep me posted"), "text": "maybe"},
        ])
        assert out["filled"][0]["ok"] is False and out["filled"][0]["reason"] == "value_differs"
        assert out["filled"][0]["value"] == "ADA LOVELACE"
        assert out["filled"][1]["reason"] == "bad_tick_text"
    finally:
        await pool.shutdown()


@pytest.mark.asyncio
async def test_fill_needs_fields_and_takes_at_most_forty():
    from src.services.browser_agent.agent import MAX_FILL_FIELDS, BrowserError, _fill

    assert MAX_FILL_FIELDS == 40
    for fields in ([], [{"ref": "e1_ab", "text": "x"}] * 41):
        with pytest.raises(BrowserError) as exc:
            await _fill(None, fields)  # type: ignore[arg-type] - refused before the page is touched
        assert exc.value.reason in ("no_fields", "too_many_fields")


def test_the_route_caps_fields_and_their_text():
    from pydantic import ValidationError

    from src.api.routes.browser import BrowserActionRequest
    from src.services.browser_agent.agent import ACTIONS, MAX_TYPE_CHARS

    assert "fill" in ACTIONS
    ok = BrowserActionRequest.model_validate({"action": "fill", "fields": [{"ref": "e1_ab", "text": "Ada"}]})
    assert ok.fields[0].text == "Ada"
    for bad in (
        [{"ref": "e1_ab", "text": "x"}] * 41,
        [{"ref": "e1_ab", "text": "x" * (MAX_TYPE_CHARS + 1)}],
        [{"ref": "e1_ab", "text": "x", "on": True}],
    ):
        with pytest.raises(ValidationError):
            BrowserActionRequest.model_validate({"action": "fill", "fields": bad})


@pytest.mark.asyncio
async def test_the_mcp_tool_passes_fields_to_the_route(monkeypatch):
    """MCP tools call the route function (rule M5): fields must reach browser_agent.run unchanged."""
    from src.api import mcp_server
    from src.core import settings
    from src.services.browser_agent import agent

    seen: dict = {}

    async def fake_run(pool, user_id, action, **kw):
        seen.update(action=action, fields=kw.get("fields"))
        return {"status": "ok", "filled": []}

    monkeypatch.setattr(settings, "BROWSER_AGENT_ENABLED", True)
    monkeypatch.setattr(agent, "run", fake_run)
    monkeypatch.setattr(agent, "get_pool", lambda: None)
    monkeypatch.setattr(mcp_server, "_user", lambda: _FakeUser())
    fields = [{"ref": "e1_ab", "text": "Ada"}]
    await mcp_server.build_server().call_tool("browser", {"action": "fill", "fields": fields})
    assert seen == {"action": "fill", "fields": fields}


class _FakeUser:
    id = "u-fill"
    auth_via = "token"
