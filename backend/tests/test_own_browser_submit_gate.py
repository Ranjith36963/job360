"""Autonomy A8 (2026-11-02): the own-browser submit gate. check_submit's `submit` writes one server-only,
single-use `submit_cleared`; an assistant's receipt needs an unused, unexpired one (409 `submit_not_cleared`)
and spends it (`submit_cleared_used`). The user's own web click is not gated; Job360's browser finds the
clearance its own check wrote. Real Postgres through the routes and MCP. Helpers are copied, never imported.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.core import settings
from src.services.profile.models import AssistantSettings, CVData, UserPreferences, UserProfile

CV_TEXT = "Ada Lovelace\nSenior data engineer\nPython, dbt, Snowflake."
FORM = "https://careers.northwind.example/apply/7"
NOTICE = {"question": "Preferred start date", "answer": "1 month", "source": "profile"}


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


def _sql(statement: str, params: tuple = ()) -> list[Any]:
    from src.repositories import pgsync

    conn = pgsync.connect(str(settings.DB_PATH))
    try:
        cur = conn.execute(statement, params)
        rows = cur.fetchall() if cur.description else []
        conn.commit()
        return rows
    finally:
        conn.close()


def _events(app_id: int, event_type: str) -> list[dict[str, Any]]:
    rows = _sql("SELECT id, payload FROM application_events WHERE application_id = ? AND event_type = ? ORDER BY id",
                (app_id, event_type))
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


async def _event(client: AsyncClient, app_id: int, event_type: str, payload: dict[str, Any]) -> int:
    resp = await client.post(f"/api/applications/{app_id}/events", json={"event_type": event_type, "payload": payload})
    assert resp.status_code == 201, resp.text
    return int(resp.json()["event_id"])


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
    await _event(client, app_id, "form_swept", {"fields_checked": 1, "fixed": 0, "answers": [NOTICE]})
    if approve:
        yes = await client.post(f"/api/applications/{app_id}/send/approve", params={"form_filled_event_id": fill})
        assert yes.status_code == 201, yes.text
    return app_id


def _payload(result) -> dict:
    assert not result.is_error, result.content[0].text
    return json.loads(result.content[0].text)


def _error(result) -> str:
    assert result.is_error, "expected a tool error"
    return result.content[0].text


async def _check(mcp, app_id: int) -> dict[str, Any]:
    return _payload(await mcp.call_tool("check_submit", {"application_id": app_id, "form_url": FORM}))


async def _record(mcp, app_id: int, **extra: Any):
    return await mcp.call_tool("record_application", {"application_id": app_id, "channel": "company_site", **extra})


@pytest.mark.asyncio
async def test_mcp_receipt_needs_a_submit_and_spends_it_once(authenticated_async_context, fixture_user_id):
    from src.api.mcp_server import mcp_runtime

    async with authenticated_async_context() as web:
        app_id = await _app(web, fixture_user_id, 1)
        token = await _token(web)
    async with mcp_runtime():
        async with _mcp_client(token) as mcp:
            refused = _error(await _record(mcp, app_id))
            assert "409" in refused and "submit_not_cleared" in refused and "call check_submit" in refused
            assert (await _check(mcp, app_id))["decision"] == "submit"
            [cleared] = _events(app_id, "submit_cleared")
            assert cleared["form_url"] == FORM and isinstance(cleared["cv_artifact_id"], int)
            receipt = _payload(await _record(mcp, app_id, request_id="sub-1"))
            # a retry of the same request replays; a second receipt on the spent clearance is refused
            assert _payload(await _record(mcp, app_id, request_id="sub-1"))["already_existed"] is True
            again = _error(await _record(mcp, app_id, request_id="sub-2"))
            assert "409" in again and "submit_not_cleared" in again
            # stop (already applied): no new clearance
            assert (await _check(mcp, app_id))["decision"] == "stop"
    assert _events(app_id, "submit_cleared_used") == [
        {"id": _events(app_id, "submit_cleared_used")[0]["id"], "clearance_event_id": cleared["id"],
         "receipt_id": receipt["receipt_id"]}
    ]
    assert len(_events(app_id, "submit_cleared")) == 1
    assert _sql("SELECT COUNT(*) FROM application_receipts WHERE application_id = ?", (app_id,))[0][0] == 1


@pytest.mark.asyncio
async def test_ask_records_no_clearance_and_an_expired_one_is_refused(authenticated_async_context, fixture_user_id):
    from src.api.mcp_server import mcp_runtime

    async with authenticated_async_context() as web:
        asked = await _app(web, fixture_user_id, 2, approve=False)
        expired = await _app(web, fixture_user_id, 3)
        token = await _token(web)
    async with mcp_runtime():
        async with _mcp_client(token) as mcp:
            assert (await _check(mcp, asked))["decision"] == "ask"
            assert _events(asked, "submit_cleared") == []
            assert (await _check(mcp, expired))["decision"] == "submit"
            old = (datetime.now(timezone.utc) - timedelta(minutes=settings.SUBMIT_APPROVAL_TTL_MINUTES + 1)).isoformat()
            _sql("UPDATE application_events SET recorded_at = ? WHERE application_id = ? AND event_type = 'submit_cleared'",
                 (old, expired))
            refused = _error(await _record(mcp, expired))
            assert "409" in refused and "submit_not_cleared" in refused
    assert _events(expired, "submit_cleared_used") == []


@pytest.mark.asyncio
async def test_other_assistant_doors_are_gated_and_cannot_forge(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as web:
        app_id = await _app(web, fixture_user_id, 4)
        job_id = int((await web.get(f"/api/applications/{app_id}")).json()["job_id"])
        async with _bearer(await _token(web)) as agent:
            for forged in ("submit_cleared", "submit_cleared_used"):
                r = await agent.post(f"/api/applications/{app_id}/events",
                                     json={"event_type": forged, "payload": {"clearance_event_id": 1}})
                assert r.status_code == 422 and "written by Job360 itself" in r.text, r.text
            r = await agent.post(f"/api/applications/{app_id}/events", json={"event_type": "applied", "payload": {}})
            assert r.status_code == 409 and "submit_not_cleared" in r.text, r.text
            r = await agent.post(f"/api/receipts/{job_id}", json={"note": "sent"})
            assert r.status_code == 409 and "submit_not_cleared" in r.text, r.text
            check = await agent.get(f"/api/applications/{app_id}/submit-check", params={"form_url": FORM})
            assert check.json()["decision"] == "submit", check.text
            r = await agent.post(f"/api/applications/{app_id}/events", json={"event_type": "applied", "payload": {}})
            assert r.status_code == 201, r.text
    [used] = _events(app_id, "submit_cleared_used")
    assert used["applied_event_id"] == r.json()["event_id"]


@pytest.mark.asyncio
async def test_the_receipts_route_spends_the_clearance_with_its_receipt(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as web:
        app_id = await _app(web, fixture_user_id, 7)
        job_id = int((await web.get(f"/api/applications/{app_id}")).json()["job_id"])
        async with _bearer(await _token(web)) as agent:
            check = await agent.get(f"/api/applications/{app_id}/submit-check", params={"form_url": FORM})
            assert check.json()["decision"] == "submit", check.text
            r = await agent.post(f"/api/receipts/{job_id}", json={"note": "sent"})
            assert r.status_code == 201, r.text
    [used] = _events(app_id, "submit_cleared_used")
    assert used["receipt_id"] == r.json()["id"]


@pytest.mark.asyncio
async def test_the_users_own_web_i_applied_is_not_gated(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as web:
        app_id = await _app(web, fixture_user_id, 5)
        r = await web.post(f"/api/applications/{app_id}/receipt", json={"channel": "company_site"})
        assert r.status_code == 201, r.text
    assert _events(app_id, "submit_cleared") == [] and _events(app_id, "submit_cleared_used") == []


@pytest.mark.asyncio
async def test_job360_browser_gate_writes_the_clearance_its_receipt_uses(authenticated_async_context, fixture_user_id):
    """No exemption: the browser's own check (routes/browser.py `_check`) is the same submit_check route."""
    from src.api.auth_deps import CurrentUser
    from src.api.mcp_server import mcp_runtime
    from src.api.routes import browser as browser_route

    async with authenticated_async_context() as web:
        app_id = await _app(web, fixture_user_id, 6)
        token = await _token(web)
        email = (await web.get("/api/auth/me")).json()["email"]
    agent = CurrentUser(id=fixture_user_id, email=email, email_verified=True, auth_via="token",
                        actor_name="claude-code")
    gate, _files = browser_route._gate_and_files(agent, app_id, "")
    assert (await gate.check(FORM))["decision"] == "submit"
    await gate.mark_attempt()
    async with mcp_runtime():
        async with _mcp_client(token) as mcp:
            receipt = _payload(await _record(mcp, app_id))
    [cleared] = _events(app_id, "submit_cleared")
    [used] = _events(app_id, "submit_cleared_used")
    assert used["clearance_event_id"] == cleared["id"] and used["receipt_id"] == receipt["receipt_id"]
