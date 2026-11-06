"""Sent on its own, kind ``cv`` (owner 2026-11-06: the safety net before ``cv_auto_approve``).

A CV that went out on a strong review instead of the user's look is recorded once (``cv_sent_on_review``,
server-written) and listed; 5 of them a day are the day's check; Wrong fact records the report and turns
``cv_auto_approve`` off at once. Real doors, values asserted (rule #21). Helpers are copied, never imported
from another test module.
"""
from __future__ import annotations

import hashlib
from datetime import date
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.services.applications import cv_review, sent_alone

FORM = "https://careers.northwind.example/apply/7"
RAW = (
    "Ada Lovelace\nEXPERIENCE\nData engineer at Northwind since 2019.\n"
    "Built ETL pipelines in Python and Snowflake, cutting warehouse costs 30%."
)
CV = (
    "Ada Lovelace\nEXPERIENCE\n- Data engineer at Northwind since 2019.\n"
    "- Built ETL pipelines in Python and Snowflake, cutting warehouse costs 30%.\n"
)
LINES = [
    {"cv_line": "- Data engineer at Northwind since 2019.", "profile_quote": "Data engineer at Northwind since 2019.",
     "field_path": "cv_data.raw_text"},
    {"cv_line": "Built ETL pipelines in Python and Snowflake, cutting warehouse costs 30%.",
     "profile_quote": "Built ETL pipelines in Python and Snowflake, cutting warehouse costs 30%.",
     "field_path": "cv_data.raw_text"},
]
CV_AUTO = "assistant_settings.cv_auto_approve"
AGENT = "token:claude-code"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _seed(user_id: str) -> None:
    from src.services.profile.models import AssistantSettings, CVData, UserProfile
    from src.services.profile.storage import save_assistant_settings, save_profile

    save_profile(UserProfile(cv_data=CVData(raw_text=RAW, name="Ada Lovelace")), user_id, source_action="cv_upload")
    save_assistant_settings(user_id, AssistantSettings(apply_min_score=50, above_line="auto", below_line="auto"), "seed")


async def _event(client: AsyncClient, app_id: int, event_type: str, payload: dict[str, Any]):
    return await client.post(f"/api/applications/{app_id}/events", json={"event_type": event_type, "payload": payload})


async def _reviewed_app(client: AsyncClient, n: int) -> tuple[int, int]:
    """One application whose current CV has a stored strong review (verified by Job360)."""
    brought = await client.post("/api/jobs/bring", json={
        "title": f"Data Engineer {n}", "company": f"Northwind {n}", "location": "London", "country": "GB",
        "remote": False, "apply_url": f"https://northwind.example/careers/{n}", "description": "Python, Snowflake.",
    })
    assert brought.status_code == 200, brought.text
    app_id = int(brought.json()["application_id"])
    saved = await client.post(f"/api/applications/{app_id}/artifacts", json={"kind": "cv", "text": CV})
    assert saved.status_code == 201, saved.text
    cv_id = int(saved.json()["artifact_id"])
    resp = await _event(client, app_id, "cv_reviewed", {"artifact_id": cv_id, "sha256": sha(CV), "confidence": "strong",
                                                       "lines": LINES, "reviewer": "fresh helper"})
    assert resp.status_code == 201, resp.text
    return app_id, cv_id


async def _send_on_review(db: Any, user_id: str, app_id: int, actor: str = AGENT) -> dict[str, Any] | None:
    """The receipt doors' two steps: read the clearance before ``applied``, record it after."""
    clearance = await cv_review.review_clearance(db, user_id, app_id, actor)
    await cv_review.record_sent(db, user_id, app_id, clearance, actor)
    return clearance


async def _turn_on(client: AsyncClient) -> None:
    resp = await client.patch("/api/profile", json={"edits": [{"path": CV_AUTO, "value": True}]})
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_a_strong_cleared_cv_is_listed_with_its_review(authenticated_async_context, fixture_user_id):
    from src.api import dependencies as api_deps

    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        await _turn_on(client)
        app_id, cv_id = await _reviewed_app(client, 1)
        db = await api_deps.get_db()
        clearance = await _send_on_review(db, fixture_user_id, app_id)
        assert clearance is not None and clearance["artifact_id"] == cv_id and clearance["sha256"] == sha(CV)
        body = (await client.get("/api/sent-on-its-own")).json()
        [item] = body["items"]
        assert item["kind"] == "cv" and item["id"] < 0 and item["application_id"] == app_id
        assert (item["job_company"], item["job_title"]) == ("Northwind 1", "Data Engineer 1")
        assert item["question"] == "CV sent after a strong review"
        assert item["answer"] == f"CV version 1 (artifact {cv_id})"
        assert item["sources"] == [
            {"label": "Review", "text": "strong (by fresh helper)"},
            {"label": "Lines covered", "text": "2 of 2 CV lines quoted from your profile"},
            {"label": "Lines checked", "text": "2"},
        ]
        assert item["reported_wrong"] is False
        assert body["cv_sample"] == [item["id"]]
        events = (await client.get(f"/api/applications/{app_id}")).json()["events"]
        assert [e["event_type"] for e in events].count(cv_review.SENT_EVENT) == 1


@pytest.mark.asyncio
async def test_a_cv_the_user_saw_or_clicked_or_without_the_setting_is_not_listed(
    authenticated_async_context, fixture_user_id,
):
    from src.api import dependencies as api_deps

    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        db = await api_deps.get_db()
        off_app, _ = await _reviewed_app(client, 1)
        assert await _send_on_review(db, fixture_user_id, off_app) is None  # setting off: the review cleared nothing
        await _turn_on(client)
        seen_app, _ = await _reviewed_app(client, 2)
        assert (await client.post(f"/api/applications/{seen_app}/cv-seen", json={})).status_code == 201
        assert await _send_on_review(db, fixture_user_id, seen_app) is None  # the user looked
        web_app, _ = await _reviewed_app(client, 3)
        assert await _send_on_review(db, fixture_user_id, web_app, actor="web") is None  # the user's own click
        assert (await client.get("/api/sent-on-its-own")).json() == {"items": [], "cv_sample": []}


@pytest.mark.asyncio
async def test_no_caller_can_forge_the_server_written_events(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        app_id, cv_id = await _reviewed_app(client, 1)
        for event_type in (cv_review.SENT_EVENT, cv_review.WRONG_FACT_EVENT):
            resp = await _event(client, app_id, event_type, {"artifact_id": cv_id})
            assert resp.status_code == 422 and "written by Job360 itself" in resp.text, resp.text
        assert (await client.get("/api/sent-on-its-own")).json()["items"] == []


@pytest.mark.asyncio
async def test_another_users_cv_item_stays_hidden(authenticated_async_context, fixture_user_id):
    from src.api import dependencies as api_deps
    from src.api.main import app
    from src.core import settings
    from src.repositories import pgsync

    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        await _turn_on(client)
        app_id, _ = await _reviewed_app(client, 1)
        await _send_on_review(await api_deps.get_db(), fixture_user_id, app_id)
        [mine] = (await client.get("/api/sent-on-its-own")).json()["items"]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as other:
        creds = {"email": "other@example.com", "password": "s3cretpassword"}
        assert (await other.post("/api/auth/register", json=creds)).status_code == 201
        conn = pgsync.connect(str(settings.DB_PATH))
        conn.execute("UPDATE users SET email_verified_at = ? WHERE email = ?", ("2026-01-01T00:00:00Z", creds["email"]))
        conn.commit()
        conn.close()
        assert (await other.post("/api/auth/login", json=creds)).status_code == 200
        assert (await other.get("/api/sent-on-its-own")).json() == {"items": [], "cv_sample": []}
        assert (await other.post(f"/api/sent-on-its-own/{mine['id']}/wrong-fact")).status_code == 404
    async with authenticated_async_context() as client:
        [still] = (await client.get("/api/sent-on-its-own")).json()["items"]
        assert still["reported_wrong"] is False


@pytest.mark.asyncio
async def test_cv_ids_never_collide_with_linkedin_ids(authenticated_async_context, fixture_user_id):
    """A CV item's id is the negative of its event id; LinkedIn items keep their positive contact_outreach ids."""
    from src.api import dependencies as api_deps

    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        await _turn_on(client)
        db = await api_deps.get_db()
        for n in range(1, 4):
            app_id, _ = await _reviewed_app(client, n)
            await _send_on_review(db, fixture_user_id, app_id)
        items = (await client.get("/api/sent-on-its-own")).json()["items"]
        cur = await db._db.execute(
            "SELECT id FROM application_events WHERE user_id = ? AND event_type = ? ORDER BY id",
            (fixture_user_id, cv_review.SENT_EVENT),
        )
        sent_ids = [int(r["id"]) for r in await cur.fetchall()]
        assert sorted(-i["id"] for i in items) == sent_ids
        assert all(i["id"] < 0 for i in items) and len({i["id"] for i in items}) == 3
        # A LinkedIn item's id is a contact_outreach row id: always > 0, so it can never equal a CV id.
        assert all(-i["id"] > 0 for i in items)


def test_the_daily_sample_is_at_most_five_and_stable_within_a_day():
    day = date(2026, 11, 6)
    ids = [-i for i in range(1, 13)]
    first = sent_alone.daily_sample("u1", ids, day)
    assert len(first) == 5 and set(first) <= set(ids)
    assert sent_alone.daily_sample("u1", list(reversed(ids)), day) == first  # order of the list never matters
    assert sent_alone.daily_sample("u1", ids[:3], day) == sorted(ids[:3])  # fewer than 5: all of them
    assert sent_alone.daily_sample("u1", [], day) == []
    days = {tuple(sent_alone.daily_sample("u1", ids, date(2026, 11, d))) for d in range(1, 11)}
    assert len(days) > 1, "the sample moves with the day"


@pytest.mark.asyncio
async def test_the_route_sample_is_drawn_from_cv_items_for_the_fixed_day(
    authenticated_async_context, fixture_user_id, monkeypatch,
):
    from src.api import dependencies as api_deps

    day = date(2026, 11, 6)  # the fixture's own clock: both the route and the assert read it
    monkeypatch.setattr(sent_alone, "today", lambda: day)
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        await _turn_on(client)
        db = await api_deps.get_db()
        for n in range(1, 8):
            app_id, _ = await _reviewed_app(client, n)
            await _send_on_review(db, fixture_user_id, app_id)
        body = (await client.get("/api/sent-on-its-own")).json()
        ids = [i["id"] for i in body["items"]]
        assert len(ids) == 7 and len(body["cv_sample"]) == 5 and set(body["cv_sample"]) <= set(ids)
        assert body["cv_sample"] == sent_alone.daily_sample(fixture_user_id, ids, day)
        assert (await client.get("/api/sent-on-its-own")).json()["cv_sample"] == body["cv_sample"]


@pytest.mark.asyncio
async def test_wrong_fact_marks_the_item_and_turns_auto_cv_off(authenticated_async_context, fixture_user_id):
    from src.api import dependencies as api_deps

    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        await _turn_on(client)
        assert (await client.get("/api/assistant-settings")).json()["cv_auto_approve"]["effective"] is True
        app_id, _ = await _reviewed_app(client, 1)
        await _send_on_review(await api_deps.get_db(), fixture_user_id, app_id)
        [item] = (await client.get("/api/sent-on-its-own")).json()["items"]
        resp = await client.post(f"/api/sent-on-its-own/{item['id']}/wrong-fact")
        assert resp.status_code == 204, resp.text
        assert (await client.get("/api/assistant-settings")).json()["cv_auto_approve"] == {
            "value": False, "effective": False}
        [after] = (await client.get("/api/sent-on-its-own")).json()["items"]
        assert after["reported_wrong"] is True
        assert (await client.post(f"/api/sent-on-its-own/{item['id']}/wrong-fact")).status_code == 204
        events = (await client.get(f"/api/applications/{app_id}")).json()["events"]
        reports = [e for e in events if e["event_type"] == cv_review.WRONG_FACT_EVENT]
        assert len(reports) == 1 and reports[0]["payload"] == {"sent_event_id": -item["id"]}
        assert reports[0]["recorded_by"] == "web"
        # With the setting off, the next strong review clears nothing.
        next_app, _ = await _reviewed_app(client, 2)
        assert await cv_review.review_clearance(await api_deps.get_db(), fixture_user_id, next_app, AGENT) is None


@pytest.mark.asyncio
async def test_a_bearer_token_cannot_press_wrong_fact(authenticated_async_context, fixture_user_id):
    from src.api import dependencies as api_deps
    from src.api.main import app

    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        await _turn_on(client)
        app_id, _ = await _reviewed_app(client, 1)
        await _send_on_review(await api_deps.get_db(), fixture_user_id, app_id)
        [item] = (await client.get("/api/sent-on-its-own")).json()["items"]
        token = (await client.post("/api/tokens", json={"name": "claude-code"})).json()["token"]
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                               headers={"Authorization": f"Bearer {token}"}) as agent:
            assert (await agent.post(f"/api/sent-on-its-own/{item['id']}/wrong-fact")).status_code == 403
            assert (await agent.get("/api/sent-on-its-own")).status_code == 403
        assert (await client.get("/api/assistant-settings")).json()["cv_auto_approve"]["effective"] is True
        [still] = (await client.get("/api/sent-on-its-own")).json()["items"]
        assert still["reported_wrong"] is False
