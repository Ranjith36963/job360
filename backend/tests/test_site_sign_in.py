"""SITE SIGN-IN (owner 2026-11-19): ``assistant_settings.site_sign_in`` lets the assistant sign in to a JOB SITE.

"Sign in is what you can do yourself. If you get an email code, use the Gmail connector and log into the job board.
You can create your own sign in and reset the password ... CAPTCHA is the only thing you can't do." Off by default; on
only by the user's OK (exactly like ``use_email_codes``). With it on, ``email_code_used`` takes the purposes
``sign_in`` / ``create_account`` / ``reset_password`` (403 while off; every EMAIL CODES rule still holds), ``site_account``
records how the assistant got in (``method``; never a password key), and a sign-in / sign-up wall on a job site opens
no owner ask: it is the assistant's PC task. Off = exactly as before. Real doors, real Postgres; fixtures only.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.api import dependencies as api_deps
from src.services.applications import blockers
from src.services.applications import email_codes as ec
from src.services.browser_agent import routing
from src.services.profile import assistant_settings as rules
from src.services.profile import schedule
from src.services.profile.models import AssistantSettings, CVData, UserProfile
from tests._pc_ready import pc_ready

SIGN_IN = "assistant_settings.site_sign_in"
FORM = "https://job-boards.greenhouse.io/northwind/jobs/7"
GH = "job-boards.greenhouse.io"
NOW = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)


def _seed(user_id: str) -> None:
    from src.services.profile.storage import save_profile

    save_profile(UserProfile(cv_data=CVData(raw_text="Ada Lovelace", name="Ada Lovelace")), user_id,
                 source_action="cv_upload")


def _agent(token: str) -> AsyncClient:
    from src.api.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"})


def _mcp_client(token: str):
    import httpx2
    from mcp.client import Client
    from mcp.client.streamable_http import streamable_http_client

    from src.api.main import app

    http = httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://test",
                              headers={"Authorization": f"Bearer {token}"})
    return Client(streamable_http_client("http://test/api/mcp", http_client=http))


async def _set(client: AsyncClient, on: bool) -> None:
    resp = await client.patch("/api/profile", json={"edits": [{"path": SIGN_IN, "value": on}]})
    assert resp.status_code == 200, resp.text
    assert (await client.get("/api/assistant-settings")).json()["site_sign_in"]["effective"] is on


async def _bring(client: AsyncClient, n: int = 1, url: str = FORM) -> int:
    ad = {"title": f"Engineer {n}", "company": f"Company{n}", "location": "London", "apply_url": url,
          "description": "Build the pipelines."}
    resp = await client.post("/api/jobs/bring", json=ad)
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _event(client: AsyncClient, app_id: int, event_type: str, payload: dict[str, Any]) -> dict:
    resp = await client.post(f"/api/applications/{app_id}/events", json={"event_type": event_type,
                                                                          "payload": payload})
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _wall(client: AsyncClient, app_id: int, reason: str = "login_needed", host: str = GH) -> None:
    await _event(client, app_id, "blocked", {"reason": reason, "step": "sign in", "page_host": host})


def _mail(purpose: str, **over: Any) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    base = {"to": "test@example.com", "sender": "Greenhouse <no-reply@greenhouse.io>",
            "subject": "Your sign-in code for Greenhouse", "purpose": purpose, "form_host": GH,
            "asked_at": (now - timedelta(minutes=2)).isoformat(), "received_at": (now - timedelta(minutes=1)).isoformat()}
    return {**base, **over}


# ── The setting ───────────────────────────────────────────────────────────────────────────────────────────────


def test_the_setting_is_off_by_default_and_turning_it_on_is_riskier():
    assert AssistantSettings().site_sign_in is False
    assert rules.effective(AssistantSettings()).site_sign_in is False
    assert SIGN_IN in rules.SETTING_PATHS and SIGN_IN in rules.GATED_PATHS
    assert rules.classify_change(SIGN_IN, False, True) == "riskier"
    assert rules.classify_change(SIGN_IN, True, False) == "safer"
    assert rules.effective_path_value(SIGN_IN, None) is False


@pytest.mark.asyncio
async def test_only_the_user_turns_it_on_and_get_profile_shows_it(authenticated_async_context, fixture_user_id):
    from src.api.mcp_server import mcp_runtime

    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        token = (await client.post("/api/tokens", json={"name": "claude-code"})).json()["token"]
        async with _agent(token) as agent:  # an assistant's "on" waits for the user's OK
            held = await agent.patch("/api/profile", json={"edits": [{"path": SIGN_IN, "value": True}]})
            assert held.status_code == 200, held.text
        view = (await client.get("/api/assistant-settings")).json()
        assert view["site_sign_in"] == {"value": False, "effective": False}
        assert [w["path"] for w in view["waiting"]] == [SIGN_IN]
        await _set(client, True)  # the user's own web click applies it
    async with mcp_runtime():
        async with _mcp_client(token) as mcp:
            result = await mcp.call_tool("get_profile", {"view": "full"})
            assert not result.is_error, result.content[0].text
            settings_view = json.loads(result.content[0].text)["settings"]
            assert settings_view["site_sign_in"] == {"value": True, "effective": True}


# ── email_code_used: the sign-in purposes ─────────────────────────────────────────────────────────────────────


def _problems(payload: dict[str, Any], sign_in: bool = True) -> list[str]:
    return ec.problems(payload, own_emails={"test@example.com"}, known_hosts={GH, "uk.indeed.com"}, now=NOW,
                       sign_in=sign_in)


def _pure(purpose: str, **over: Any) -> dict[str, Any]:
    return {**_mail(purpose), "asked_at": (NOW - timedelta(minutes=3)).isoformat(),
            "received_at": (NOW - timedelta(minutes=2)).isoformat(), **over}


@pytest.mark.parametrize("purpose", sorted(ec.SIGN_IN_PURPOSES))
def test_sign_in_purposes_pass_only_with_the_setting(purpose: str):
    assert _problems(_pure(purpose)) == []
    assert "purpose_not_allowed" in _problems(_pure(purpose), sign_in=False)


def test_a_job_sites_own_reset_email_passes_but_bank_words_and_other_purposes_do_not():
    assert _problems(_pure("reset_password", subject="Reset your Greenhouse password")) == []
    assert "not_an_application_code" in _problems(_pure("sign_in", subject="Reset your password"))
    assert "not_an_application_code" in _problems(_pure("reset_password", subject="Reset your bank card password"))
    # the old purposes keep the old words rule, whatever the setting
    assert "not_an_application_code" in _problems(_pure("apply", subject="Reset your password"))


def test_indeed_is_never_a_sign_in_site():
    found = _problems(_pure("sign_in", form_host="uk.indeed.com", sender="Indeed <no-reply@indeed.com>"))
    assert "not_a_job_site" in found
    assert ec.sign_in_site(GH) and ec.sign_in_site("careers.acme.co.uk")
    for host in ("uk.indeed.com", "indeed.co.uk", "accounts.google.com", "www.linkedin.com", "login.live.com", ""):
        assert not ec.sign_in_site(host), host


@pytest.mark.asyncio
async def test_the_route_refuses_while_off_and_checks_every_rule_when_on(
    authenticated_async_context, fixture_user_id, caplog,
):
    caplog.set_level(logging.INFO)
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        app_id = await _bring(client)
        url = f"/api/applications/{app_id}/events"
        token = (await client.post("/api/tokens", json={"name": "claude-code"})).json()["token"]
        async with _agent(token) as agent:
            for purpose in sorted(ec.SIGN_IN_PURPOSES):
                off = await agent.post(url, json={"event_type": "email_code_used", "payload": _mail(purpose)})
                assert off.status_code == 403 and "site_sign_in is off" in off.text, off.text
        await _set(client, True)
        async with _agent(token) as agent:
            for sender in ("no-reply@accounts.google.com", "security-noreply@linkedin.com",
                           "account-security-noreply@accountprotection.microsoft.com"):
                bad = await agent.post(url, json={"event_type": "email_code_used",
                                                  "payload": _mail("sign_in", sender=sender)})
                assert bad.status_code == 422 and "sender_forbidden" in bad.text, (sender, bad.text)
            other = await agent.post(url, json={"event_type": "email_code_used",
                                                "payload": _mail("sign_in", form_host="jobs.other-site.example")})
            assert other.status_code == 422 and "form_host_unknown" in other.text, other.text
            # use_email_codes stays off: an apply code is still refused, the setting is its own
            apply_code = await agent.post(url, json={"event_type": "email_code_used", "payload": _mail("apply")})
            assert apply_code.status_code == 403 and "use_email_codes is off" in apply_code.text
            reset = _mail("reset_password", subject="Reset your password for Greenhouse")
            ok = await agent.post(url, json={"event_type": "email_code_used", "payload": reset})
            assert ok.status_code == 201, ok.text
        rows = (await client.get(f"/api/applications/{app_id}")).json()["events"]
        stored = next(e for e in rows if e["event_type"] == "email_code_used")
        assert stored["payload"] == {"host": GH, "sender_host": "greenhouse.io",
                                     "received_at": reset["received_at"], "purpose": "reset_password"}
    checks = [r for r in caplog.records if getattr(r, "event", "") == "email_code_check"]
    assert {getattr(r, "purpose", "") for r in checks} >= {"sign_in", "reset_password", "create_account"}
    assert any(getattr(r, "reasons", "") == "site_sign_in_off" for r in checks)
    assert any(getattr(r, "result", "") == "ok" and getattr(r, "purpose", "") == "reset_password" for r in checks)
    logged = " ".join(str(r.__dict__) for r in checks)
    assert "Reset your password" not in logged and "test@example.com" not in logged  # never the subject or address


# ── site_account {host, method} ───────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_site_account_takes_a_method_and_never_a_password(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        app_id = await _bring(client)
        url = f"/api/applications/{app_id}/events"
        token = (await client.post("/api/tokens", json={"name": "claude-code"})).json()["token"]
        async with _agent(token) as agent:
            ok = await agent.post(url, json={"event_type": "site_account",
                                             "payload": {"host": GH, "method": "reset_link"}})
            assert ok.status_code == 201, ok.text
            plain = await agent.post(url, json={"event_type": "site_account", "payload": {"host": GH}})
            assert plain.status_code == 201, plain.text  # method stays optional
            for bad in ({"password": "x"}, {"new_password": "x"}, {"Passwd": "x"}, {"client_secret": "x"}):
                refused = await agent.post(url, json={"event_type": "site_account",
                                                      "payload": {"host": GH, "method": "new_account", **bad}})
                assert refused.status_code == 422 and "never carries a password" in refused.text, refused.text
                assert '"x"' not in refused.text
            odd = await agent.post(url, json={"event_type": "site_account",
                                              "payload": {"host": GH, "method": "typed_my_password"}})
            assert odd.status_code == 422 and "method must be one of" in odd.text
            needed = await agent.post(url, json={"event_type": "account_needed",
                                                 "payload": {"host": GH, "method": "new_account"}})
            assert needed.status_code == 422  # account_needed keeps its one key
        rows = (await client.get(f"/api/applications/{app_id}")).json()["events"]
        stored = [e["payload"] for e in rows if e["event_type"] == "site_account"]
        assert {"host": GH, "method": "reset_link", "by": "token:claude-code"} in stored
        assert all(not any("pass" in k for k in p) for p in stored)


# ── The wall: the owner's ask when off, the assistant's PC task when on ───────────────────────────────────────


async def _blockers(user_id: str, ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    db = await api_deps.get_db()
    return await blockers.blockers_for(db, user_id, ids)  # type: ignore[return-value]


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["login_needed", "account_creation"])
async def test_off_a_sign_in_wall_still_asks_the_owner(authenticated_async_context, reason: str):
    async with authenticated_async_context() as client:
        uid = authenticated_async_context.fixture_user_id
        _seed(uid)
        app_id = await _bring(client)
        await _wall(client, app_id, reason)
        [ask] = (await client.get("/api/asks")).json()["asks"]
        assert ask["kind"] == "login" and ask["browser"]["kind"] == "human"
        first = (await _blockers(uid, [app_id]))[app_id][0]
        assert (first["kind"], first["actor"]) == ("site_step", "you")
        db = await api_deps.get_db()
        assert await schedule._waiting_on_you(db, uid, [app_id]) == 1  # noqa: SLF001


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["login_needed", "account_creation"])
async def test_on_a_job_sites_wall_opens_no_ask_and_goes_to_the_pc(authenticated_async_context, reason: str, caplog):
    caplog.set_level(logging.INFO)
    async with authenticated_async_context() as client:
        uid = authenticated_async_context.fixture_user_id
        _seed(uid)
        await _set(client, True)
        app_id = await _bring(client)
        await _wall(client, app_id, reason)
        assert (await client.get("/api/asks")).json() == {"asks": [], "open_count": 0}
        first = (await _blockers(uid, [app_id]))[app_id][0]
        assert (first["kind"], first["actor"]) == ("assistant_step", "assistant")
        assert "SITE SIGN-IN" in first["label"]
        db = await api_deps.get_db()
        assert await schedule._waiting_on_you(db, uid, [app_id]) == 0  # noqa: SLF001
        await _set(client, False)  # off hands it back at once
        first = (await _blockers(uid, [app_id]))[app_id][0]
        assert (first["kind"], first["actor"]) == ("site_step", "you")
        await _set(client, True)
        await pc_ready(client, app_id)  # only a row check_submit can pass is a PC task
        queue = (await client.get("/api/own-browser/queue")).json()
        assert queue["count"] == 1 and queue["tasks"][0]["browser"]["reason"] == "site_sign_in"
    assert any(getattr(r, "event", "") == "signin_block_assistant" for r in caplog.records)


@pytest.mark.asyncio
async def test_on_indeed_or_linkedin_walls_stay_the_owners(authenticated_async_context):
    async with authenticated_async_context() as client:
        uid = authenticated_async_context.fixture_user_id
        _seed(uid)
        await _set(client, True)
        indeed = await _bring(client, 1, "https://uk.indeed.com/viewjob?jk=abc123")
        await _wall(client, indeed, host="uk.indeed.com")
        no_host = await _bring(client, 2, "https://co2.example/apply/2")
        await _event(client, no_host, "blocked", {"reason": "login_needed", "step": "sign in"})
        asks = (await client.get("/api/asks")).json()["asks"]
        assert {a["application_id"] for a in asks} == {indeed, no_host}


@pytest.mark.asyncio
async def test_turning_it_on_withdraws_the_open_sign_in_ask(authenticated_async_context):
    async with authenticated_async_context() as client:
        uid = authenticated_async_context.fixture_user_id
        _seed(uid)
        app_id = await _bring(client)
        await _wall(client, app_id)
        assert (await client.get("/api/asks")).json()["open_count"] == 1
        await _set(client, True)
        assert (await client.get("/api/asks")).json()["open_count"] == 0
        everything = (await client.get("/api/asks", params={"status": "all"})).json()["asks"]
        assert everything[0]["withdrawn_reason"] == "Job360: the assistant signs in on this site with your email"


@pytest.mark.asyncio
async def test_two_walls_without_a_site_account_hand_the_job_back(authenticated_async_context):
    async with authenticated_async_context() as client:
        uid = authenticated_async_context.fixture_user_id
        _seed(uid)
        await _set(client, True)
        app_id = await _bring(client)
        await _wall(client, app_id)
        await _event(client, app_id, "unblocked", {"resolution": "retried"})
        await _wall(client, app_id)  # no site_account between: the owner's again
        [ask] = (await client.get("/api/asks")).json()["asks"]
        assert ask["application_id"] == app_id
        other = await _bring(client, 2, "https://job-boards.greenhouse.io/southwind/jobs/9")
        await _wall(client, other)
        await _event(client, other, "site_account", {"host": GH, "method": "magic_link"})
        await _event(client, other, "unblocked", {"resolution": "retried"})
        await _wall(client, other)  # a site_account between: still the assistant's
        assert (await _blockers(uid, [other]))[other][0]["actor"] == "assistant"


def test_ask_route_sends_a_sign_in_wall_to_the_pc_only_with_the_flag():
    on = routing.ask_route("login", block_reason="login_needed", site_sign_in=True)
    assert (on.kind, on.reason) == ("own_browser", "site_sign_in")
    assert routing.ask_route("login", block_reason="login_needed").reason == "password"
    assert routing.ask_route("login", block_reason="otp_needed", site_sign_in=True).reason == "mfa"
    assert "site_sign_in" in routing.ROUTE_REASONS and "site_sign_in" in routing.ROUTE_LABELS


def test_the_recipes_carry_site_sign_in_and_the_new_hard_line():
    from src.api.mcp_server import instructions
    from src.api.routes.recipes import load_recipe

    rules_text = " ".join(load_recipe("rules").text.split())
    assert "SITE SIGN-IN (job sites only)" in rules_text
    for words in ("at least 16 characters", "never store or recall a password", "purpose sign_in | create_account |",
                  "email_code | magic_link | reset_link | new_account", "never Google, Microsoft, Apple, LinkedIn, Indeed"):
        assert words in rules_text, words
    flat = " ".join(instructions().lower().split())
    assert "never ask for, store or reuse a password" in flat and "site sign-in" in flat
    for name in ("own_browser", "batch"):
        text = load_recipe(name).text
        assert "never ask for, store or reuse a password" in " ".join(text.lower().split()), name
        assert len(text.splitlines()) <= 70, name
