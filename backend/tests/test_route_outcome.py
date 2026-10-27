"""Routes PR A (C3): an open's outcome comes from transport facts only - the main document's status, the
bot-wall vendors' headers and the host the tab ended on. Page text never makes ``bot_block``: a body-text
stop ("Just a moment...", a vendor's script) still stops the page, and its outcome is ``page_error``.
A ``bot_block`` on an open for a named application records ``blocked {reason: bot_check, by: browser}``."""
from __future__ import annotations

import contextlib
import json
from types import SimpleNamespace

import pytest

from src.services.browser_agent import outcome
from src.services.browser_agent.safety import blocker_for

JOB = "https://jobs.acme.com/apply/7"


def _t(status=200, headers=None, final=JOB, requested=JOB):
    return outcome.transport_of(status, headers or {}, requested, final)


@pytest.mark.parametrize(("transport", "expected"), [
    (_t(403), "bot_block"),
    (_t(999), "bot_block"),
    (_t(200, {"cf-mitigated": "challenge"}), "bot_block"),
    (_t(405, {"x-datadome": "protected"}), "bot_block"),
    (_t(400, {"akamai-grn": "0.1234.5678"}), "bot_block"),
    # DataDome and Akamai stamp their headers on ordinary pages too: a 200 with them is a normal page.
    (_t(200, {"x-datadome": "protected"}), "ok"),
    (_t(200, {"akamai-grn": "0.1234.5678"}), "ok"),
    (_t(200, final="https://challenges.cloudflare.com/cdn-cgi/challenge-platform/x"), "bot_block"),
    (_t(429), "rate_limited"),
    (_t(500), "page_error"),
    (_t(503), "page_error"),
    (_t(200, final="https://jobs.acme.com/users/sign_in?next=/apply/7"), "login_wall"),
    (_t(200, final="https://jobs.acme.com/login"), "login_wall"),
    (_t(200), "ok"),
    (_t(0), "ok"),
    # Headers that are only a vendor's name, or Akamai's ordinary ones, are no bot wall on their own.
    (_t(200, {"server": "cloudflare", "x-akamai-transformed": "9 - 0 pmb=mRUM,1"}), "ok"),
    (_t(200, {"cf-mitigated": "nothing"}), "ok"),
])
def test_each_status_and_header_gives_its_outcome(transport, expected):
    assert outcome.classify(transport) == expected
    assert expected in outcome.OUTCOMES


@pytest.mark.parametrize(("stop", "needs_you", "expected"), [
    ("timeout", "", "page_error"),
    ("load_failed", "", "page_error"),
    ("browser_error", "", "page_error"),
    ("", "login_required", "login_wall"),
    ("", "captcha", "captcha"),
])
def test_the_agents_own_stops_give_their_outcome(stop, needs_you, expected):
    assert outcome.classify(_t(200), stop_reason=stop, needs_you=needs_you) == expected


def test_rate_limited_is_its_own_outcome_never_a_block():
    assert outcome.classify(_t(429)) == "rate_limited"
    assert outcome.classify(_t(429), needs_you="captcha") == "rate_limited"


def test_transport_holds_only_closed_signals_and_the_canonical_host():
    t = _t(403, {"CF-Mitigated": "challenge", "x-datadome": "1", "akamai-grn": "r", "x-akamai-request-id": "i",
                 "server": "AkamaiGHost", "set-cookie": "secret=1"}, final="https://Jobs.Acme.com/apply/7")
    assert t == {"status": 403, "final_host": "jobs.acme.com",
                 "signals": ["akamai", "akamai_ref", "cf_challenge", "datadome", "server_akamai"]}
    assert set(t["signals"]) <= set(outcome.SIGNALS)


@pytest.mark.parametrize("body", [
    "Just a moment… Checking your browser before accessing jobs.acme.com.",
    '<script src="https://js.datadome.co/tags.js"></script> Please enable JS and disable any ad blocker',
])
def test_a_page_that_only_says_bot_check_is_page_error_never_bot_block(body):
    # 200, no headers: whatever the body says, the wire said nothing about a bot wall.
    blocker = blocker_for([JOB], body, False)
    stop = outcome.TEXT_STOP if blocker is not None and blocker.from_text else ""
    needs_you = blocker.reason if blocker is not None else ""
    got = outcome.classify(_t(200), stop_reason=stop, needs_you=needs_you)
    assert got != "bot_block"
    if blocker is not None:  # the text still stops the page for safety, as a page_error
        assert blocker.reason == "captcha" and got == "page_error"
    # Body text smuggled into the transport dict is never read.
    assert outcome.classify({**_t(200), "text": body, "signals": ["datadome-script"]}) == "ok"


def test_a_vendor_frame_is_a_captcha_not_page_text():
    blocker = blocker_for(["https://geo.captcha-delivery.com/captcha/?x=1"], "", False)
    assert blocker is not None and not blocker.from_text
    assert outcome.classify(_t(200), needs_you=blocker.reason) == "captcha"


@pytest.mark.parametrize("final", [
    "https://accounts.google.com/signin/v2",
    "https://login.microsoftonline.com/common/login",
    "https://auth.acme-sso.io/login",
])
def test_a_sign_in_redirect_to_another_host_is_not_a_login_wall(final):
    t = _t(200, final=final)
    assert "login_path" not in t["signals"]
    assert outcome.classify(t) == "ok"


class _Response:
    def __init__(self, status, headers):
        self.status = status
        self.headers = headers


def _open_page(status, headers, final):
    class Page:
        url = final

        def is_closed(self):
            return False

        async def goto(self, url, **_):
            return _Response(status, headers)

    page = Page()
    return page, SimpleNamespace(page=page, blocked_tabs={}, blocked_urls={}, context=SimpleNamespace(pages=[page]))


class _Screen:
    async def screen(self, url):
        return SimpleNamespace(ok=True, reason="")


async def _no_settle(_page):
    return None


@pytest.mark.asyncio
async def test_open_returns_the_transport_of_the_main_document(monkeypatch):
    from src.services.browser_agent import agent

    async def fake_look(_s, **_):
        return {"status": "ok", "text": "Access denied"}

    monkeypatch.setattr(agent, "look", fake_look)
    monkeypatch.setattr(agent, "_settle", _no_settle)
    _page, s = _open_page(403, {"x-datadome": "protected", "server": "nginx"}, JOB)
    result = await agent._act(SimpleNamespace(screen=_Screen()), s, "open", url=JOB, ref="", text="", on=True,
                              doc_kind="", gate=None, files=None)
    assert result["transport"] == {"status": 403, "final_host": "jobs.acme.com", "signals": ["datadome"]}


async def _blocked_rows(user_id, app_id):
    from src.api.dependencies import get_request_db

    async with contextlib.asynccontextmanager(get_request_db)() as db:
        cur = await db._db.execute(
            "SELECT payload FROM application_events WHERE user_id = ? AND application_id = ? "
            "AND event_type = 'blocked'", (user_id, app_id),
        )
        return [json.loads(r["payload"]) for r in await cur.fetchall()]


async def _bring(client):
    brought = await client.post("/api/jobs/bring", json={
        "title": "Engineer", "company": "Acme", "location": "London",
        "description": "Build services. " * 10, "apply_url": JOB,
    })
    return brought.json()["application_id"]


@pytest.mark.asyncio
async def test_the_gate_records_bot_check_on_a_403_open(authenticated_async_context, monkeypatch):
    from src.core import settings
    from src.services.browser_agent import agent

    monkeypatch.setattr(settings, "BROWSER_AGENT_ENABLED", True)

    async def fake_look(_s, **_):
        return {"status": "ok", "url": JOB, "text": "Access denied"}

    monkeypatch.setattr(agent, "look", fake_look)
    monkeypatch.setattr(agent, "_settle", _no_settle)

    async def fake_run(p, user_id, action, **kw):  # the real open, on a page whose main document is a 403
        _page, s = _open_page(403, {}, JOB)
        return await agent._act(SimpleNamespace(screen=_Screen()), s, action, url=kw["url"], ref="", text="",
                                on=True, doc_kind="", gate=None, files=None)

    monkeypatch.setattr(agent, "run", fake_run)
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        out = (await client.post("/api/browser", json={"action": "open", "url": JOB, "application_id": app_id})).json()
        assert out["blocked_recorded"] is True and out["transport"]["status"] == 403
        again = (await client.post("/api/browser", json={"action": "open", "url": JOB, "application_id": app_id})).json()
        assert again["blocked_recorded"] is False  # once per open block
        (row,) = await _blocked_rows(authenticated_async_context.fixture_user_id, app_id)
    assert row["reason"] == "bot_check" and row["by"] == "browser" and row["page_host"] == "jobs.acme.com"


@pytest.mark.asyncio
async def test_a_body_text_stop_never_records_bot_check(authenticated_async_context, monkeypatch):
    from src.core import settings
    from src.services.browser_agent import agent

    monkeypatch.setattr(settings, "BROWSER_AGENT_ENABLED", True)

    async def fake_run(p, user_id, action, **kw):  # 200, no headers; the page's text alone said "bot check"
        return {"status": "needs_you", "reason": "captcha", "detail": "x", "url": JOB,
                agent.STOP_KEY: outcome.TEXT_STOP, "transport": _t(200)}

    monkeypatch.setattr(agent, "run", fake_run)
    monkeypatch.setattr(agent, "hold_for_user", lambda *a, **k: False)
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        out = (await client.post("/api/browser", json={"action": "open", "url": JOB, "application_id": app_id})).json()
        assert agent.STOP_KEY not in out  # server-side only
        (row,) = await _blocked_rows(authenticated_async_context.fixture_user_id, app_id)
    assert row["reason"] == "captcha"  # the safety stop stands; never a bot_check


@pytest.mark.asyncio
async def test_a_200_open_records_nothing(authenticated_async_context, monkeypatch):
    from src.core import settings
    from src.services.browser_agent import agent

    monkeypatch.setattr(settings, "BROWSER_AGENT_ENABLED", True)

    async def fake_run(p, user_id, action, **kw):
        return {"status": "ok", "url": JOB, "transport": _t(200, {"server": "cloudflare"})}

    monkeypatch.setattr(agent, "run", fake_run)
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        out = (await client.post("/api/browser", json={"action": "open", "url": JOB, "application_id": app_id})).json()
        assert "blocked_recorded" not in out
        assert await _blocked_rows(authenticated_async_context.fixture_user_id, app_id) == []


async def _open_without_application(client_ctx, monkeypatch, transport, text="Welcome"):
    from src.core import settings
    from src.services.browser_agent import agent

    monkeypatch.setattr(settings, "BROWSER_AGENT_ENABLED", True)

    async def fake_run(p, user_id, action, **kw):
        return {"status": "ok", "url": JOB, "text": text, "transport": transport}

    monkeypatch.setattr(agent, "run", fake_run)
    async with client_ctx() as client:
        resp = await client.post("/api/browser", json={"action": "open", "url": JOB})
    return resp.json()


@pytest.mark.asyncio
async def test_an_open_with_no_application_on_a_403_is_needs_you_bot_check(authenticated_async_context, monkeypatch):
    from src.api.routes.browser import BOT_CHECK_DETAIL

    out = await _open_without_application(authenticated_async_context, monkeypatch, _t(403), "Access Denied")
    assert (out["status"], out["reason"], out["detail"]) == ("needs_you", "bot_check", BOT_CHECK_DETAIL)
    assert out["transport"]["status"] == 403 and out["text"] == "Access Denied"
    assert "blocked_recorded" not in out


@pytest.mark.asyncio
async def test_an_open_with_no_application_on_a_200_stays_ok(authenticated_async_context, monkeypatch):
    out = await _open_without_application(authenticated_async_context, monkeypatch, _t(200))
    assert out["status"] == "ok" and "reason" not in out


@pytest.mark.asyncio
async def test_a_200_page_whose_text_says_blocked_stays_ok_with_no_application(authenticated_async_context, monkeypatch):
    out = await _open_without_application(
        authenticated_async_context, monkeypatch, _t(200), "You have been blocked",
    )
    assert out["status"] == "ok" and "reason" not in out
