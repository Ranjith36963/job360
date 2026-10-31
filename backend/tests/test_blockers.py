"""blockers_for: what waits on whom, per application (slice 3 of the blockers work).

Real Postgres, real HTTP doors to seed, the service called directly to read. Pinned:
* a CAPTCHA ask and its blocked event are ONE site_step on `you`; a block with no ask is still listed;
* a private answer needs the user's answer; a guess needs an OK; an expired yes reads Send again;
* a note is hidden before the application is sent, on a rejected job and below the score line;
* the order, and a fixed number of queries whatever the number of ids; another user's id is ignored.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from httpx import AsyncClient

from src.api import dependencies as api_deps
from src.core import settings
from src.repositories import pg, pgsync
from src.services.applications import blockers, kit, spine

FORM = "https://careers.example/apply/7"


def _sql(statement: str, params: tuple = ()) -> None:
    with pgsync.connect(str(settings.DB_PATH)) as conn:
        conn.execute(statement, params)
        conn.commit()


async def _bring(client: AsyncClient, n: int = 1) -> int:
    ad = {
        "title": f"Engineer {n}", "company": f"Company{n}", "location": "London", "country": "GB", "remote": False,
        "apply_url": f"https://co{n}.example/careers/{n}", "description": "Build things.",
    }
    resp = await client.post("/api/jobs/bring", json=ad)
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _event(client: AsyncClient, app_id: int, event_type: str, payload: dict[str, Any] | None = None) -> int:
    resp = await client.post(f"/api/applications/{app_id}/events", json={"event_type": event_type, "payload": payload or {}})
    assert resp.status_code == 201, resp.text
    return int(resp.json()["id"]) if "id" in resp.json() else 0


async def _blockers(user_id: str, ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    db = await api_deps.get_db()
    return await blockers.blockers_for(db, user_id, ids)  # type: ignore[return-value]


def _kinds(rows: list[dict[str, Any]]) -> list[str]:
    return [b["kind"] for b in rows]


async def _person(client: AsyncClient, app_id: int, name: str = "Rita") -> int:
    resp = await client.post(f"/api/applications/{app_id}/contacts", json={"name": name, "company": "Acme", "found_via": "event"})
    assert resp.status_code in (200, 201), resp.text
    body = resp.json()
    cid = int(body["contact"]["id"] if "contact" in body else body["id"])
    msg = await client.post(f"/api/contacts/{cid}/outreach", json={"entry": "message", "channel": "linkedin", "text": "Hi"})
    assert msg.status_code == 201, msg.text
    return cid


@pytest.fixture(autouse=True)
def _line(monkeypatch):
    async def fifty(_user_id: str) -> int:
        return 50

    monkeypatch.setattr(spine, "user_score_line", fifty)


def _above(app_id: int, score: int = 90) -> None:
    _sql("UPDATE applications SET fit_score = ?, fit_recorded_at = ? WHERE id = ?", (score, datetime.now(timezone.utc).isoformat(), app_id))


@pytest.mark.asyncio
async def test_captcha_ask_and_blocked_event_are_one_site_step_on_you(authenticated_async_context):
    async with authenticated_async_context() as client:
        uid = authenticated_async_context.fixture_user_id
        app_id = await _bring(client)
        await _event(client, app_id, "blocked", {"reason": "captcha", "step": "open_form"})
        rows = (await _blockers(uid, [app_id]))[app_id]
        site = [b for b in rows if b["kind"] == "site_step"]
        assert len(site) == 1 and site[0]["actor"] == "you" and site[0]["label"] == "solve the CAPTCHA"
        assert site[0]["ref"]["ask_id"] and site[0]["ref"]["event_id"]
        assert rows[0]["kind"] == "site_step"


@pytest.mark.asyncio
async def test_a_blocked_event_with_no_ask_is_still_a_blocker(authenticated_async_context):
    async with authenticated_async_context() as client:
        uid = authenticated_async_context.fixture_user_id
        app_id = await _bring(client)
        await _event(client, app_id, "blocked", {"reason": "login_needed"})
        asks = (await client.get("/api/asks")).json()["asks"]
        for ask in asks:
            assert (await client.post(f"/api/asks/{ask['id']}/withdraw", json={})).status_code == 200
        site = [b for b in (await _blockers(uid, [app_id]))[app_id] if b["kind"] == "site_step"]
        assert len(site) == 1 and site[0]["actor"] == "you" and site[0]["label"] == "sign in"
        assert "ask_id" not in site[0]["ref"] and site[0]["ref"]["event_id"]


async def _ask(client: AsyncClient, app_id: int, source: str, key: str, question: str, answer: str = "Yes") -> None:
    body = {"application_id": app_id, "question": "Form questions", "kind": "question",
            "questions": [{"key": key, "question": question, "source": source, "answer": answer}]}
    assert (await client.post("/api/asks", json=body)).status_code == 201


@pytest.mark.asyncio
async def test_private_answer_is_answer_and_guess_is_ok(authenticated_async_context):
    async with authenticated_async_context() as client:
        uid = authenticated_async_context.fixture_user_id
        private, guessed = await _bring(client, 1), await _bring(client, 2)
        await _ask(client, private, "memory", "equality.gender", "What is your gender?", "Male")
        await _ask(client, guessed, "guess", "referred", "Were you referred by an employee?", "No")
        got = await _blockers(uid, [private, guessed])
        assert [(b["kind"], b["label"], b["actor"]) for b in got[private] if b["kind"] == "answers"] == [("answers", "answer 1 question", "you")]
        assert [(b["kind"], b["label"]) for b in got[guessed] if b["kind"] == "answers"] == [("answers", "OK 1 answer")]


@pytest.mark.asyncio
async def test_a_yes_older_than_its_time_to_live_reads_send_again(authenticated_async_context):
    async with authenticated_async_context() as client:
        uid = authenticated_async_context.fixture_user_id
        app_id = await _bring(client)
        _above(app_id)
        cv = await _client_cv(client, app_id)
        await _event(client, app_id, "form_filled", {"form_url": FORM, "fields_count": 3, "answers": []})
        assert [b["label"] for b in (await _blockers(uid, [app_id]))[app_id] if b["kind"] == "send_yes"] == ["Send"]
        await _event(client, app_id, "submit_approved", {"artifact_id": cv, "sha256": kit.sha256_text("Ada Lovelace"), "where": "web"})
        rows = (await _blockers(uid, [app_id]))[app_id]
        assert "send_yes" not in _kinds(rows) and "check form" in [b["label"] for b in rows]
        old = (datetime.now(timezone.utc) - timedelta(minutes=settings.SUBMIT_APPROVAL_TTL_MINUTES + 5)).isoformat()
        _sql("UPDATE application_events SET recorded_at = ? WHERE application_id = ? AND event_type = 'submit_approved'", (old, app_id))
        assert [b["label"] for b in (await _blockers(uid, [app_id]))[app_id] if b["kind"] == "send_yes"] == ["Send again (your yes expired)"]


@pytest.mark.asyncio
async def test_notes_wait_for_the_application_and_skip_dead_and_below_line_jobs(authenticated_async_context):
    async with authenticated_async_context() as client:
        uid = authenticated_async_context.fixture_user_id
        sent, early, dead, low = [await _bring(client, n) for n in range(1, 5)]
        for app_id in (sent, early, dead, low):
            await _person(client, app_id, f"P{app_id}")
        for app_id in (sent, dead, low):
            _above(app_id, 10 if app_id == low else 90)
        _sql("UPDATE applications SET status = 'applied' WHERE id IN (?, ?)", (sent, low))
        _sql("UPDATE applications SET status = 'rejected' WHERE id = ?", (dead,))
        got = await _blockers(uid, [sent, early, dead, low])
        notes = lambda i: [b for b in got[i] if b["kind"] == "outreach_send"]  # noqa: E731
        assert [(b["actor"], b["label"]) for b in notes(sent)] == [("you", "send 1 note")]
        assert notes(early) == [] and notes(dead) == [] and notes(low) == []
        assert got[dead] == []


@pytest.mark.asyncio
async def test_order_and_another_users_id_is_ignored(authenticated_async_context):
    async with authenticated_async_context() as client:
        uid = authenticated_async_context.fixture_user_id
        app_id = await _bring(client)
        _above(app_id)
        await _client_cv(client, app_id)
        await _ask(client, app_id, "guess", "referred", "Were you referred by an employee?", "No")
        await _event(client, app_id, "form_filled", {"form_url": FORM, "fields_count": 3, "answers": []})
        await _event(client, app_id, "blocked", {"reason": "otp_needed"})
        got = (await _blockers(uid, [app_id]))[app_id]
        assert _kinds(got) == ["site_step", "approve_cv", "answers", "send_yes"]
        assert [b["label"] for b in got] == ["enter the code", "approve the CV", "OK 1 answer", "Send"]
        assert await _blockers("someone-else", [app_id]) == {}


async def _client_cv(client: AsyncClient, app_id: int) -> int:
    resp = await client.post(f"/api/applications/{app_id}/artifacts", json={"kind": "cv", "text": "Ada Lovelace"})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return int(body["artifact_id"])


@pytest.mark.asyncio
async def test_query_count_does_not_grow_with_the_number_of_ids(authenticated_async_context, monkeypatch):
    async with authenticated_async_context() as client:
        uid = authenticated_async_context.fixture_user_id
        ids = [await _bring(client, n) for n in range(1, 6)]
        for i in ids:
            await _client_cv(client, i)
            await _event(client, i, "blocked", {"reason": "captcha"})
        calls: list[str] = []
        original = pg.Connection.execute

        async def counting(self, sql, params=()):
            calls.append(sql)
            return await original(self, sql, params)

        monkeypatch.setattr(pg.Connection, "execute", counting)
        await _blockers(uid, ids[:1])
        one = len(calls)
        calls.clear()
        got = await _blockers(uid, ids)
        assert len(calls) == one, "five ids must cost the same queries as one"
        assert one <= 16 and all(_kinds(got[i])[0] == "site_step" for i in ids)


@pytest.mark.asyncio
async def test_application_detail_and_list_carry_blockers_next_and_more(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        await _client_cv(client, app_id)
        await _event(client, app_id, "blocked", {"reason": "captcha"})
        detail = (await client.get(f"/api/applications/{app_id}")).json()
        assert detail["next"]["kind"] == "site_step" and detail["next"]["actor"] == "you"
        assert detail["more"] == len(detail["blockers"]) - 1 and detail["more"] >= 1
        row = next(a for a in (await client.get("/api/applications")).json()["applications"] if a["id"] == app_id)
        assert row["next"] == detail["next"] and row["blockers"] == detail["blockers"]


@pytest.mark.asyncio
async def test_an_old_cleared_block_does_not_leak_into_a_new_ask_only_site_step(authenticated_async_context):
    async with authenticated_async_context() as client:
        uid = authenticated_async_context.fixture_user_id
        app_id = await _bring(client)
        await _event(client, app_id, "blocked", {"reason": "captcha"})
        await _event(client, app_id, "unblocked", {"resolution": "user_did_it"})
        body = {"application_id": app_id, "question": "Sign in to continue", "kind": "login"}
        ask = await client.post("/api/asks", json=body)
        assert ask.status_code == 201, ask.text
        site = [b for b in (await _blockers(uid, [app_id]))[app_id] if b["kind"] == "site_step"]
        assert len(site) == 1 and site[0]["label"] == "sign in"
        assert "event_id" not in site[0]["ref"] and site[0]["ref"]["ask_id"] == ask.json()["id"]
        assert site[0]["since"] == str(ask.json()["asked_at"])
