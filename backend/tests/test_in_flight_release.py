"""Weak spot #3, wave 2 (2026-10-15): the web shows who holds an application and lets the user release it.

Wave 1 gave the backend a hold (kit read by another session). A crashed session left it
standing for KIT_HOLD_MINUTES with no way out except the assistant itself. Here the list and
the detail carry ``hold`` + ``stage``, ``in_flight`` narrows the list, and the web can
``POST .../hold/release`` (appends ``hold_released``; nothing is rewritten).

Tests go through the real doors (HTTP + MCP) and assert VALUES (rule #21). Helpers are copied,
never imported from another test module.
"""
from __future__ import annotations

import json
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.core import settings

CV_TEXT = "Ada Lovelace\nSenior data engineer\nPython, dbt, Snowflake."
FORM = "https://careers.northwind.example/apply/7"
ACTOR = "token:claude-code"
_AD = {
    "title": "Data Engineer", "company": "Northwind", "location": "London", "country": "GB", "remote": False,
    "apply_url": "https://northwind.example/careers/7",
    "description": "Build the pipelines. Python, dbt, Snowflake.",
}
_AD_B = {**_AD, "title": "Analytics Engineer", "company": "Contoso", "apply_url": "https://contoso.example/careers/9"}


def _seed_profile(user_id: str) -> None:
    from src.services.profile.models import AssistantSettings, CVData, UserPreferences, UserProfile
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


def _bearer_client(token: str) -> AsyncClient:
    from src.api.main import app

    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    )


async def _setup(client: AsyncClient, user_id: str) -> tuple[int, int, str]:
    """Two brought jobs (A, B) with a fit and a CV each, and a personal token (actor ``token:claude-code``)."""
    _seed_profile(user_id)
    ids = []
    for ad in (_AD, _AD_B):
        resp = await client.post("/api/jobs/bring", json=ad)
        assert resp.status_code == 200, resp.text
        app_id = int(resp.json()["application_id"])
        assert (await client.put(f"/api/applications/{app_id}/fit", json={"score": 80})).status_code == 200
        saved = await client.post(f"/api/applications/{app_id}/artifacts", json={"kind": "cv", "text": CV_TEXT})
        assert saved.status_code == 201, saved.text
        ids.append(app_id)
    minted = await client.post("/api/tokens", json={"name": "claude-code"})
    assert minted.status_code == 201, minted.text
    return ids[0], ids[1], minted.json()["token"]


async def _kit(client: AsyncClient, app_id: int, session: str) -> dict[str, Any]:
    resp = await client.get(f"/api/applications/{app_id}/kit", params={"session": session})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _event(client: AsyncClient, app_id: int, event_type: str, payload: dict[str, Any]) -> None:
    resp = await client.post(
        f"/api/applications/{app_id}/events", json={"event_type": event_type, "payload": payload}
    )
    assert resp.status_code == 201, resp.text


async def _list(client: AsyncClient, **params: Any) -> dict[str, Any]:
    resp = await client.get("/api/applications", params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _rows(client: AsyncClient) -> dict[int, dict[str, Any]]:
    return {r["id"]: r for r in (await _list(client))["applications"]}


def _count_events(app_id: int, event_type: str | None = None) -> int:
    from src.repositories import pgsync

    conn = pgsync.connect(str(settings.DB_PATH))
    try:
        sql, args = "SELECT COUNT(*) FROM application_events WHERE application_id = ?", [app_id]
        if event_type:
            sql, args = sql + " AND event_type = ?", [*args, event_type]
        return int(conn.execute(sql, args).fetchone()[0])
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_list_in_flight_returns_only_held_apps(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as web:
        a, b, token = await _setup(web, fixture_user_id)
        assert (await _list(web, in_flight=True))["applications"] == [], "nothing held yet"
        async with _bearer_client(token) as agent:
            await _kit(agent, a, "desktop-apply")
        held = await _list(web, in_flight=True)
        assert [r["id"] for r in held["applications"]] == [a] and held["total"] == 1
        assert set(await _rows(web)) == {a, b}, "the plain list is unchanged"


@pytest.mark.asyncio
async def test_list_rows_carry_hold_values(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as web:
        a, b, token = await _setup(web, fixture_user_id)
        async with _bearer_client(token) as agent:
            await _kit(agent, a, "desktop-apply")
        rows = await _rows(web)
    assert rows[a]["hold"]["session"] == "desktop-apply" and rows[a]["hold"]["held_by"] == ACTOR
    assert rows[a]["hold"]["unsure"] is False and rows[a]["hold"]["until"] > rows[a]["hold"]["since"]
    assert rows[a]["stage"] == "Reading the kit…"
    assert rows[b]["hold"] is None and rows[b]["stage"] == ""


@pytest.mark.asyncio
async def test_stage_follows_latest_event(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as web:
        a, _b, token = await _setup(web, fixture_user_id)
        async with _bearer_client(token) as agent:
            await _kit(agent, a, "s1")
            await _event(agent, a, "form_filled", {"form_url": FORM, "fields_count": 1})
            assert (await _rows(web))[a]["stage"] == "Form filled"
            await _event(agent, a, "form_swept", {"fields_checked": 1, "fixed": 0})
            assert (await _rows(web))[a]["stage"] == "Ready to submit"
            detail = (await web.get(f"/api/applications/{a}")).json()
            assert detail["stage"] == "Ready to submit" and detail["hold"]["session"] == "s1"
            await _event(agent, a, "hold_released", {"reason": "done"})
        row = (await _rows(web))[a]
        assert row["stage"] == "" and row["hold"] is None
        assert (await _list(web, in_flight=True))["applications"] == []


@pytest.mark.asyncio
async def test_release_route_appends_event_and_clears_hold(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as web:
        a, _b, token = await _setup(web, fixture_user_id)
        async with _bearer_client(token) as agent:
            await _kit(agent, a, "crashed-run")
            await _event(agent, a, "form_filled", {"form_url": FORM, "fields_count": 1})
            await _event(agent, a, "form_swept", {"fields_checked": 1, "fixed": 0})
            gate = await agent.get(
                f"/api/applications/{a}/submit-check", params={"form_url": FORM, "session": "other"}
            )
            assert gate.json()["reason"] == "held_by_other_session"
            before = _count_events(a)
            resp = await web.post(f"/api/applications/{a}/hold/release")
            assert resp.status_code == 200 and resp.json() == {"released": True}
            assert _count_events(a) == before + 1, "append-only: exactly one new event, none rewritten"
            assert _count_events(a, "hold_released") == 1
            detail = (await web.get(f"/api/applications/{a}")).json()
            released = [e for e in detail["events"] if e["event_type"] == "hold_released"]
            assert released[0]["recorded_by"] == "web"
            assert detail["hold"] is None and detail["stage"] == ""
            gate = await agent.get(
                f"/api/applications/{a}/submit-check", params={"form_url": FORM, "session": "other"}
            )
            assert gate.json()["reason"] != "held_by_other_session"


def test_release_route_is_session_only_with_auth_first():
    from src.api.auth_deps import require_session_user, require_user
    from src.api.routes.applications import router

    route = next(r for r in router.routes if getattr(r, "path", "") == "/applications/{application_id}/hold/release")
    assert [d.dependency for d in route.dependencies] == [require_user]
    assert any(d.call is require_session_user for d in route.dependant.dependencies)


@pytest.mark.asyncio
async def test_release_bearer_token_is_refused_and_hold_stands(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as web:
        a, _b, token = await _setup(web, fixture_user_id)
        async with _bearer_client(token) as agent:
            await _kit(agent, a, "owner-session")
            before = _count_events(a)
            # same refusal as blocked/resolve: a j360_ token can never release another session's hold
            assert (await agent.post(f"/api/applications/{a}/hold/release")).status_code in (401, 403)
        assert _count_events(a) == before and _count_events(a, "hold_released") == 0
        assert (await _rows(web))[a]["hold"]["session"] == "owner-session", "the hold stands"


@pytest.mark.asyncio
async def test_release_idempotent_when_no_hold(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as web:
        a, _b, _token = await _setup(web, fixture_user_id)
        before = _count_events(a)
        resp = await web.post(f"/api/applications/{a}/hold/release")
        assert resp.status_code == 200 and resp.json() == {"released": False}
        assert _count_events(a) == before and _count_events(a, "hold_released") == 0


@pytest.mark.asyncio
async def test_release_other_users_app_is_404(authenticated_async_context, fixture_user_id):
    from fastapi.testclient import TestClient

    from src.api.main import app
    from src.repositories import pgsync

    async with authenticated_async_context() as web:
        a, _b, token = await _setup(web, fixture_user_id)
        async with _bearer_client(token) as agent:
            await _kit(agent, a, "s1")
    email = "release-intruder@example.com"
    sync_client = TestClient(app)
    reg = sync_client.post("/api/auth/register", json={"email": email, "password": "s3cretpassword"})
    assert reg.status_code == 201, reg.text
    conn = pgsync.connect(str(settings.DB_PATH))
    conn.execute("UPDATE users SET email_verified_at = ? WHERE email = ?", ("2026-01-01T00:00:00Z", email))
    conn.commit()
    conn.close()
    login = sync_client.post("/api/auth/login", json={"email": email, "password": "s3cretpassword"})
    assert login.status_code == 200, login.text
    cookie = str(login.cookies.get("job360_session"))
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", cookies={"job360_session": cookie}
    ) as intruder:
        assert (await intruder.post(f"/api/applications/{a}/hold/release")).status_code == 404
        assert (await _list(intruder, in_flight=True))["applications"] == []
    assert _count_events(a, "hold_released") == 0, "the owner's hold stands"


@pytest.mark.asyncio
async def test_release_requires_auth_401(authenticated_async_context):
    from src.api.main import app

    async with authenticated_async_context():
        pass
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        assert (await anon.post("/api/applications/1/hold/release")).status_code == 401
        assert (await anon.get("/api/applications", params={"in_flight": True})).status_code == 401


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


@pytest.mark.asyncio
async def test_mcp_list_applications_in_flight_parity(authenticated_async_context, fixture_user_id):
    pytest.importorskip("mcp")
    from src.api.mcp_server import mcp_runtime

    async with authenticated_async_context() as web:
        a, b, token = await _setup(web, fixture_user_id)
        async with _bearer_client(token) as agent:
            await _kit(agent, a, "cloud-run")
            route = await _list(agent, in_flight=True)
    async with mcp_runtime():
        async with _mcp_client(token) as mcp:
            result = await mcp.call_tool("list_applications", {"in_flight": True})
            assert not result.is_error, result.content[0].text
            tool = json.loads(result.content[0].text)
    assert [r["id"] for r in tool["applications"]] == [a] == [r["id"] for r in route["applications"]]
    assert b not in [r["id"] for r in tool["applications"]]
    # the MCP output omits null keys (detail), the route keeps them
    assert tool["applications"][0]["hold"] == {k: v for k, v in route["applications"][0]["hold"].items() if v is not None}
    assert tool["applications"][0]["stage"] == route["applications"][0]["stage"] == "Reading the kit…"
