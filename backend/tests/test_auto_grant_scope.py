"""Gap roadmap slice 30: an "Auto for a while" grant covers only what the user reviewed.

When the user confirms a counted auto grant on the web, the server snapshots their batch queue
(``queued`` / ``unqueued``) with each application's form host (``grant_scope.py``). check_submit
resolves auto only for an application in that snapshot whose form host is still the snapshotted one;
anything else keeps the user's own mode and asks ``grant_scope``. A grant from before snapshots stays
unscoped until its window ends. Real HTTP + MCP doors, VALUE asserts (rule #21). Helpers are copied,
never imported from another test module.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.core import settings
from src.services.profile import assistant_settings as rules
from src.services.profile import grant_scope
from src.services.profile.models import AssistantSettings, CVData, UserPreferences, UserProfile

FORM = "https://careers.northwind.example/apply/7"
FORM_HOST = "careers.northwind.example"
OTHER_FORM = "https://boards.otherats.example/northwind/7"
GRANT_N = "assistant_settings.auto_grant_count"
GRANT_UNTIL = "assistant_settings.auto_grant_until"
NOW = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)


def _ad(n: int) -> dict[str, Any]:
    return {
        "title": f"Data Engineer {n}", "company": f"Northwind {n}", "location": "Remote",
        "apply_url": f"https://northwind.example/careers/{n}",
        "description": "Build the pipelines. Python, dbt, Snowflake. Fully remote.",
    }


def _seed_profile(user_id: str) -> None:
    from src.services.profile.storage import save_assistant_settings, save_profile

    save_profile(
        UserProfile(cv_data=CVData(raw_text="Python data engineer.", name="Ada Lovelace"),
                    preferences=UserPreferences(target_job_titles=["Data Engineer"])),
        user_id, source_action="cv_upload",
    )
    save_assistant_settings(
        user_id, AssistantSettings(apply_min_score=50, above_line="auto", below_line="auto"), "test_seed"
    )


def _sql(statement: str, params: tuple = ()) -> None:
    from src.repositories import pgsync

    conn = pgsync.connect(str(settings.DB_PATH))
    try:
        conn.execute(statement, params)
        conn.commit()
    finally:
        conn.close()


async def _event(client: AsyncClient, app_id: int, event_type: str, payload: dict[str, Any] | None = None) -> None:
    body: dict[str, Any] = {"event_type": event_type}
    if payload is not None:
        body["payload"] = payload
    resp = await client.post(f"/api/applications/{app_id}/events", json=body)
    assert resp.status_code == 201, resp.text


async def _bring(client: AsyncClient, n: int) -> int:
    resp = await client.post("/api/jobs/bring", json=_ad(n))
    assert resp.status_code == 200, resp.text
    app_id = int(resp.json()["application_id"])
    fit = {"score": 80, "verdict": "ok", "score_source": "full_ad"}
    assert (await client.put(f"/api/applications/{app_id}/fit", json=fit)).status_code == 200
    return app_id


async def _fill(client: AsyncClient, app_id: int, url: str = FORM) -> None:
    await _event(client, app_id, "form_filled", {"form_url": url, "fields_count": 1})
    await _event(client, app_id, "form_swept", {"fields_checked": 1, "fixed": 0})


async def _check(client: AsyncClient, app_id: int, url: str = FORM) -> dict[str, Any]:
    resp = await client.get(f"/api/applications/{app_id}/submit-check", params={"form_url": url})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _grant(client: AsyncClient, count: int = 5) -> None:
    until = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
    resp = await client.patch("/api/profile", json={"edits": [
        {"path": GRANT_N, "value": count}, {"path": GRANT_UNTIL, "value": until},
    ]})
    assert resp.status_code == 200, resp.text


async def _token(client: AsyncClient) -> str:
    resp = await client.post("/api/tokens", json={"name": "claude-code"})
    assert resp.status_code == 201, resp.text
    return resp.json()["token"]


def _bearer_client(token: str) -> AsyncClient:
    from src.api.main import app

    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    )


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


# Under an auto grant with no CV saved, the first auto-mode ask is `cv_not_seen`: proof the mode
# resolved to auto. Outside the grant the user's own `confirm` asks `grant_scope` instead.
AUTO = ("ask", "cv_not_seen")


def _pair(body: dict[str, Any]) -> tuple[str, str]:
    return body["decision"], body["reason"]


# ═══════════════════════════════════════════════════════════════════════════
# Real doors
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_a_grant_covers_the_queue_it_was_given_and_not_one_queued_after(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        first, second, late = await _bring(client, 1), await _bring(client, 2), await _bring(client, 3)
        for app_id in (first, second, late):
            await _fill(client, app_id)
        for app_id in (first, second):
            await _event(client, app_id, "queued")
        before = (await client.get("/api/assistant-settings")).json()["auto_grant_scope"]
        assert before == {"queue": {"count": 2, "hosts": [FORM_HOST], "no_host": 0}, "granted": None, "unscoped": False}
        await _grant(client)
        assert _pair(await _check(client, first)) == AUTO
        assert _pair(await _check(client, second)) == AUTO
        await _event(client, late, "queued")  # queued AFTER the user said yes
        body = await _check(client, late)
        assert _pair(body) == ("ask", "grant_scope"), body
        assert "the 2 applications" in body["detail"] and "not among them" in body["detail"]
        view = (await client.get("/api/assistant-settings")).json()["auto_grant_scope"]
        assert view["granted"] == {"count": 2, "hosts": [FORM_HOST], "no_host": 0}
        assert view["queue"]["count"] == 3 and view["unscoped"] is False
        # "Stop now" ends the grant for everyone: back to the user's own confirm
        resp = await client.patch("/api/profile", json={"edits": [
            {"path": GRANT_N, "value": None}, {"path": GRANT_UNTIL, "value": None},
        ]})
        assert resp.status_code == 200, resp.text
        assert _pair(await _check(client, first)) == ("ask", "submit_mode_confirm")


@pytest.mark.asyncio
async def test_a_covered_application_whose_form_moved_site_is_asked(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        filled, unfilled = await _bring(client, 1), await _bring(client, 4)
        await _fill(client, filled)
        await _event(client, filled, "queued")
        await _event(client, unfilled, "queued")  # no form yet: its apply link's host is snapshotted
        await _grant(client)
        view = (await client.get("/api/assistant-settings")).json()["auto_grant_scope"]
        assert view["granted"] == {"count": 2, "hosts": ["careers.northwind.example", "northwind.example"], "no_host": 0}
        assert _pair(await _check(client, filled)) == AUTO
        await _fill(client, filled, OTHER_FORM)  # the form is now on another site
        moved = await _check(client, filled, OTHER_FORM)
        assert _pair(moved) == ("ask", "grant_scope") and "another site" in moved["detail"], moved
        await _fill(client, unfilled, FORM)  # not the apply link's host the user saw
        assert _pair(await _check(client, unfilled)) == ("ask", "grant_scope")
        await _fill(client, unfilled, "https://northwind.example/careers/4/apply")
        assert _pair(await _check(client, unfilled, "https://northwind.example/careers/4/apply")) == AUTO


@pytest.mark.asyncio
async def test_mcp_check_submit_and_get_profile_give_the_route_answer(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        inside, outside = await _bring(client, 1), await _bring(client, 2)
        for app_id in (inside, outside):
            await _fill(client, app_id)
        await _event(client, inside, "queued")
        await _grant(client)
        route = {app_id: await _check(client, app_id) for app_id in (inside, outside)}
        view = (await client.get("/api/assistant-settings")).json()["auto_grant_scope"]
        token = await _token(client)
    assert _pair(route[inside]) == AUTO and _pair(route[outside]) == ("ask", "grant_scope")
    from src.api.mcp_server import mcp_runtime

    async with mcp_runtime(), _mcp_client(token) as mcp:
        for app_id in (inside, outside):
            result = await mcp.call_tool("check_submit", {"application_id": app_id, "form_url": FORM})
            assert not result.is_error, result.content[0].text
            tool = json.loads(result.content[0].text)
            keys = ("decision", "reason", "detail")
            assert {k: tool[k] for k in keys} == {k: route[app_id][k] for k in keys}
        profile = await mcp.call_tool("get_profile", {})
        assert not profile.is_error, profile.content[0].text
    got = json.loads(profile.content[0].text)["settings"]["auto_grant_scope"]
    assert got == view == {
        "queue": {"count": 1, "hosts": [FORM_HOST], "no_host": 0},
        "granted": {"count": 1, "hosts": [FORM_HOST], "no_host": 0}, "unscoped": False,
    }


@pytest.mark.asyncio
async def test_a_grant_without_a_snapshot_is_unscoped_only_if_it_began_before_scoping(
    authenticated_async_context, fixture_user_id
):
    until = json.dumps((datetime.now(timezone.utc) + timedelta(hours=3)).isoformat())
    before_cutoff = (grant_scope.SCOPE_REQUIRED_FROM - timedelta(hours=6)).isoformat()

    def old_grant(set_at: str) -> None:
        for path, value in ((GRANT_N, "5"), (GRANT_UNTIL, until)):
            _sql(
                "INSERT INTO profile_edits (user_id, path, value, set_by, set_at, source) VALUES (?, ?, ?, ?, ?, ?)",
                (fixture_user_id, path, value, "web", set_at, "user_said"),
            )

    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client, 1)  # never queued
        await _fill(client, app_id)
        old_grant(before_cutoff)  # confirmed before snapshots existed: today's behaviour until it ends
        assert _pair(await _check(client, app_id)) == AUTO
        view = (await client.get("/api/assistant-settings")).json()["auto_grant_scope"]
        assert view["unscoped"] is True and view["granted"] is None
        _sql("INSERT INTO profile_edits (user_id, path, value, set_by, set_at, source) VALUES (?, ?, NULL, ?, ?, ?)",
             (fixture_user_id, GRANT_UNTIL, "web", datetime.now(timezone.utc).isoformat(), "user_said"))
        old_grant(datetime.now(timezone.utc).isoformat())  # no snapshot, began after scoping: covers nothing
        assert _pair(await _check(client, app_id)) == ("ask", "grant_scope")
        view = (await client.get("/api/assistant-settings")).json()["auto_grant_scope"]
        assert view["unscoped"] is False and view["granted"] == {"count": 0, "hosts": [], "no_host": 0}


@pytest.mark.asyncio
async def test_the_scope_is_server_written_and_logged_without_hosts(
    authenticated_async_context, fixture_user_id, monkeypatch
):
    lines: list[dict[str, Any]] = []

    class _Audit:
        def info(self, msg: str, extra: dict[str, Any]) -> None:
            lines.append(extra)

    monkeypatch.setattr(grant_scope, "get_audit_logger", lambda: _Audit())
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client, 1)
        await _fill(client, app_id)
        await _event(client, app_id, "queued")
        bad = await client.patch("/api/profile", json={"edits": [{"path": grant_scope.SCOPE_PATH, "value": {"apps": []}}]})
        assert bad.status_code == 422, "nobody writes the scope through update_profile"
        await _grant(client)
    scoped = [line for line in lines if line["event"] == "auto_grant_scoped"]
    assert len(scoped) == 1
    assert scoped[0]["applications"] == 1 and scoped[0]["hosts"] == 1 and scoped[0]["no_host"] == 0
    assert FORM_HOST not in json.dumps(scoped[0]) and "northwind" not in json.dumps(scoped[0])


@pytest.mark.asyncio
async def test_a_riskier_grant_from_a_token_snapshots_only_when_the_user_confirms(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        reviewed, later = await _bring(client, 1), await _bring(client, 2)
        for app_id in (reviewed, later):
            await _fill(client, app_id)
        token = await _token(client)
        async with _bearer_client(token) as bearer:
            until = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
            asked = await bearer.patch("/api/profile", json={"edits": [
                {"path": GRANT_N, "value": 3}, {"path": GRANT_UNTIL, "value": until},
            ]})
            assert asked.status_code == 200 and asked.json()["applied"] == []
        await _event(client, reviewed, "queued")
        for request in (await client.get("/api/assistant-settings")).json()["waiting"]:
            done = await client.post(f"/api/assistant-settings/requests/{request['id']}/confirm")
            assert done.status_code == 200, done.text
        await _event(client, later, "queued")
        assert _pair(await _check(client, reviewed)) == AUTO
        assert _pair(await _check(client, later)) == ("ask", "grant_scope")


# ═══════════════════════════════════════════════════════════════════════════
# Pure gate
# ═══════════════════════════════════════════════════════════════════════════


def _cfg(until_hours: float = 2, count: int = 3) -> rules.EffectiveSettings:
    return rules.effective(AssistantSettings(
        apply_min_score=50, above_line="auto", below_line="auto", submit_mode="confirm",
        auto_grant_count=count, auto_grant_until=(NOW + timedelta(hours=until_hours)).isoformat(),
    ))


def _facts(**kw: Any) -> rules.SubmitFacts:
    base: dict[str, Any] = {
        "status": "considering", "has_receipt": False, "submit_override": None, "cv_seen": True,
        "form_filled": True, "form_swept": True, "fit_score": 80, "form_host": FORM_HOST, "application_id": 7,
    }
    base.update(kw)
    return rules.SubmitFacts(**base)


def _gate(cfg: rules.EffectiveSettings, scope: Any, facts: rules.SubmitFacts | None = None, **counts: Any):
    counts.setdefault("applied_since_auto_on", 1)
    got = rules.may_submit(
        cfg, facts or _facts(), FORM, rules.SubmitCounts(submitted_today=0, grant_scope=scope, **counts), now=NOW
    )
    return got.decision, got.reason


def test_the_gate_reads_the_scope_and_the_expiry_still_ends_everything():
    scope = {7: FORM_HOST, 8: None}
    assert _gate(_cfg(), scope) == ("submit", "auto_when_sure")
    assert _gate(_cfg(), scope, _facts(application_id=9)) == ("ask", "grant_scope"), "not in the snapshot"
    assert _gate(_cfg(), {7: "old.example"}, _facts()) == ("ask", "grant_scope"), "host moved since the yes"
    assert _gate(_cfg(), scope, _facts(application_id=8)) == ("ask", "grant_scope"), "no host then: any host is new"
    assert _gate(_cfg(), {}, _facts()) == ("ask", "grant_scope"), "an empty snapshot covers nothing"
    assert _gate(_cfg(until_hours=-1), scope) == ("ask", "submit_mode_confirm"), "an ended grant covers nothing"
    assert _gate(_cfg(until_hours=-1), None) == ("ask", "submit_mode_confirm")
    assert _gate(_cfg(), scope, applied_since_grant=3) == ("ask", "submit_mode_confirm"), "count used up"
    assert _gate(_cfg(), None, _facts(application_id=None)) == ("submit", "auto_when_sure"), "legacy: unscoped"
    # the user's own modes still win: their auto mode, their per-job confirm, their yes
    own_auto = rules.effective(AssistantSettings(apply_min_score=50, above_line="auto", below_line="auto",
                                                 submit_mode="auto_when_sure"))
    assert _gate(own_auto, {}, _facts()) == ("submit", "auto_when_sure")
    assert _gate(_cfg(), {}, _facts(submit_override="confirm")) == ("ask", "job_override_confirm")
    assert _gate(_cfg(), {}, _facts(approved=True)) == ("submit", "user_approved")
    assert "grant_scope" in rules.REASONS


def test_the_record_hook_watches_the_two_grant_paths():
    from src.services.profile import edits

    assert edits._GRANT_PATHS == {rules.AUTO_GRANT_COUNT_PATH, rules.AUTO_GRANT_UNTIL_PATH}
    assert grant_scope.SCOPE_PATH not in edits.editable_paths()
