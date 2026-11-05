"""Where do I stand: ``stats(view="today")`` and the honestly named grant count.

* The owner's assistant quoted ``settings.auto_grant_scope.queue.count`` (492) as the apply queue (254).
  That number counts every queued, still-considering job BEFORE the user's own score-line rule drops any;
  the apply queue is ``list_applications(queued=true)``'s ``queue_summary.queued``. ``grant_covers_jobs``
  names the first one and says so.
* ``stats(view="today")`` answers "where do I stand" in one call, in the user's own timezone.

Real HTTP + MCP doors, VALUE asserts (rule #21). The clock is pinned per test and every date is derived from
that pinned instant, never a second ``now()``. Helpers are copied, never imported from another test module.
"""
from __future__ import annotations

import datetime as _dt
import json
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from httpx import AsyncClient

from src.core import settings
from src.services.profile.models import AssistantSettings, CVData, UserPreferences, UserProfile


def _ad(n: int) -> dict[str, Any]:
    return {
        "title": f"Data Engineer {n}", "company": f"Northwind {n}", "location": "Remote",
        "apply_url": f"https://northwind.example/careers/{n}",
        "description": "Build the pipelines. Python, dbt, Snowflake. Fully remote.",
    }


def _sql(statement: str, params: tuple = ()) -> None:
    from src.repositories import pgsync

    conn = pgsync.connect(str(settings.DB_PATH))
    try:
        conn.execute(statement, params)
        conn.commit()
    finally:
        conn.close()


class _Clock:
    """Pins "what time is it" in every module that stamps today's facts; ``set`` moves it."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, when: datetime):
        from src.services.applications import asks, spine, today

        self.when = when
        clock = self

        class _Frozen(_dt.datetime):
            @classmethod
            def now(cls, tz=None):  # type: ignore[override]
                return clock.when.astimezone(tz) if tz else clock.when

        for module in (spine, today, asks):
            monkeypatch.setattr(module, "datetime", _Frozen)

    def set(self, when: datetime) -> None:
        self.when = when


async def _bring(client: AsyncClient, n: int) -> int:
    resp = await client.post("/api/jobs/bring", json=_ad(n))
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _fit(client: AsyncClient, app_id: int, score: int = 80) -> None:
    fit = {"score": score, "verdict": "ok", "score_source": "full_ad"}
    resp = await client.put(f"/api/applications/{app_id}/fit", json=fit)
    assert resp.status_code == 200, resp.text


async def _contact(client: AsyncClient, app_id: int) -> int:
    resp = await client.post(f"/api/applications/{app_id}/contacts", json={"name": "Ann", "email": "ann@northwind.example"})
    assert resp.status_code == 201, resp.text
    return int(resp.json()["contact"]["id"])


async def _event(
    client: AsyncClient, app_id: int, event_type: str, payload: dict[str, Any] | None = None, **extra: Any
) -> None:
    body: dict[str, Any] = {"event_type": event_type, **extra}
    if payload is not None:
        body["payload"] = payload
    resp = await client.post(f"/api/applications/{app_id}/events", json=body)
    assert resp.status_code == 201, resp.text


async def _receipt(client: AsyncClient, app_id: int) -> None:
    resp = await client.post(f"/api/applications/{app_id}/receipt", json={"channel": "company_site"})
    assert resp.status_code == 201, resp.text


async def _ask(client: AsyncClient, app_id: int, question: str) -> int:
    resp = await client.post("/api/asks", json={"application_id": app_id, "question": question})
    assert resp.status_code == 201, resp.text
    return int(resp.json()["id"])


async def _token(client: AsyncClient) -> str:
    resp = await client.post("/api/tokens", json={"name": "claude-code"})
    assert resp.status_code == 201, resp.text
    return resp.json()["token"]


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


async def _mcp(token: str, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    from src.api.mcp_server import mcp_runtime

    async with mcp_runtime(), _mcp_client(token) as mcp:
        result = await mcp.call_tool(tool, args)
    assert not result.is_error, result.content[0].text
    return json.loads(result.content[0].text)


def _seed_line(user_id: str, line: int = 70) -> None:
    from src.services.profile.storage import save_assistant_settings, save_profile

    save_profile(
        UserProfile(cv_data=CVData(raw_text="Python data engineer.", name="Ada Lovelace"),
                    preferences=UserPreferences(target_job_titles=["Data Engineer"])),
        user_id, source_action="cv_upload",
    )
    save_assistant_settings(user_id, AssistantSettings(apply_min_score=line, below_line="skip"), "test_seed")


# ── #5: the number named "queue" is not the apply queue ───────────────────────


@pytest.mark.asyncio
async def test_grant_covers_jobs_is_not_the_apply_queue(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_line(fixture_user_id)
        ids = [await _bring(client, n) for n in (1, 2, 3)]
        for app_id in ids:
            await _fit(client, app_id, 90)
            await _event(client, app_id, "queued")
        # the user's line is 70: the assistant's stored fit for the third falls under it AFTER it was queued
        _sql("UPDATE applications SET fit_score = 30 WHERE id = ?", (ids[2],))
        listed = (await client.get("/api/applications", params={"queued": "true"})).json()
        view = (await client.get("/api/assistant-settings")).json()
        token = await _token(client)
    assert listed["queue_summary"]["queued"] == 2
    assert view["auto_grant_scope"]["queue"]["count"] == 3  # the old key: before the line rule drops any
    assert view["grant_covers_jobs"]["count"] == 3
    assert "queue_summary.queued" in view["grant_covers_jobs"]["meaning"]
    assert "before your score-line rule" in view["grant_covers_jobs"]["meaning"]
    got = (await _mcp(token, "get_profile", {}))["settings"]
    assert got["grant_covers_jobs"] == view["grant_covers_jobs"]
    assert got["auto_grant_scope"] == view["auto_grant_scope"]


# ── #6 / #7: stats(view="today") ──────────────────────────────────────────────

# (zone, pinned instant): the first is 50 minutes before UTC midnight; the second is 23:30 on the 4th in
# Los Angeles while the UTC calendar already says the 5th.
DAYS = [
    pytest.param("UTC", datetime(2026, 3, 28, 23, 50, tzinfo=timezone.utc), id="utc-before-midnight"),
    pytest.param("America/Los_Angeles", datetime(2026, 11, 5, 7, 30, tzinfo=timezone.utc), id="la-before-midnight"),
]

COUNT_KEYS = {
    "brought", "fit_judged", "applied", "asks_opened", "asks_answered", "outreach_sent", "outreach_replies",
    "replies",
}


@pytest.mark.asyncio
@pytest.mark.parametrize(("zone_name", "now"), DAYS)
async def test_today_view_counts_the_users_own_day(
    authenticated_async_context, fixture_user_id, monkeypatch, zone_name, now
):
    zone = ZoneInfo(zone_name)
    local_midnight = now.astimezone(zone).replace(hour=0, minute=0, second=0, microsecond=0)
    just_before = local_midnight.astimezone(timezone.utc) - timedelta(minutes=1)  # still yesterday
    just_after = local_midnight.astimezone(timezone.utc) + timedelta(minutes=5)  # already today
    _sql("UPDATE users SET timezone = ? WHERE id = ?", (zone_name, fixture_user_id))
    clock = _Clock(monkeypatch, just_before)
    async with authenticated_async_context() as client:
        # yesterday, one minute before local midnight: none of this may count
        old = await _bring(client, 1)
        await _fit(client, old)
        await _receipt(client, old)
        await _event(client, old, "outreach_sent", contact_id=await _contact(client, old), channel="email")
        await _event(client, old, "replied")
        old_ask = await _ask(client, old, "Yesterday's question?")
        # today, five minutes after local midnight
        clock.set(just_after)
        early = await _bring(client, 2)
        await _receipt(client, early)
        # today, now
        clock.set(now)
        first, second = await _bring(client, 3), await _bring(client, 4)
        await _fit(client, first)
        await _fit(client, second)
        reach = await _contact(client, first)
        await _event(client, first, "outreach_sent", contact_id=reach, channel="email")
        await _event(client, first, "outreach_sent", contact_id=reach, channel="email")
        replier = await _bring(client, 6)
        await _event(client, replier, "replied")
        await _event(client, second, "queued")
        queued_early = await _bring(client, 5)
        await _event(client, queued_early, "queued")
        new_ask = await _ask(client, second, "Which visa?")
        answered = await client.post(f"/api/asks/{old_ask}/answer", json={"answer": "Yes"})
        assert answered.status_code == 200, answered.text
        http = await client.get("/api/applications/stats", params={"view": "today"})
        assert http.status_code == 200, http.text
        plain = (await client.get("/api/applications/stats")).json()
        listed = (await client.get("/api/applications", params={"queued": "true"})).json()
        token = await _token(client)
    assert plain["today"] is None
    today = http.json()["today"]
    assert set(today) == {"date", "timezone", "counts", "queue_summary", "waiting_on_you"}
    assert today["date"] == now.astimezone(zone).date().isoformat() and today["timezone"] == zone_name
    assert set(today["counts"]) == COUNT_KEYS
    assert today["counts"] == {
        "brought": 5,  # early + first + second + replier + queued_early; the one before midnight is yesterday's
        "fit_judged": 2, "applied": 1, "asks_opened": 1, "asks_answered": 1,
        "outreach_sent": 2, "outreach_replies": 0, "replies": 1,
    }
    # one function, one answer: the same block list_applications(queued=true) returns
    assert today["queue_summary"] == listed["queue_summary"]
    assert today["queue_summary"]["queued"] == 2 and today["queue_summary"]["asked"] == 1
    assert today["waiting_on_you"] == 1  # the open ask on `second`
    assert new_ask != old_ask
    assert (await _mcp(token, "stats", {"view": "today"}))["today"] == today


async def _today(client: AsyncClient) -> dict[str, Any]:
    resp = await client.get("/api/applications/stats", params={"view": "today"})
    assert resp.status_code == 200, resp.text
    return resp.json()["today"]


@pytest.mark.asyncio
async def test_today_counts_cold_outreach_and_leaves_test_asks_out(
    authenticated_async_context, fixture_user_id, monkeypatch
):
    now = datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc)
    _Clock(monkeypatch, now)
    async with authenticated_async_context() as client:
        real = await _bring(client, 1)
        test_ad = {**_ad(2), "test": True}
        resp = await client.post("/api/jobs/bring", json=test_ad)
        assert resp.status_code == 200, resp.text
        practice = int(resp.json()["application_id"])
        # a cold (network) contact: no application, so no application event is ever written for its marks
        cold = await client.post("/api/contacts", json={"name": "Cleo", "company": "Northwind", "email": "c@northwind.example"})
        assert cold.status_code in (200, 201), cold.text
        cid = int((cold.json().get("contact") or cold.json())["id"])
        for entry in ("sent", "reply"):
            marked = await client.post(f"/api/contacts/{cid}/outreach", json={"entry": entry, "channel": "email"})
            assert marked.status_code == 201, marked.text
        await _ask(client, real, "Notice period?")
        await _ask(client, practice, "Salary?")  # a test application: left out
        body = await _today(client)
    assert body["counts"]["outreach_sent"] == 1 and body["counts"]["outreach_replies"] == 1
    assert body["counts"]["asks_opened"] == 1
    assert body["counts"]["brought"] == 1  # the real one; the test application is left out


@pytest.mark.asyncio
async def test_today_query_count_does_not_grow_with_the_queue(authenticated_async_context, fixture_user_id):
    from src.repositories import pg

    seen = {"n": 0, "on": False}
    real_execute = pg.Connection.execute

    async def counting(self, sql, params=()):  # type: ignore[no-untyped-def]
        if seen["on"]:
            seen["n"] += 1
        return await real_execute(self, sql, params)

    async def measure(client: AsyncClient) -> int:
        seen["n"], seen["on"] = 0, True
        try:
            await _today(client)
        finally:
            seen["on"] = False
        return seen["n"]

    pg.Connection.execute = counting  # type: ignore[method-assign]
    try:
        async with authenticated_async_context() as client:
            for n in range(1, 4):
                await _event(client, await _bring(client, n), "queued")
            small = await measure(client)
            for n in range(4, 11):
                app_id = await _bring(client, n)
                await _event(client, app_id, "queued")
                await _event(client, app_id, "blocked", {"reason": "login_needed"})
            checked = await _today(client)
            large = await measure(client)
    finally:
        pg.Connection.execute = real_execute  # type: ignore[method-assign]
    assert checked["queue_summary"]["queued"] == 10 and checked["queue_summary"]["blocked"] == 7
    assert checked["waiting_on_you"] == 7
    assert small == large, (small, large)
