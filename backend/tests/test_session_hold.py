"""Weak spot #3 (2026-10-15): two sessions of ONE assistant on one application.

Seen live: a Claude Code cloud session and a desktop session worked the same
application at once. Both came through the same claude.ai connector, so both were
``agent:Claude`` and the kit's ``hold`` (which compared actors only) stayed null.
Each session now names itself (``session``); the hold compares (actor, session),
and check_submit asks ``held_by_other_session`` - a yes does not clear it.

Tests go through the real doors (HTTP + MCP) and assert VALUES (rule #21).
Helpers are copied, never imported from another test module.
"""
from __future__ import annotations

import contextlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.core import settings
from src.services.applications import kit as kit_service
from src.services.profile import assistant_settings as rules
from src.services.profile.models import AssistantSettings, CVData, UserPreferences, UserProfile

CV_TEXT = "Ada Lovelace\nSenior data engineer\nPython, dbt, Snowflake."
FORM = "https://careers.northwind.example/apply/7"
ACTOR = "token:claude-code"
_AD = {
    "title": "Data Engineer", "company": "Northwind", "location": "London", "country": "GB", "remote": False,
    "apply_url": "https://northwind.example/careers/7",
    "description": "Build the pipelines. Python, dbt, Snowflake.",
}


# ── helpers (copied, never imported across test modules) ─────────────────────


def _seed_profile(user_id: str) -> None:
    from src.services.profile.storage import save_assistant_settings, save_profile

    save_profile(
        UserProfile(
            cv_data=CVData(raw_text="Python data engineer.", name="Ada Lovelace", location="London, UK"),
            preferences=UserPreferences(target_job_titles=["Data Engineer"]),
        ),
        user_id, source_action="cv_upload",
    )
    save_assistant_settings(
        user_id, AssistantSettings(apply_min_score=50, above_line="auto", below_line="auto"), "test_seed"
    )


async def _mint_token(client: AsyncClient, name: str = "claude-code") -> str:
    resp = await client.post("/api/tokens", json={"name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()["token"]


def _bearer_client(token: str) -> AsyncClient:
    from src.api.main import app

    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    )


async def _setup(client: AsyncClient, user_id: str) -> tuple[int, str]:
    """A brought job with a fit and a CV, and a personal token (actor ``token:claude-code``)."""
    _seed_profile(user_id)
    resp = await client.post("/api/jobs/bring", json=_AD)
    assert resp.status_code == 200, resp.text
    app_id = int(resp.json()["application_id"])
    assert (await client.put(f"/api/applications/{app_id}/fit", json={"score": 80, "verdict": "ok", "score_source": "full_ad"})).status_code == 200
    saved = await client.post(f"/api/applications/{app_id}/artifacts", json={"kind": "cv", "text": CV_TEXT})
    assert saved.status_code == 201, saved.text
    return app_id, await _mint_token(client)


async def _kit(client: AsyncClient, app_id: int, session: str | None = None) -> dict[str, Any]:
    params = {} if session is None else {"session": session}
    resp = await client.get(f"/api/applications/{app_id}/kit", params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _event(client: AsyncClient, app_id: int, event_type: str, payload: dict[str, Any]) -> None:
    resp = await client.post(
        f"/api/applications/{app_id}/events", json={"event_type": event_type, "payload": payload}
    )
    assert resp.status_code == 201, resp.text


async def _check(client: AsyncClient, app_id: int, session: str | None = None) -> tuple[str, str, str]:
    params = {"form_url": FORM} if session is None else {"form_url": FORM, "session": session}
    resp = await client.get(f"/api/applications/{app_id}/submit-check", params=params)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    return body["decision"], body["reason"], body["detail"]


def _db():
    from src.api.dependencies import get_request_db

    return contextlib.asynccontextmanager(get_request_db)()


def _token_user(user_id: str):
    from src.api.auth_deps import CurrentUser

    return CurrentUser(id=user_id, email="e2e@example.com", auth_via="token", actor_name="claude-code",
                       email_verified=True)


def _kit_reads(user_id: str, app_id: int) -> list[dict[str, Any]]:
    from src.repositories import pgsync

    conn = pgsync.connect(str(settings.DB_PATH))
    try:
        rows = conn.execute(
            "SELECT recorded_by, payload FROM application_events WHERE user_id = ? AND application_id = ? "
            "AND event_type = 'kit_read' ORDER BY id",
            (user_id, app_id),
        ).fetchall()
    finally:
        conn.close()
    return [{"recorded_by": r[0], "payload": json.loads(r[1])} for r in rows]


# ═══════════════════════════════════════════════════════════════════════════
# The kit's hold
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_two_sessions_of_one_assistant_see_each_others_hold(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id, token = await _setup(client, fixture_user_id)
    async with _bearer_client(token) as agent:
        first = await _kit(agent, app_id, "a")
        assert first["hold"] is None, "nobody else was on it"
        second = await _kit(agent, app_id, "b")
    hold = second["hold"]
    assert hold["held_by"] == ACTOR and hold["session"] == "a" and hold["unsure"] is False
    assert hold["detail"] is None
    assert datetime.fromisoformat(hold["until"]) - datetime.fromisoformat(hold["since"]) == timedelta(
        minutes=settings.KIT_HOLD_MINUTES
    )
    reads = _kit_reads(fixture_user_id, app_id)
    assert [(r["recorded_by"], r["payload"]["session"]) for r in reads] == [(ACTOR, "a"), (ACTOR, "b")]
    # `since` is when session a's kit_read was recorded (just after its kit was generated)
    lag = datetime.fromisoformat(hold["since"]) - datetime.fromisoformat(first["kit"]["generated_at"])
    assert timedelta(0) <= lag < timedelta(seconds=30)


@pytest.mark.asyncio
async def test_the_same_session_twice_is_no_hold(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id, token = await _setup(client, fixture_user_id)
    async with _bearer_client(token) as agent:
        assert (await _kit(agent, app_id, "desktop-apply"))["hold"] is None
        assert (await _kit(agent, app_id, " desktop-apply "))["hold"] is None, "the label is stripped"
        assert (await _kit(agent, app_id, "desktop-apply"))["hold"] is None
    assert [r["payload"]["session"] for r in _kit_reads(fixture_user_id, app_id)] == ["desktop-apply"] * 3


@pytest.mark.asyncio
async def test_an_old_kit_read_without_a_session_key_is_session_blank(authenticated_async_context, fixture_user_id):
    from src.services.applications import spine

    async with authenticated_async_context() as client:
        app_id, token = await _setup(client, fixture_user_id)
    async with _db() as db:
        legacy = {"kit_sha256": "0" * 64, "cv_artifact_id": None, "cover_letter_artifact_id": None,
                  "links": [], "by": ACTOR}
        await spine.append_event(
            db, user_id=fixture_user_id, application_id=app_id, event_type="kit_read", payload=legacy,
            occurred_at=datetime.now(timezone.utc).isoformat(), recorded_by=ACTOR,
        )
    assert "session" not in _kit_reads(fixture_user_id, app_id)[0]["payload"]
    async with _bearer_client(token) as agent:
        kit = await _kit(agent, app_id)
        assert kit["hold"] is None, "same actor, both sessions blank: the same holder, unchanged"
        assert await _check_after_fill(agent, app_id, session=None) == ("ask", "submit_mode_confirm")
    assert _kit_reads(fixture_user_id, app_id)[1]["payload"]["session"] == ""


async def _check_after_fill(agent: AsyncClient, app_id: int, *, session: str | None) -> tuple[str, str]:
    await _event(agent, app_id, "form_filled", {"form_url": FORM, "fields_count": 1})
    await _event(agent, app_id, "form_swept", {"fields_checked": 1, "fixed": 0})
    decision, reason, _detail = await _check(agent, app_id, session)
    return decision, reason


@pytest.mark.asyncio
async def test_an_unlabelled_caller_after_a_labelled_read_is_warned_unsure(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        app_id, token = await _setup(client, fixture_user_id)
    async with _bearer_client(token) as agent:
        await _kit(agent, app_id, "cloud-run-360")
        hold = (await _kit(agent, app_id))["hold"]
        assert hold["held_by"] == ACTOR and hold["session"] == "cloud-run-360"
        assert hold["unsure"] is True
        assert hold["detail"] == (
            "another session of this assistant may be working on this application; "
            "pass `session` to tell sessions apart"
        )
        # unsure only warns: the gate does not ask held_by_other_session for it
        assert await _check_after_fill(agent, app_id, session=None) == ("ask", "submit_mode_confirm")


@pytest.mark.asyncio
async def test_hold_released_clears_it(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id, token = await _setup(client, fixture_user_id)
    async with _bearer_client(token) as agent:
        await _kit(agent, app_id, "a")
        assert (await _kit(agent, app_id, "b"))["hold"]["session"] == "a"
        await _event(agent, app_id, "hold_released", {"reason": "done"})
        assert (await _kit(agent, app_id, "b"))["hold"] is None, "a released it (any actor's release counts)"
        # b's own read is now the newest; a coming back sees b
        hold = (await _kit(agent, app_id, "a"))["hold"]
        assert hold["held_by"] == ACTOR and hold["session"] == "b"


@pytest.mark.asyncio
async def test_the_window_expiring_clears_it(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id, token = await _setup(client, fixture_user_id)
    async with _bearer_client(token) as agent:
        await _kit(agent, app_id, "a")
    user = _token_user(fixture_user_id)
    now = datetime.now(timezone.utc)
    async with _db() as db:
        inside = await kit_service.live_hold(db, user, app_id, now + timedelta(minutes=1), "b")
        assert inside is not None and inside["session"] == "a" and inside["unsure"] is False
        later = now + timedelta(minutes=settings.KIT_HOLD_MINUTES + 1)
        assert await kit_service.live_hold(db, user, app_id, later, "b") is None
        kit = await kit_service.build_kit(db, user, app_id, now=later, session="b")
        assert kit["hold"] is None, f"{settings.KIT_HOLD_MINUTES + 1} minutes later nobody is on it"
        assert kit["settings"]["submit_preview"]["reason"] != "held_by_other_session"


# ═══════════════════════════════════════════════════════════════════════════
# check_submit
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_check_submit_asks_held_by_other_session_even_with_a_yes(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id, token = await _setup(client, fixture_user_id)
    async with _bearer_client(token) as agent:
        kit_a = await _kit(agent, app_id, "a")
        kit_b = await _kit(agent, app_id, "b")
        assert kit_b["settings"]["submit_preview"]["reason"] == "held_by_other_session"
        cv = kit_b["cv"]
        await _event(agent, app_id, "submit_approved", {"artifact_id": cv["artifact_id"], "sha256": cv["sha256"],
                                                       "where": "chat"})
        await _event(agent, app_id, "form_filled", {"form_url": FORM, "fields_count": 1})
        await _event(agent, app_id, "form_swept", {"fields_checked": 1, "fixed": 0})
        decision, reason, detail = await _check(agent, app_id, "b")
        assert (decision, reason) == ("ask", "held_by_other_session"), "a yes does not override another session"
        since = kit_b["hold"]["since"]
        assert kit_b["hold"]["session"] == "a" and since >= kit_a["kit"]["generated_at"]
        assert detail == (
            f"Another assistant session ({ACTOR}/a) read this application's kit at {since}; "
            "make sure only one session submits."
        )
        # session a itself is not held by its own read (b read later, so a sees b's hold)
        assert (await _check(agent, app_id, "a"))[1] == "held_by_other_session"
        await _event(agent, app_id, "hold_released", {"reason": "done"})
        assert (await _check(agent, app_id, "b"))[:2] == ("submit", "user_approved"), "the previous decision"


@pytest.mark.asyncio
async def test_check_submit_ranks_every_stop_above_the_hold(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id, token = await _setup(client, fixture_user_id)
    async with _bearer_client(token) as agent:
        await _kit(agent, app_id, "a")
        await _kit(agent, app_id, "b")
        assert (await _check(agent, app_id, "b"))[:2] == ("fill", "form_not_recorded")


def test_the_gate_rule_is_pure_and_listed():
    cfg = rules.effective(AssistantSettings(apply_min_score=50, above_line="auto", below_line="auto"))
    base = dict(status="considering", has_receipt=False, submit_override=None, fit_score=80, form_filled=True,
                form_swept=True, form_host="careers.northwind.example", approved=True)
    counts = rules.SubmitCounts(submitted_today=0, applied_since_auto_on=1)
    now = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
    held = rules.SubmitFacts(**base, other_session_hold=("agent:Claude", "desktop-apply", "2026-10-15T11:50:00"))
    out = rules.may_submit(cfg, held, FORM, counts, now=now)
    assert (out.decision, out.reason) == ("ask", "held_by_other_session")
    assert "(agent:Claude/desktop-apply)" in out.detail and "2026-10-15T11:50:00" in out.detail
    blank = rules.SubmitFacts(**base, other_session_hold=("web", "", "2026-10-15T11:50:00"))
    assert "(web)" in rules.may_submit(cfg, blank, FORM, counts, now=now).detail
    assert rules.may_submit(cfg, rules.SubmitFacts(**base), FORM, counts, now=now).reason == "user_approved"
    paused = rules.effective(AssistantSettings(paused_until="2099-01-01T00:00:00+00:00"))
    assert rules.may_submit(paused, held, FORM, counts, now=now).decision == "stop"
    assert "held_by_other_session" in rules.REASONS
    assert kit_service.hold_fact({"held_by": "x", "session": "a", "since": "s", "unsure": True}) is None


# ═══════════════════════════════════════════════════════════════════════════
# The label itself
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_an_invalid_session_label_is_422(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id, token = await _setup(client, fixture_user_id)
    async with _bearer_client(token) as agent:
        for bad in ("<b>x</b>", "a" * 61, "tab\there", "naïve", "a/b"):
            kit = await agent.get(f"/api/applications/{app_id}/kit", params={"session": bad})
            assert kit.status_code == 422, bad
            assert "session must be a short label of at most 60 characters" in kit.json()["detail"]
            check = await agent.get(
                f"/api/applications/{app_id}/submit-check", params={"form_url": FORM, "session": bad}
            )
            assert check.status_code == 422, bad
        ok = await _kit(agent, app_id, "Cloud run_360: v1.2-a")
        assert ok["hold"] is None
    assert _kit_reads(fixture_user_id, app_id) == [
        {"recorded_by": ACTOR, "payload": {**_kit_reads(fixture_user_id, app_id)[0]["payload"],
                                           "session": "Cloud run_360: v1.2-a"}}
    ], "a refused label writes no kit_read"
    assert kit_service.clean_session("a" * 60) == "a" * 60


# ═══════════════════════════════════════════════════════════════════════════
# MCP: the tools pass `session` through
# ═══════════════════════════════════════════════════════════════════════════


def _mcp_client(token: str):
    import httpx as httpx2
    from mcp.client.client import Client
    from mcp.client.streamable_http import streamable_http_client

    from src.api.main import app

    http = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )
    return Client(streamable_http_client("http://test/api/mcp", http_client=http))


def _payload(result) -> dict:
    assert not result.is_error, result.content[0].text
    return json.loads(result.content[0].text)


@pytest.mark.asyncio
async def test_mcp_tools_pass_session_through(authenticated_async_context, fixture_user_id):
    pytest.importorskip("mcp")
    from src.api.mcp_server import mcp_runtime

    async with authenticated_async_context() as client:
        app_id, token = await _setup(client, fixture_user_id)
    async with _bearer_client(token) as agent:
        await _kit(agent, app_id, "cloud-run-360")
    async with mcp_runtime():
        async with _mcp_client(token) as mcp:
            kit = _payload(await mcp.call_tool("get_application_kit", {"application_id": app_id,
                                                                        "session": "desktop-apply"}))
            assert kit["hold"]["held_by"] == ACTOR and kit["hold"]["session"] == "cloud-run-360"
            assert kit["hold"]["unsure"] is False
            for event_type, payload in (("form_filled", {"form_url": FORM, "fields_count": 1}),
                                        ("form_swept", {"fields_checked": 1, "fixed": 0})):
                _payload(await mcp.call_tool("record_event", {"event_type": event_type, "application_id": app_id,
                                                              "payload": payload}))
            gate = _payload(await mcp.call_tool("check_submit", {"application_id": app_id, "form_url": FORM,
                                                                 "session": "desktop-apply"}))
            assert (gate["decision"], gate["reason"]) == ("ask", "held_by_other_session")
            bad = await mcp.call_tool("get_application_kit", {"application_id": app_id, "session": "<script>"})
            assert bad.is_error and "422" in bad.content[0].text
            bad = await mcp.call_tool("check_submit", {"application_id": app_id, "form_url": FORM,
                                                       "session": "x" * 61})
            assert bad.is_error and "422" in bad.content[0].text
    async with _bearer_client(token) as agent:
        route = await _check(agent, app_id, "desktop-apply")
    assert route[:2] == (gate["decision"], gate["reason"]) and route[2] == gate["detail"], "same answer as the route"
    assert _kit_reads(fixture_user_id, app_id)[-1]["payload"]["session"] == "desktop-apply"
