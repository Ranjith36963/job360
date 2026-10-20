"""Gap 10 - upcoming and just-passed interviews reach the agent (``whats_new``, ``doctor``) and
``list_applications(agenda=...)`` filters to a day or week in the user's own timezone.

The agenda reuses the calendar feed's notion of a live interview (``calendar_ics.live_interviews``),
so a corrected interview and a closed application drop out exactly as they do from the .ics feed.
Helpers are copied, never imported from another test module.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from httpx import ASGITransport, AsyncClient

from src.core import settings

_AD = {
    "title": "Data Engineer", "company": "Northwind", "location": "London", "country": "GB", "remote": False,
    "apply_url": "https://northwind.example/careers/7", "description": "Build the pipelines.",
}


def _iso(days: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).replace(microsecond=0).isoformat()


async def _bring(client: AsyncClient, ad: dict[str, Any] | None = None) -> int:
    resp = await client.post("/api/jobs/bring", json=ad or _AD)
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _event(client: AsyncClient, app_id: int, event_type: str, **extra: Any) -> dict[str, Any]:
    resp = await client.post(f"/api/applications/{app_id}/events", json={"event_type": event_type, **extra})
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _interviews(client: AsyncClient) -> dict[str, list[dict[str, Any]]]:
    resp = await client.get("/api/whats-new")
    assert resp.status_code == 200, resp.text
    return resp.json()["interviews"]


def _sql(statement: str, params: tuple = ()) -> None:
    from src.repositories import pgsync

    conn = pgsync.connect(str(settings.DB_PATH))
    try:
        conn.execute(statement, params)
        conn.commit()
    finally:
        conn.close()


async def _second_user_session_cookie(email: str = "second@example.com") -> str:
    from fastapi.testclient import TestClient

    from src.api.main import app

    sync_client = TestClient(app)
    r = sync_client.post("/api/auth/register", json={"email": email, "password": "s3cretpassword"})
    assert r.status_code == 201, r.text
    _sql("UPDATE users SET email_verified_at = ? WHERE email = ?", ("2026-01-01T00:00:00Z", email))
    lr = sync_client.post("/api/auth/login", json={"email": email, "password": "s3cretpassword"})
    assert lr.status_code == 200, lr.text
    return str(lr.cookies.get("job360_session"))


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


def _payload(result) -> dict:
    assert not result.is_error, result.content[0].text
    return json.loads(result.content[0].text)


# ── upcoming and just_passed ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_empty_account_has_empty_interview_lists(authenticated_async_context):
    async with authenticated_async_context() as client:
        assert await _interviews(client) == {"upcoming": [], "just_passed": []}
        doctor = (await client.get("/api/doctor")).json()
    assert doctor["interviews"] == {"upcoming": [], "just_passed": []}


@pytest.mark.asyncio
async def test_two_rounds_one_upcoming_one_just_passed(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        past = _iso(-1)
        soon = _iso(2)
        await _event(client, app_id, "interview_scheduled", scheduled_at=past, payload={"round": "phone_screen"})
        await _event(
            client, app_id, "interview_scheduled", scheduled_at=soon,
            payload={"round": "technical", "link": "https://meet.example/abc", "duration_minutes": 45},
        )
        other = await _bring(client, {**_AD, "title": "Analyst", "company": "Globex", "apply_url": "https://g.example/1"})
        await _event(client, other, "interview_scheduled", scheduled_at=_iso(20))  # beyond 7 days
        await _event(client, other, "interview_scheduled", scheduled_at=_iso(-9))  # beyond 3 days back
        got = await _interviews(client)
        doctor = (await client.get("/api/doctor")).json()["interviews"]
    # the earlier round has no outcome, but the NEXT round is booked: that counts as answered.
    assert got["just_passed"] == []
    assert [i["application_id"] for i in got["upcoming"]] == [app_id]
    item = got["upcoming"][0]
    assert item["company"] == "Northwind" and item["title"] == "Data Engineer"
    assert item["round"] == "technical" and item["link"] == "https://meet.example/abc"
    assert item["duration_minutes"] == 45
    assert datetime.fromisoformat(item["scheduled_at"]) == datetime.fromisoformat(soon)
    assert doctor == got


@pytest.mark.asyncio
async def test_just_passed_lists_both_rounds_when_nothing_followed(authenticated_async_context):
    async with authenticated_async_context() as client:
        a = await _bring(client)
        b = await _bring(client, {**_AD, "title": "Analyst", "company": "Globex", "apply_url": "https://g.example/1"})
        await _event(client, a, "interview_scheduled", scheduled_at=_iso(-2), payload={"round": "phone_screen"})
        final = await _event(
            client, b, "interview_scheduled", scheduled_at=_iso(-0.5), payload={"round": "final"},
        )
        # A non-http link is refused at write time now (slice 43); rows from before that can
        # still hold one, so the agenda must keep dropping it. Plant such a row directly.
        _sql("UPDATE application_events SET payload = ? WHERE id = ?",
             (json.dumps({"round": "final", "link": "javascript:alert(1)"}), final["event_id"]))
        got = await _interviews(client)
    assert got["upcoming"] == []
    assert [(i["application_id"], i["round"]) for i in got["just_passed"]] == [(a, "phone_screen"), (b, "final")]
    assert got["just_passed"][1]["link"] == "", "only http(s) links leave"
    assert got["just_passed"][0]["duration_minutes"] == 60


@pytest.mark.asyncio
async def test_corrected_interview_is_excluded(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        first = await _event(client, app_id, "interview_scheduled", scheduled_at=_iso(2))
        moved = await _event(
            client, app_id, "interview_scheduled", scheduled_at=_iso(4), corrects_event_id=first["event_id"]
        )
        got = await _interviews(client)
    assert len(got["upcoming"]) == 1
    assert datetime.fromisoformat(got["upcoming"][0]["scheduled_at"]) == datetime.fromisoformat(_iso(4))
    assert moved["event_id"] != first["event_id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["interview_done", "rejected", "offer", "replied"])
async def test_recording_an_outcome_removes_it_from_just_passed(authenticated_async_context, outcome):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        await _event(client, app_id, "interview_scheduled", scheduled_at=_iso(-1))
        assert [i["application_id"] for i in (await _interviews(client))["just_passed"]] == [app_id]
        await _event(client, app_id, outcome)
        assert (await _interviews(client))["just_passed"] == []


@pytest.mark.asyncio
async def test_a_note_does_not_count_as_an_outcome(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        await _event(client, app_id, "interview_scheduled", scheduled_at=_iso(-1))
        await _event(client, app_id, "note", detail="remember to send thanks")
        assert [i["application_id"] for i in (await _interviews(client))["just_passed"]] == [app_id]


@pytest.mark.asyncio
async def test_closed_application_is_excluded(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        await _event(client, app_id, "interview_scheduled", scheduled_at=_iso(2))
        await _event(client, app_id, "withdrawn")
        assert await _interviews(client) == {"upcoming": [], "just_passed": []}


@pytest.mark.asyncio
async def test_another_users_interview_never_appears(authenticated_async_context):
    async with authenticated_async_context() as client:
        mine = await _bring(client)
        await _event(client, mine, "interview_scheduled", scheduled_at=_iso(1))
        cookie = await _second_user_session_cookie()
    from src.api.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", cookies={"job360_session": cookie}
    ) as other:
        theirs = await _bring(other, {**_AD, "company": "Other Corp", "apply_url": "https://other.example/c/1"})
        theirs_past = await _bring(other, {**_AD, "company": "Other Two", "apply_url": "https://other.example/c/2"})
        await _event(other, theirs, "interview_scheduled", scheduled_at=_iso(2))
        await _event(other, theirs_past, "interview_scheduled", scheduled_at=_iso(-1))
        seen = await _interviews(other)
        assert [i["application_id"] for i in seen["upcoming"]] == [theirs]
        assert [i["application_id"] for i in seen["just_passed"]] == [theirs_past]
        listed = await other.get("/api/applications", params={"agenda": "week"})
        assert [a["id"] for a in listed.json()["applications"]] == [theirs]
        assert (await other.get("/api/doctor")).json()["interviews"] == seen


# ── the agenda filter, in the user's own timezone ───────────────────────────


@pytest.mark.asyncio
async def test_agenda_tomorrow_uses_the_users_timezone(authenticated_async_context, fixture_user_id):
    zone = ZoneInfo("Pacific/Auckland")  # UTC+12/+13: its "tomorrow" starts hours before UTC's
    _sql("UPDATE users SET timezone = ? WHERE id = ?", ("Pacific/Auckland", fixture_user_id))
    local_today = datetime.now(timezone.utc).astimezone(zone).date()

    def local(day_offset: int, hour: int) -> str:
        day = local_today + timedelta(days=day_offset)
        return datetime(day.year, day.month, day.day, hour, 0, tzinfo=zone).isoformat()

    async with authenticated_async_context() as client:
        ids = {}
        for name, when in {
            "today": local(0, 23), "tomorrow_early": local(1, 0), "tomorrow": local(1, 15), "after": local(2, 0),
        }.items():
            ids[name] = await _bring(
                client, {**_AD, "company": name, "apply_url": f"https://{name}.example/1"}
            )
            await _event(client, ids[name], "interview_scheduled", scheduled_at=when)
        followed = await _bring(client, {**_AD, "company": "chase", "apply_url": "https://chase.example/1"})
        await _event(client, followed, "note", follow_up_on=(local_today + timedelta(days=1)).isoformat())
        tomorrow = (await client.get("/api/applications", params={"agenda": "tomorrow"})).json()
        today = (await client.get("/api/applications", params={"agenda": "today"})).json()
        week = (await client.get("/api/applications", params={"agenda": "week"})).json()
        bad = await client.get("/api/applications", params={"agenda": "yesterday"})
    assert sorted(a["id"] for a in tomorrow["applications"]) == sorted(
        [ids["tomorrow_early"], ids["tomorrow"], followed]
    )
    assert tomorrow["total"] == 3
    assert [a["id"] for a in today["applications"]] == [ids["today"]]
    assert sorted(a["id"] for a in week["applications"]) == sorted(
        [ids["today"], ids["tomorrow_early"], ids["tomorrow"], ids["after"], followed]
    )
    assert bad.status_code == 422 and "agenda" in bad.json()["detail"]


# ── MCP == route (hard rule M5) ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mcp_matches_the_routes(authenticated_async_context):
    from src.api.mcp_server import mcp_runtime

    async with authenticated_async_context() as client:
        token = (await client.post("/api/tokens", json={"name": "agent"})).json()["token"]
        app_id = await _bring(client)
        await _event(client, app_id, "interview_scheduled", scheduled_at=_iso(1), payload={"round": "onsite"})
        done = await _bring(client, {**_AD, "company": "Done Ltd", "apply_url": "https://done.example/1"})
        await _event(client, done, "interview_scheduled", scheduled_at=_iso(-1))
        route_new = (await client.get("/api/whats-new")).json()["interviews"]
        route_doctor = (await client.get("/api/doctor")).json()["interviews"]
        route_list = (await client.get("/api/applications", params={"agenda": "week"})).json()
    assert [i["application_id"] for i in route_new["upcoming"]] == [app_id]
    assert [i["application_id"] for i in route_new["just_passed"]] == [done]
    async with mcp_runtime():
        async with _mcp_client(token) as mcp:
            mcp_new = _payload(await mcp.call_tool("whats_new", {}))["interviews"]
            mcp_doctor = _payload(await mcp.call_tool("doctor", {}))["interviews"]
            mcp_list = _payload(await mcp.call_tool("list_applications", {"agenda": "week"}))
            bad = await mcp.call_tool("list_applications", {"agenda": "someday"})
    assert mcp_new == route_new and mcp_doctor == route_doctor == route_new
    assert [a["id"] for a in mcp_list["applications"]] == [a["id"] for a in route_list["applications"]] == [app_id]
    assert bad.is_error and "agenda" in bad.content[0].text
