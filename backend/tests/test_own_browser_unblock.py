"""Owner 2026-11-03 (prod, application 116): Job360's browser recorded a CAPTCHA, the job went to the user's own
browser, the form filled there, and check_submit answered ask/blocked forever: `retried` is refused for a CAPTCHA
and no other resolution fitted. `unblocked` {resolution: own_browser} = the own browser loaded the page clean. It
clears only a block Job360's browser wrote (any reason), is stamped `browser: own_browser`, and never opens the
gate alone: every other check_submit reason still applies. Real Postgres through the routes and MCP. Helpers are
copied, never imported.
"""
from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timezone
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.core import settings
from src.services.applications import blocked as blocked_service
from src.services.profile import assistant_settings as rules
from src.services.profile.models import AssistantSettings, CVData, UserPreferences, UserProfile

CV_TEXT = "Ada Lovelace\nSenior data engineer\nPython, dbt, Snowflake."
FORM = "https://careers.northwind.example/apply/7"
NOTICE = {"question": "Preferred start date", "answer": "1 month", "source": "profile"}


# ── helpers (copied, never imported across test modules) ─────────────────────


def _seed_profile(user_id: str) -> None:
    from src.services.profile.storage import save_assistant_settings, save_profile

    save_profile(
        UserProfile(cv_data=CVData(raw_text="Python data engineer: dbt, Snowflake.", name="Ada Lovelace"),
                    preferences=UserPreferences(target_job_titles=["Data Engineer"])),
        user_id, source_action="cv_upload",
    )
    save_assistant_settings(
        user_id, AssistantSettings(apply_min_score=50, above_line="auto", below_line="auto"), "test_seed"
    )


def _events(app_id: int, event_type: str) -> list[dict[str, Any]]:
    from src.repositories import pgsync

    conn = pgsync.connect(str(settings.DB_PATH))
    try:
        rows = conn.execute(
            "SELECT id, payload FROM application_events WHERE application_id = ? AND event_type = ? ORDER BY id",
            (app_id, event_type),
        ).fetchall()
    finally:
        conn.close()
    return [{"id": int(r[0]), **json.loads(r[1] or "{}")} for r in rows]


def _mcp_client(token: str):
    import httpx2
    from mcp.client import Client
    from mcp.client.streamable_http import streamable_http_client

    from src.api.main import app

    http = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )
    return Client(streamable_http_client("http://test/api/mcp", http_client=http))


def _bearer(token: str) -> AsyncClient:
    from src.api.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"})


async def _token(client: AsyncClient) -> str:
    resp = await client.post("/api/tokens", json={"name": "claude-code"})
    assert resp.status_code == 201, resp.text
    return resp.json()["token"]


async def _event(client: AsyncClient, app_id: int, event_type: str, payload: dict[str, Any]):
    return await client.post(f"/api/applications/{app_id}/events", json={"event_type": event_type, "payload": payload})


async def _app(client: AsyncClient, user_id: str, n: int, *, approve: bool = True) -> int:
    """A brought job with a CV, a filled + swept form and (``approve``) the user's web Send yes."""
    _seed_profile(user_id)
    resp = await client.post("/api/jobs/bring", json={
        "title": f"Data Engineer {n}", "company": "Northwind", "location": "London", "country": "GB",
        "remote": False, "apply_url": f"https://northwind.example/careers/{n}", "description": "Pipelines. Python.",
    })
    assert resp.status_code == 200, resp.text
    app_id = int(resp.json()["application_id"])
    fit = {"score": 80, "verdict": "ok", "score_source": "full_ad"}
    assert (await client.put(f"/api/applications/{app_id}/fit", json=fit)).status_code == 200
    resp = await client.post(f"/api/applications/{app_id}/artifacts", json={"kind": "cv", "text": CV_TEXT})
    assert resp.status_code == 201, resp.text
    fill = await _event(client, app_id, "form_filled", {"form_url": FORM, "fields_count": 1, "answers": [NOTICE]})
    assert fill.status_code == 201, fill.text
    sweep = await _event(client, app_id, "form_swept", {"fields_checked": 1, "fixed": 0, "answers": [NOTICE]})
    assert sweep.status_code == 201, sweep.text
    if approve:
        yes = await client.post(
            f"/api/applications/{app_id}/send/approve", params={"form_filled_event_id": fill.json()["event_id"]}
        )
        assert yes.status_code == 201, yes.text
    return app_id


async def _job360_block(user_id: str, app_id: int, reason: str) -> None:
    """Job360's own browser stops on the page (``record_browser_block``: ``by: browser``, ``browser: job360``)."""
    from src.api.auth_deps import CurrentUser
    from src.api.routes import browser as browser_routes

    user = CurrentUser(id=user_id, email="u@example.com")
    async with browser_routes._db() as db:  # noqa: SLF001
        await blocked_service.record_browser_block(
            db, user, app_id, {"reason": reason, "step": "Job360 browser", "page_host": "careers.northwind.example"}
        )


async def _check(client: AsyncClient, app_id: int) -> dict[str, Any]:
    resp = await client.get(f"/api/applications/{app_id}/submit-check", params={"form_url": FORM})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _open_asks(client: AsyncClient, app_id: int) -> list[dict[str, Any]]:
    resp = await client.get("/api/asks", params={"status": "open"})
    assert resp.status_code == 200, resp.text
    return [a for a in resp.json()["asks"] if a["application_id"] == app_id]


def _payload(result) -> dict:
    assert not result.is_error, result.content[0].text
    return json.loads(result.content[0].text)


def _error(result) -> str:
    assert result.is_error, "expected a tool error"
    return result.content[0].text


# ── the fix: the own browser clears Job360's block, then check_submit decides ──


@pytest.mark.asyncio
async def test_own_browser_clears_job360s_captcha_over_mcp_and_check_submit_then_submits(
    authenticated_async_context, fixture_user_id,
):
    from src.api.mcp_server import mcp_runtime

    async with authenticated_async_context() as web:
        app_id = await _app(web, fixture_user_id, 1)
        token = await _token(web)
    await _job360_block(fixture_user_id, app_id, "captcha")
    async with mcp_runtime():
        async with _mcp_client(token) as mcp:
            check = {"application_id": app_id, "form_url": FORM}
            assert (_payload(await mcp.call_tool("check_submit", check))["reason"]) == "blocked"
            # `retried` stays refused for a CAPTCHA (the old dead end)
            refused = _error(await mcp.call_tool("record_event", {
                "event_type": "unblocked", "application_id": app_id, "payload": {"resolution": "retried"},
            }))
            assert "never retried" in refused
            _payload(await mcp.call_tool("record_event", {
                "event_type": "unblocked", "application_id": app_id,
                "payload": {"resolution": "own_browser", "note": "loaded clean in the own browser"},
            }))
            verdict = _payload(await mcp.call_tool("check_submit", check))
            assert verdict["decision"] == "submit", verdict
    [unblocked] = _events(app_id, "unblocked")
    assert unblocked["resolution"] == "own_browser" and unblocked["browser"] == "own_browser"
    assert unblocked["assistant"] == "token:claude-code" and unblocked["note"] == "loaded clean in the own browser"
    async with authenticated_async_context() as web:
        assert await _open_asks(web, app_id) == []  # the CAPTCHA's Needs-you ask closed with it


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", sorted(blocked_service.OWN_BROWSER_CLEARS))
async def test_any_job360_block_clears_but_the_gate_stays_shut_without_the_owners_yes(
    authenticated_async_context, fixture_user_id, reason,
):
    """Every getting-in block Job360's browser can write is cleared by own_browser (CAPTCHA and sign-in included),
    yet with no yes from the user check_submit never answers `submit`."""
    async with authenticated_async_context() as web:
        app_id = await _app(web, fixture_user_id, 2, approve=False)
        token = await _token(web)
    await _job360_block(fixture_user_id, app_id, reason)
    async with _bearer(token) as agent:
        assert (await _check(agent, app_id))["reason"] == "blocked"
        resp = await _event(agent, app_id, "unblocked", {"resolution": "own_browser"})
        assert resp.status_code == 201, resp.text
        verdict = await _check(agent, app_id)
        assert verdict["decision"] != "submit" and verdict["reason"] != "blocked", verdict


def _passing() -> tuple[rules.EffectiveSettings, rules.SubmitFacts, rules.SubmitCounts, datetime]:
    cfg = rules.effective(rules.AssistantSettings(submit_mode="auto_when_sure"))
    counts = rules.SubmitCounts(submitted_today=0, applied_since_auto_on=3)
    facts = rules.SubmitFacts(status="considering", has_receipt=False, submit_override=None,
                              cv_seen=True, approved=True, blocked=False, form_filled=True, form_swept=True,
                              form_host="careers.northwind.example")
    return cfg, facts, counts, datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)


# One fact per gate, each set the way that must keep Submit shut. `blocked` is False in every case: that is all an
# own_browser unblock changes.
_SHUT = [
    {"approved": False, "cv_seen": False}, {"approved": False}, {"declined": True}, {"status": "applied"},
    {"has_receipt": True}, {"form_filled": False}, {"form_swept": False}, {"duplicate_job": True},
    {"email_verified": False}, {"required_unanswered": True}, {"form_count_short": True},
    {"unreadable_fields": True}, {"form_check_missing": True}, {"form_host": "evil.example"},
    {"other_session_hold": ("chatgpt", "s2", "2026-11-03T11:55:00+00:00")}, {"submit_unconfirmed": True},
    {"company_avoided": True}, {"form_pages_incomplete": True}, {"guessed_unverified": True},
    {"written_unapproved": True}, {"sensitive_unconfirmed": True}, {"answer_blank": ("Notice period",)},
    {"answer_placeholder": ("Why us?",)}, {"approved": False, "approval_stale": "cv"},
    {"open_asks": ("question",)}, {"approved": False, "submit_override": "confirm"},
]


def test_the_pure_gate_passes_only_when_every_other_fact_holds():
    cfg, facts, counts, now = _passing()
    assert rules.may_submit(cfg, facts, FORM, counts, now=now).decision == "submit"
    assert rules.may_submit(cfg, dataclasses.replace(facts, blocked=True), FORM, counts, now=now).reason == "blocked"


@pytest.mark.parametrize("change", _SHUT, ids=lambda c: ",".join(c))
def test_an_own_browser_unblock_never_opens_any_other_gate(change):
    cfg, facts, counts, now = _passing()
    verdict = rules.may_submit(cfg, dataclasses.replace(facts, blocked=False, **change), FORM, counts, now=now)
    assert verdict.decision != "submit", (change, verdict)
    assert verdict.reason != "blocked"


# ── refusals: only Job360's own block, closed payload ─────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", [{}, {"browser": "own_browser"}, {"browser": "manual"}])
async def test_a_block_job360s_browser_did_not_write_is_422_and_stays_open(
    authenticated_async_context, fixture_user_id, extra,
):
    async with authenticated_async_context() as web:
        app_id = await _app(web, fixture_user_id, 3)
        token = await _token(web)
    async with _bearer(token) as agent:
        block = {"reason": "captcha", "step": "submit", "page_host": "careers.northwind.example", **extra}
        assert (await _event(agent, app_id, "blocked", block)).status_code == 201
        resp = await _event(agent, app_id, "unblocked", {"resolution": "own_browser"})
        assert resp.status_code == 422 and "only a block Job360's browser recorded" in resp.text, resp.text
        assert (await _check(agent, app_id))["reason"] == "blocked"
        assert _events(app_id, "unblocked") == []
        assert len(await _open_asks(agent, app_id)) == 1


@pytest.mark.asyncio
async def test_own_browser_with_nothing_blocked_is_409_and_the_payload_stays_closed(
    authenticated_async_context, fixture_user_id,
):
    async with authenticated_async_context() as web:
        app_id = await _app(web, fixture_user_id, 4)
        token = await _token(web)
    async with _bearer(token) as agent:
        assert (await _event(agent, app_id, "unblocked", {"resolution": "own_browser"})).status_code == 409
    await _job360_block(fixture_user_id, app_id, "login_needed")
    async with _bearer(token) as agent:
        # the server stamps the browser; a caller's key is outside the closed set
        bad = await _event(agent, app_id, "unblocked", {"resolution": "own_browser", "browser": "job360"})
        assert bad.status_code == 422, bad.text
        assert (await _check(agent, app_id))["reason"] == "blocked"


def test_the_resolution_is_listed_everywhere_the_agent_reads():
    from src.api.routes.recipes import load_recipe

    assert "own_browser" in blocked_service.UNBLOCK_RESOLUTIONS
    for name in ("own_browser", "rules"):
        flat = " ".join(load_recipe(name).text.split())
        assert '`unblocked` {resolution: "own_browser"}' in flat, name
        assert "no CAPTCHA, no sign-in wall" in flat, name


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", sorted(set(blocked_service.BLOCKED_REASONS) - blocked_service.OWN_BROWSER_CLEARS))
async def test_what_the_site_said_is_never_cleared_by_a_clean_load(authenticated_async_context, fixture_user_id, reason):
    """Already applied, ID or payment asked, a closed posting...: a clean load elsewhere changes none of it."""
    async with authenticated_async_context() as web:
        app_id = await _app(web, fixture_user_id, 2, approve=False)
        token = await _token(web)
    await _job360_block(fixture_user_id, app_id, reason)
    async with _bearer(token) as agent:
        resp = await _event(agent, app_id, "unblocked", {"resolution": "own_browser"})
        assert resp.status_code == 422, resp.text
        assert (await _check(agent, app_id))["reason"] == "blocked"
