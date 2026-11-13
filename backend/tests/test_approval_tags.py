"""Who approved what ("AI reviewed" / "You approved", owner 2026-11-13): approvals_for reads stored rows only.

Real doors for the births (HTTP) and for the server-only rows (the spine append the routes use); VALUES asserted
(rule #21). ``you`` needs a ``recorded_by = 'web'`` row, ``ai`` needs an agent row, no record means no tag. The
expected ``at`` comes from the fixture's own stored ``recorded_at``, never a second now(). Helpers are copied."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

import pytest
from httpx import AsyncClient

AGENT = "token:claude-code"
CV = "Ada Lovelace\nEXPERIENCE\n- Data engineer at Northwind since 2019.\n"
FORM = "https://careers.northwind.example/apply/7"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def _db():
    from src.api import dependencies as api_deps

    return await api_deps.get_db()


async def _bring(client: AsyncClient, n: int) -> int:
    resp = await client.post("/api/jobs/bring", json={
        "title": f"Engineer {n}", "company": f"Company{n}", "location": "London", "country": "GB",
        "apply_url": f"https://co{n}.example/careers/{n}", "description": "Build things.",
    })
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _add(user_id: str, app_id: int, event_type: str, payload: dict[str, Any], by: str = "web") -> int:
    from src.services.applications import spine

    ev = await spine.append_event(
        await _db(), user_id=user_id, application_id=app_id, event_type=event_type, payload=payload,
        occurred_at=datetime.now(timezone.utc).isoformat(), recorded_by=by,
    )
    return int(ev["event_id"])


async def _recorded_at(event_id: int) -> str:
    cur = await (await _db())._db.execute("SELECT recorded_at FROM application_events WHERE id = ?", (event_id,))
    return str((await cur.fetchone())["recorded_at"])


async def _cv(client: AsyncClient, app_id: int, text: str = CV) -> int:
    saved = await client.post(f"/api/applications/{app_id}/artifacts", json={"kind": "cv", "text": text})
    assert saved.status_code == 201, saved.text
    return int(saved.json()["artifact_id"])


async def _fill(user_id: str, app_id: int, source: str = "guessed", answer: str = "x", by: str = AGENT) -> int:
    answers = [{"question": "Why us?", "answer": answer, "source": source, "key": "why"}]
    return await _add(user_id, app_id, "form_filled", {"form_url": FORM, "answers": answers}, by)


async def _read(user_id: str, *app_ids: int) -> dict[int, list[dict[str, Any]]]:
    from src.services.applications import approval

    return await approval.approvals_for(await _db(), user_id, list(app_ids))


def _pairs(got: dict[int, list[dict[str, Any]]], app_id: int) -> set[tuple[str, str]]:
    return {(i["by"], i["what"]) for i in got.get(app_id, [])}


async def _person(client: AsyncClient, app_id: int) -> int:
    added = await client.post(f"/api/applications/{app_id}/contacts", json={"name": "Priya", "role": "Recruiter"})
    assert added.status_code in (200, 201), added.text
    return int(added.json()["contact"]["id"])


async def _row(user_id: str, cid: int, entry: str, channel: str = "linkedin", text: str = "", by: str = AGENT) -> int:
    db = await _db()
    stamp = datetime.now(timezone.utc).isoformat()
    cur = await db._db.execute(
        "INSERT INTO contact_outreach (user_id, contact_id, entry, channel, text, occurred_at, recorded_at, recorded_by) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?) RETURNING id", (user_id, cid, entry, channel, text, stamp, stamp, by))
    row = await cur.fetchone()
    await db._db.commit()
    return int(row[0])


# ---- CV -------------------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_cv_web_mark_of_the_current_cv_is_you(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id = await _bring(client, 1)
        cv_id = await _cv(client, app_id)
        ev = await _add(fixture_user_id, app_id, "cv_seen", {"artifact_id": cv_id, "sha256": _sha(CV)})
        got = await _read(fixture_user_id, app_id)
    assert got[app_id] == [{"by": "you", "what": "cv", "at": await _recorded_at(ev), "ref": ev, "ref_kind": "event", "reviewer": None}]


@pytest.mark.asyncio
async def test_cv_sent_on_review_is_ai_with_the_reviewer_the_review_names(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id = await _bring(client, 2)
        cv_id = await _cv(client, app_id)
        review = await _add(fixture_user_id, app_id, "cv_reviewed", {"artifact_id": cv_id, "reviewer": "fresh helper"}, AGENT)
        sent = await _add(fixture_user_id, app_id, "cv_sent_on_review", {"artifact_id": cv_id, "review_event_id": review}, AGENT)
        got = await _read(fixture_user_id, app_id)
    assert got[app_id] == [{"by": "ai", "what": "cv", "at": await _recorded_at(sent), "ref": sent, "ref_kind": "event", "reviewer": "fresh helper"}]


@pytest.mark.asyncio
async def test_cv_chat_or_claimed_web_or_older_version_is_not_you(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id = await _bring(client, 3)
        old = await _cv(client, app_id)
        await _add(fixture_user_id, app_id, "cv_seen", {"artifact_id": old, "sha256": _sha(CV)})  # web, but of v1
        await _cv(client, app_id, CV + "- Built ETL pipelines.\n")  # v2 is current now
        chat = await _bring(client, 4)
        cv2 = await _cv(client, chat)
        # an agent claims the web in its payload: the recorded_by column is the agent's, so it is not the owner's click
        await _add(fixture_user_id, chat, "cv_seen", {"artifact_id": cv2, "sha256": _sha(CV), "where": "web", "by": "web"}, AGENT)
        await _add(fixture_user_id, chat, "cv_seen", {"artifact_id": cv2, "sha256": _sha(CV), "where": "chat"}, AGENT)
        got = await _read(fixture_user_id, app_id, chat)
    assert got == {}


# ---- outreach ---------------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_outreach_web_send_for_that_message_is_you(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id = await _bring(client, 5)
        cid = await _person(client, app_id)
        mid = await _row(fixture_user_id, cid, "message", text="Hi Priya")
        await _row(fixture_user_id, cid, "send_approved", text=str(mid), by="web")
        sent = await _row(fixture_user_id, cid, "sent")
        got = await _read(fixture_user_id, app_id)
    assert [(i["by"], i["what"], i["ref"], i["ref_kind"]) for i in got[app_id]] == [("you", "outreach", sent, "outreach")]


@pytest.mark.asyncio
async def test_outreach_auto_request_without_a_click_is_ai(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id = await _bring(client, 6)
        cid = await _person(client, app_id)
        await _row(fixture_user_id, cid, "message", text="Hi Priya")
        invite = await _row(fixture_user_id, cid, "send_released", text="invited")
        got = await _read(fixture_user_id, app_id)
    assert [(i["by"], i["what"], i["ref"]) for i in got[app_id]] == [("ai", "outreach", invite)]


@pytest.mark.asyncio
async def test_outreach_approval_of_another_message_is_not_you_and_a_manual_mark_is_nothing(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id = await _bring(client, 7)
        cid = await _person(client, app_id)
        await _row(fixture_user_id, cid, "message", text="Hi Priya")
        await _row(fixture_user_id, cid, "send_approved", text="99999", by="web")
        agent_sent = await _row(fixture_user_id, cid, "sent")
        manual = await _bring(client, 8)
        cid2 = await _person(client, manual)
        await _row(fixture_user_id, cid2, "message", text="Hi again")
        await _row(fixture_user_id, cid2, "sent", by="web")
        got = await _read(fixture_user_id, app_id, manual)
    assert [(i["by"], i["ref"]) for i in got[app_id]] == [("ai", agent_sent)]  # the agent's send had no click on it
    assert manual not in got  # the owner's own manual mark is neither tag


# ---- submit -----------------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_submit_web_yes_on_the_newest_fill_is_you(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id = await _bring(client, 9)
        fill = await _fill(fixture_user_id, app_id, source="memory")
        yes = await _add(fixture_user_id, app_id, "submit_approved", {"form_filled_event_id": fill, "artifact_id": 1})
        got = await _read(fixture_user_id, app_id)
    assert ("you", "submit", yes) in {(i["by"], i["what"], i["ref"]) for i in got[app_id]}


@pytest.mark.asyncio
async def test_submit_yes_before_a_newer_fill_is_not_you_and_agent_applied_is_ai(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id = await _bring(client, 10)
        first = await _fill(fixture_user_id, app_id, answer="a", source="memory")
        await _add(fixture_user_id, app_id, "submit_approved", {"form_filled_event_id": first, "artifact_id": 1})
        await _fill(fixture_user_id, app_id, answer="b", source="memory")  # a refill: the yes was for the old form
        applied = await _add(fixture_user_id, app_id, "applied", {}, AGENT)
        got = await _read(fixture_user_id, app_id)
        manual = await _bring(client, 11)
        await _add(fixture_user_id, manual, "applied", {}, "web")
        got2 = await _read(fixture_user_id, manual)
    assert _pairs(got, app_id) == {("ai", "submit")}
    assert [i["ref"] for i in got[app_id] if i["what"] == "submit"] == [applied]
    assert got2 == {}  # the owner's manual applied, with no yes event: no tag


# ---- answers ----------------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_answers_web_verify_is_you_guess_without_decision_is_ai_memory_is_none(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        verified, guessed, saved = await _bring(client, 12), await _bring(client, 13), await _bring(client, 14)
        fill = await _fill(fixture_user_id, verified)
        ok = await _add(fixture_user_id, verified, "answer_verified", {"question": "Why us?", "key": "why", "form_filled_event_id": fill})
        gfill = await _fill(fixture_user_id, guessed)
        await _fill(fixture_user_id, saved, source="memory")
        got = await _read(fixture_user_id, verified, guessed, saved)
    assert [(i["by"], i["what"], i["ref"]) for i in got[verified]] == [("you", "answers", ok)]
    assert [(i["by"], i["what"], i["ref"]) for i in got[guessed]] == [("ai", "answers", gfill)]
    assert saved not in got


@pytest.mark.asyncio
async def test_chat_approval_and_chat_verified_answers_get_no_ai_tag(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id = await _bring(client, 40)
        fill = await _fill(fixture_user_id, app_id)  # a guessed answer, filled by the agent
        await _add(fixture_user_id, app_id, "submit_approved", {"form_filled_event_id": fill, "where": "chat"}, AGENT)
        await _add(fixture_user_id, app_id, "answer_verified", {"question": "Why us?", "key": "why", "form_filled_event_id": fill}, AGENT)
        await _add(fixture_user_id, app_id, "applied", {}, AGENT)
        got = await _read(fixture_user_id, app_id)
    assert got == {}  # the owner decided in chat: not "AI reviewed", and not "You approved" either (no web click)


# ---- no record, no leak, no write -----------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_no_records_other_user_and_simulation_give_nothing_and_a_read_writes_nothing(authenticated_async_context, fixture_user_id):
    from src.core import settings

    async with authenticated_async_context() as client:
        empty = await _bring(client, 15)
        test_app = await _bring(client, 16)
        await _add(fixture_user_id, test_app, settings.SIMULATION_EVENT_TYPE, {"note": "test"})
        await _fill(fixture_user_id, test_app)
        real = await _bring(client, 17)
        await _fill(fixture_user_id, real)
        db = await _db()
        before = int((await (await db._db.execute("SELECT COUNT(*) FROM application_events")).fetchone())[0])
        mine = await _read(fixture_user_id, empty, test_app, real)
        theirs = await _read("someone-else", empty, test_app, real)
        after = int((await (await db._db.execute("SELECT COUNT(*) FROM application_events")).fetchone())[0])
    assert set(mine) == {real} and theirs == {} and before == after


# ---- the surfaces ---------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_sent_on_its_own_item_carries_the_ai_tag_and_a_read_changes_no_count(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id = await _bring(client, 20)
        cid = await _person(client, app_id)
        await _row(fixture_user_id, cid, "message", text="Hi Priya")
        invite = await _row(fixture_user_id, cid, "send_released", text="invited")
        count_before = (await client.get("/api/needs-you-count")).json()
        body = (await client.get("/api/sent-on-its-own")).json()
        count_after = (await client.get("/api/needs-you-count")).json()
    item = next(i for i in body["items"] if i["id"] == invite)
    assert item["kind"] == "linkedin_request"
    assert item["approval"] == {"by": "ai", "what": "outreach", "at": item["sent_at"], "ref": invite, "ref_kind": "outreach", "reviewer": None}
    assert count_before == count_after


@pytest.mark.asyncio
async def test_sent_on_its_own_cv_item_names_the_reviewer_and_the_detail_route_lists_the_approvals(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id = await _bring(client, 21)
        cv_id = await _cv(client, app_id)
        review = await _add(fixture_user_id, app_id, "cv_reviewed", {"artifact_id": cv_id, "reviewer": "fresh helper", "confidence": "strong"}, AGENT)
        sent = await _add(fixture_user_id, app_id, "cv_sent_on_review", {"artifact_id": cv_id, "review_event_id": review}, AGENT)
        items = (await client.get("/api/sent-on-its-own")).json()["items"]
        detail = (await client.get(f"/api/applications/{app_id}")).json()
    cv_item = next(i for i in items if i["id"] == -sent)
    assert (cv_item["approval"]["by"], cv_item["approval"]["what"], cv_item["approval"]["reviewer"]) == ("ai", "cv", "fresh helper")
    assert [(a["by"], a["what"], a["ref"]) for a in detail["approvals"]] == [("ai", "cv", sent)]
