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
LINES = [  # one row per review line (rules 5): {n, verdict, evidence}
    {"n": 1, "verdict": "supported", "evidence": ["Data engineer at Northwind since 2019."]},
    {"n": 2, "verdict": "supported", "evidence": ["Built ETL pipelines in Python and Snowflake, cutting warehouse costs 30%."]},
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


async def _reviewed_app(
    client: AsyncClient, n: int, url: str = "https://northwind.example/careers",
) -> tuple[int, int]:
    """One application whose current CV has a stored strong review (verified by Job360)."""
    brought = await client.post("/api/jobs/bring", json={
        "title": f"Data Engineer {n}", "company": f"Northwind {n}", "location": "London", "country": "GB",
        "remote": False, "apply_url": f"{url}/{n}", "description": "Python, Snowflake.",
    })
    assert brought.status_code == 200, brought.text
    app_id = int(brought.json()["application_id"])
    saved = await client.post(f"/api/applications/{app_id}/artifacts", json={"kind": "cv", "text": CV})
    assert saved.status_code == 201, saved.text
    cv_id = int(saved.json()["artifact_id"])
    for etype, payload in (("form_checked", {"questions_count": 1, "answered": 1, "missing": 0}),
                           ("form_filled", {"form_url": FORM, "fields_count": 1}),
                           ("form_swept", {"fields_checked": 1, "fixed": 0})):
        assert (await _event(client, app_id, etype, payload)).status_code == 201
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


def test_the_daily_sample_is_at_most_five_and_the_same_for_the_same_pool():
    day = date(2026, 11, 6)
    ids = [-i for i in range(1, 13)]
    first = sent_alone.daily_sample("u1", ids, day)
    assert len(first) == 5 and set(first) <= set(ids)
    assert sent_alone.daily_sample("u1", list(reversed(ids)), day) == first  # order of the list never matters
    assert sent_alone.daily_sample("u1", ids[:3], day) == sorted(ids[:3])  # fewer than 5: all of them
    assert sent_alone.daily_sample("u1", [], day) == []
    days = {tuple(sent_alone.daily_sample("u1", ids, date(2026, 11, d))) for d in range(1, 11)}
    assert len(days) > 1, "the sample moves with the day"


def test_a_new_item_keeps_every_earlier_pick_that_still_ranks_in_the_lowest_five():
    """Each id's rank is fixed for the user and day, so adding item N+1 only ever replaces the pick it outranks."""
    day = date(2026, 11, 6)

    def rank(i: int) -> str:
        return hashlib.sha256(f"u1|{day.isoformat()}|{i}".encode()).hexdigest()

    for n in range(5, 30):
        before = sent_alone.daily_sample("u1", [-i for i in range(1, n + 1)], day)
        grown = [-i for i in range(1, n + 2)]
        after = sent_alone.daily_sample("u1", grown, day)
        lowest = set(sorted(grown, key=rank)[:5])
        assert set(after) == lowest
        assert {p for p in before if p in lowest} <= set(after)
        assert len(set(before) - set(after)) <= 1  # at most the one pick the new item outranks
        if -(n + 1) not in after:
            assert after == before


@pytest.mark.asyncio
async def test_the_route_sample_is_drawn_from_unreported_cv_items_for_the_fixed_day(
    authenticated_async_context, fixture_user_id, monkeypatch,
):
    from src.api import dependencies as api_deps

    day = date(2026, 11, 6)  # the fixture's own clock: both the route and the assert read it

    async def fixed_day(_db: Any, _user_id: str) -> date:
        return day

    monkeypatch.setattr(sent_alone, "today", fixed_day)
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
        picked = body["cv_sample"][0]
        assert (await client.post(f"/api/sent-on-its-own/{picked}/wrong-fact")).status_code == 204
        after = (await client.get("/api/sent-on-its-own")).json()["cv_sample"]
        assert picked not in after
        assert after == sent_alone.daily_sample(fixture_user_id, [i for i in ids if i != picked], day)


@pytest.mark.asyncio
async def test_the_sample_day_is_the_users_own_day(authenticated_async_context, fixture_user_id):
    from src.api import dependencies as api_deps
    from src.services.applications import spine

    async with authenticated_async_context():
        db = await api_deps.get_db()
        assert await sent_alone.today(db, fixture_user_id) == await spine.user_today(db, fixture_user_id)


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


@pytest.mark.asyncio
async def test_reports_never_push_cv_items_out_of_the_cap(authenticated_async_context, fixture_user_id, monkeypatch):
    from src.api import dependencies as api_deps

    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        await _turn_on(client)
        db = await api_deps.get_db()
        for n in (1, 2):
            app_id, _ = await _reviewed_app(client, n)
            await _send_on_review(db, fixture_user_id, app_id)
        for item in (await client.get("/api/sent-on-its-own")).json()["items"]:
            assert (await client.post(f"/api/sent-on-its-own/{item['id']}/wrong-fact")).status_code == 204
        monkeypatch.setattr(sent_alone, "_LIMIT", 1)  # the cap counts CV items, never the reports
        [newest] = (await client.get("/api/sent-on-its-own")).json()["items"]
        assert newest["job_company"] == "Northwind 2" and newest["reported_wrong"] is True


# ── The real doors (owner 2026-11-06): an assistant's first `applied` over HTTP ────────────────────────────


def _bearer(token: str) -> AsyncClient:
    from src.api.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"})


async def _door(client: AsyncClient, agent: AsyncClient, door: str, app_id: int):
    if door == "events":
        return await _event(agent, app_id, "applied", {})
    if door == "receipt":
        return await agent.post(f"/api/applications/{app_id}/receipt", json={"channel": "company_site"})
    job_id = int((await client.get(f"/api/applications/{app_id}")).json()["job_id"])
    return await agent.post(f"/api/receipts/{job_id}", json={"channel": "company_site"})


async def _sent_events(client: AsyncClient, app_id: int) -> list[dict[str, Any]]:
    events = (await client.get(f"/api/applications/{app_id}")).json()["events"]
    return [e for e in events if e["event_type"] == cv_review.SENT_EVENT]


@pytest.mark.asyncio
@pytest.mark.parametrize("door", ["events", "receipt", "legacy_receipt"])
async def test_each_applied_door_records_exactly_one_cv_sent_on_review(
    authenticated_async_context, fixture_user_id, door,
):
    from tests._clearance import clear

    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        token = (await client.post("/api/tokens", json={"name": "claude-code"})).json()["token"]
        async with _bearer(token) as agent:
            off_app, _ = await _reviewed_app(client, 1)  # setting off: the review clears nothing, so 409
            await clear(fixture_user_id, off_app)
            refused = await _door(client, agent, door, off_app)
            assert refused.status_code == 409 and "cv_not_approved" in refused.text, refused.text
            assert await _sent_events(client, off_app) == []
            await _turn_on(client)
            app_id, cv_id = await _reviewed_app(client, 2)
            await clear(fixture_user_id, app_id)
            resp = await _door(client, agent, door, app_id)
            assert resp.status_code == 201, resp.text
            [sent] = await _sent_events(client, app_id)
            assert (sent["payload"]["artifact_id"], sent["payload"]["sha256"]) == (cv_id, sha(CV))
            assert sent["recorded_by"] == "token:claude-code"
            [item] = (await client.get("/api/sent-on-its-own")).json()["items"]
            assert item["id"] == -sent["id"] and item["application_id"] == app_id
            if door == "events":  # a repeat `applied` is a confirmation note, never a second CV item
                assert (await _event(agent, app_id, "applied", {})).status_code == 201
            else:  # a second receipt on an applied application is no new send
                await clear(fixture_user_id, app_id)
                await _door(client, agent, door, app_id)
            assert len(await _sent_events(client, app_id)) == 1
            seen_app, _ = await _reviewed_app(client, 3)  # the user looked at this CV: not sent on a review
            assert (await client.post(f"/api/applications/{seen_app}/cv-seen", json={})).status_code == 201
            await clear(fixture_user_id, seen_app)
            assert (await _door(client, agent, door, seen_app)).status_code == 201
            assert await _sent_events(client, seen_app) == []


@pytest.mark.asyncio
async def test_the_email_proof_path_records_the_cv_sent_on_review(authenticated_async_context, fixture_user_id):
    """auto_applied.apply_if_proven: the employer's confirmation email marks it applied (by ``system``)."""
    from datetime import datetime, timezone

    from tests._clearance import clear

    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        await _turn_on(client)
        token = (await client.post("/api/tokens", json={"name": "claude-code"})).json()["token"]
        app_id, cv_id = await _reviewed_app(client, 1, url="https://boards.greenhouse.io/northwind/jobs")
        async with _bearer(token) as agent:
            assert (await _event(agent, app_id, "submit_attempted", {})).status_code == 201
            await clear(fixture_user_id, app_id)
            email = {"kind": "email", "message_id": "<confirm-1@mailer.example>",
                     "sender": "Northwind 1 via Greenhouse <no-reply@greenhouse-mail.io>",
                     "subject": "Thank you for applying to Northwind 1",
                     "received_at": datetime.now(timezone.utc).isoformat()}
            resp = await agent.post(f"/api/applications/{app_id}/events", json={
                "event_type": "note", "payload": {}, "detail": f"Submission confirmed: {email['subject']}",
                "source": email})
            assert resp.status_code == 201 and resp.json()["status"] == "applied", resp.text
        [sent] = await _sent_events(client, app_id)
        assert sent["recorded_by"] == "system" and sent["payload"]["artifact_id"] == cv_id
