"""GET /api/needs-you-count - the menu badge's exact total (owner 2026-10-27).

Real Postgres, real HTTP doors. The number must be the Needs-you page's own total
(``countNeedsYou`` in the frontend): distinct JOBS with anything open + the OTHER items. Pinned:
* each kind counts once, in the right bucket, with a real seeded row (value asserts, rule #21);
* one job with an ask AND a ready card AND a CV to approve counts ONCE;
* withdrawn, answered and practice (simulation) asks, sent notes and another user's rows do not count;
* the total equals the page's rules re-run over the page's own HTTP reads (parity);
* session only, and it writes no audit row (a navigation must never move /api/changes).
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.core import settings
from src.repositories import pgsync

FORM = "https://careers.example/apply/7"


def _sql(statement: str, params: tuple = ()) -> list[Any]:
    with pgsync.connect(str(settings.DB_PATH)) as conn:
        cur = conn.execute(statement, params)
        rows = cur.fetchall() if cur.description else []
        conn.commit()
    return rows


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _bring(client: AsyncClient, n: int = 1) -> int:
    ad = {
        "title": f"Engineer {n}", "company": f"Company{n}", "location": "London", "country": "GB", "remote": False,
        "apply_url": f"https://co{n}.example/careers/{n}", "description": "Build things.",
    }
    resp = await client.post("/api/jobs/bring", json=ad)
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _count(client: AsyncClient) -> dict[str, int]:
    resp = await client.get("/api/needs-you-count")
    assert resp.status_code == 200, resp.text
    assert resp.headers["cache-control"] == "no-store"
    return resp.json()


async def _ask(client: AsyncClient, app_id: int | None, question: str = "Notice period?", **extra: Any) -> int:
    body: dict[str, Any] = {"question": question, **extra}
    if app_id is not None:
        body["application_id"] = app_id
    resp = await client.post("/api/asks", json=body)
    assert resp.status_code == 201, resp.text
    return int(resp.json()["id"])


async def _ready(client: AsyncClient, app_id: int) -> None:
    resp = await client.post(
        f"/api/applications/{app_id}/events",
        json={"event_type": "form_filled", "payload": {"form_url": FORM, "fields_count": 4, "answers": []}},
    )
    assert resp.status_code == 201, resp.text


async def _cv(client: AsyncClient, app_id: int) -> None:
    resp = await client.post(f"/api/applications/{app_id}/artifacts", json={"kind": "cv", "text": "Ada Lovelace"})
    assert resp.status_code == 201, resp.text


async def _person(client: AsyncClient, name: str, app_id: int | None, draft: bool = True, sent: bool = False) -> int:
    url = "/api/contacts" if app_id is None else f"/api/applications/{app_id}/contacts"
    resp = await client.post(url, json={"name": name, "company": "Acme", "found_via": "event"})
    assert resp.status_code in (200, 201), resp.text
    cid = int(resp.json()["contact"]["id"] if "contact" in resp.json() else resp.json()["id"])
    if draft:
        msg = await client.post(
            f"/api/contacts/{cid}/outreach", json={"entry": "message", "channel": "linkedin", "text": f"Hi {name}"}
        )
        assert msg.status_code == 201, msg.text
    if sent:
        done = await client.post(f"/api/contacts/{cid}/outreach", json={"entry": "sent", "channel": "linkedin"})
        assert done.status_code == 201, done.text
    return cid


async def _suggest(client: AsyncClient, app_id: int, mid: str = "<s-1@co.example>") -> None:
    assert (await client.post(f"/api/applications/{app_id}/events", json={"event_type": "applied"})).status_code == 201
    src = {"message_id": mid, "sender": "hr@co.example", "subject": "Update", "received_at": "2026-10-02T08:00:00+00:00"}
    resp = await client.post(
        f"/api/applications/{app_id}/events",
        json={"event_type": "outcome_suggested", "source": src,
              "payload": {"event_type": "rejected", "quote": "We will not proceed.", "why": "maybe"}},
    )
    assert resp.status_code == 201, resp.text


def _waiting_request(user_id: str, path: str = "assistant_settings.daily_cap") -> None:
    _sql(
        "INSERT INTO assistant_setting_requests (user_id, path, value, requested_by, requested_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (user_id, path, "20", "token:agent", _now()),
    )


def _mail(user_id: str, mid: str = "msg-1") -> None:
    _sql(
        "INSERT INTO inbox_items (user_id, message_id, recorded_by, created_at) VALUES (?, ?, ?, ?)",
        (user_id, mid, "token:agent", _now()),
    )


@pytest.mark.asyncio
async def test_nothing_open_is_zero(authenticated_async_context):
    async with authenticated_async_context() as client:
        await _bring(client)  # a job alone is not something that needs you
        assert await _count(client) == {"n": 0, "jobs": 0, "other": 0}


@pytest.mark.asyncio
async def test_every_kind_counts_once_in_its_bucket(authenticated_async_context):
    async with authenticated_async_context() as client:
        user_id = authenticated_async_context.fixture_user_id
        job_ask, job_ready, job_cv, job_person, job_sugg = [await _bring(client, n) for n in range(1, 6)]

        await _ask(client, job_ask)
        assert await _count(client) == {"n": 1, "jobs": 1, "other": 0}
        await _ready(client, job_ready)
        assert (await _count(client))["jobs"] == 2
        await _cv(client, job_cv)
        assert (await _count(client))["jobs"] == 3
        await _person(client, "Rita", job_person)
        assert (await _count(client))["jobs"] == 4, "a drafted, unsent note to a person on a job"

        await _ask(client, None, "General question?")
        assert await _count(client) == {"n": 5, "jobs": 4, "other": 1}
        _waiting_request(user_id)
        assert (await _count(client))["other"] == 2
        await _suggest(client, job_sugg)  # applied + a suggestion: the job itself is not "open"
        assert await _count(client) == {"n": 7, "jobs": 4, "other": 3}
        _mail(user_id)
        assert (await _count(client))["other"] == 4
        await _person(client, "Cold Cleo", None)
        assert await _count(client) == {"n": 9, "jobs": 4, "other": 5}


@pytest.mark.asyncio
async def test_one_job_with_an_ask_a_ready_card_and_a_cv_counts_once(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        await _ask(client, app_id)
        await _cv(client, app_id)
        await _ready(client, app_id)
        await _person(client, "Rita", app_id)
        assert (await client.get("/api/ready-to-send")).json()["total"] == 1
        assert len((await client.get("/api/cv-to-approve")).json()["items"]) == 1
        assert await _count(client) == {"n": 1, "jobs": 1, "other": 0}


@pytest.mark.asyncio
async def test_withdrawn_answered_and_practice_asks_do_not_count(authenticated_async_context):
    async with authenticated_async_context() as client:
        job_a, job_b = await _bring(client, 1), await _bring(client, 2)
        withdrawn = await _ask(client, job_a, "Withdrawn one?")
        answered = await _ask(client, None, "Answered one?")
        await _ask(client, None, "SIMULATION run: fill this in")
        await _ask(client, job_b, "Lifecycle test question")
        assert (await client.post(f"/api/asks/{withdrawn}/withdraw", json={})).status_code == 200
        assert (await client.post(f"/api/asks/{answered}/answer", json={"answer": "yes"})).status_code == 200
        assert await _count(client) == {"n": 0, "jobs": 0, "other": 0}


@pytest.mark.asyncio
async def test_a_sent_note_does_not_count_but_a_newer_draft_does(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        done_job = await _person(client, "Sent Sam", app_id, sent=True)
        await _person(client, "Sent Cold", None, sent=True)
        await _person(client, "No Draft", None, draft=False)
        assert await _count(client) == {"n": 0, "jobs": 0, "other": 0}
        # A new draft after the send is waiting again.
        again = await client.post(
            f"/api/contacts/{done_job}/outreach",
            json={"entry": "message", "channel": "linkedin", "text": "A follow up", "occurred_at": (datetime.now(timezone.utc) + timedelta(seconds=200)).isoformat()},
        )
        assert again.status_code == 201, again.text
        assert await _count(client) == {"n": 1, "jobs": 1, "other": 0}


@pytest.mark.asyncio
async def test_a_skipped_note_does_not_count_until_a_newer_draft(authenticated_async_context):
    """#1342 saves a skip as a `skipped` ledger row; the badge must agree with the page that hides them."""
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        on_job = await _person(client, "Skip Sam", app_id)
        cold = await _person(client, "Skip Cold", None)
        assert await _count(client) == {"n": 2, "jobs": 1, "other": 1}
        for cid in (on_job, cold):
            done = await client.post(f"/api/contacts/{cid}/outreach", json={"entry": "skipped", "channel": "linkedin"})
            assert done.status_code == 201, done.text
        assert await _count(client) == {"n": 0, "jobs": 0, "other": 0}
        # A newer draft for the same person brings them back, as on the page.
        again = await client.post(
            f"/api/contacts/{cold}/outreach", json={"entry": "message", "channel": "linkedin", "text": "A new note"}
        )
        assert again.status_code == 201, again.text
        assert await _count(client) == {"n": 1, "jobs": 0, "other": 1}


@pytest.mark.asyncio
async def test_another_users_rows_are_not_counted(authenticated_async_context):
    from fastapi.testclient import TestClient

    from src.api.main import app

    async with authenticated_async_context() as client:
        mine = await _bring(client)
        await _ask(client, mine)
        sync_client = TestClient(app)
        email = "needs-you-stranger@example.com"
        assert sync_client.post("/api/auth/register", json={"email": email, "password": "s3cretpassword"}).status_code == 201
        _sql("UPDATE users SET email_verified_at = ? WHERE email = ?", ("2026-01-01T00:00:00Z", email))
        lr = sync_client.post("/api/auth/login", json={"email": email, "password": "s3cretpassword"})
        stranger_id = str(_sql("SELECT id FROM users WHERE email = ?", (email,))[0][0])
        cookies = {"job360_session": str(lr.cookies.get("job360_session"))}
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", cookies=cookies) as other:
            theirs = await _bring(other, 2)
            await _ask(other, theirs)
            await _ask(other, None, "Their general question")
            await _ready(other, theirs)
            await _cv(other, theirs)
            await _person(other, "Their Cold", None)
            _waiting_request(stranger_id)
            _mail(stranger_id)
            assert await _count(other) == {"n": 5, "jobs": 1, "other": 4}
        assert await _count(client) == {"n": 1, "jobs": 1, "other": 0}


@pytest.mark.asyncio
async def test_the_total_equals_the_pages_rules_over_the_pages_own_reads(authenticated_async_context):
    """Parity: the page's countNeedsYou rules, run here over the same HTTP reads the page makes."""
    async with authenticated_async_context() as client:
        user_id = authenticated_async_context.fixture_user_id
        jobs = [await _bring(client, n) for n in range(1, 8)]
        await _ask(client, jobs[0])
        await _ask(client, jobs[1], "Second")
        await _ready(client, jobs[1])
        await _cv(client, jobs[2])
        await _ready(client, jobs[3])
        await _person(client, "Rita", jobs[4])
        await _person(client, "Sent Sam", jobs[5], sent=True)
        await _ask(client, None, "General")
        await _person(client, "Cold Cleo", None)
        await _person(client, "Cold Cora", None)
        _waiting_request(user_id)
        _mail(user_id)
        await _suggest(client, jobs[6])

        sim = re.compile(r"\bsimulation\b|lifecycle test|do not apply", re.IGNORECASE)
        asks = [a for a in (await client.get("/api/asks")).json()["asks"]
                if not any(sim.search(str(t or "")) for t in (a["question"], a["context"], a["job_title"], a["job_company"]))]
        people = (await client.get("/api/people")).json()["people"]
        unsent = [p for p in people if p["message_count"] > 0 and not p["last_sent"]]
        job_ids = (
            {a["application_id"] for a in asks if a["application_id"] is not None}
            | {c["application_id"] for c in (await client.get("/api/ready-to-send")).json()["items"]}
            | {r["application_id"] for r in (await client.get("/api/cv-to-approve")).json()["items"]}
            | {j["application_id"] for p in unsent for j in p["jobs"]}
        )
        other = (
            len([a for a in asks if a["application_id"] is None])
            + len((await client.get("/api/assistant-settings")).json()["waiting"])
            + len((await client.get("/api/applications/outcome-suggestions")).json()["suggestions"])
            + len((await client.get("/api/inbox-items")).json()["items"])
            + len([p for p in unsent if not p["jobs"]])
        )
        expected = {"n": len(job_ids) + other, "jobs": len(job_ids), "other": other}
        assert expected["jobs"] == 5 and expected["other"] == 6, expected  # the seed really produced each kind
        assert await _count(client) == expected


@pytest.mark.asyncio
async def test_session_only_and_it_writes_no_audit_row(authenticated_async_context):
    from src.api.main import app
    from src.utils import audit_trail

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        assert (await anon.get("/api/needs-you-count")).status_code == 401
    async with authenticated_async_context() as client:
        user_id = authenticated_async_context.fixture_user_id
        token = (await client.post("/api/tokens", json={"name": "agent"})).json()["token"]
        before = await client.get("/api/changes")
        audit_trail.flush()
        rows = _sql("SELECT COUNT(*) FROM audit_log WHERE user_id = ?", (user_id,))[0][0]
        await _count(client)
        audit_trail.flush()
        assert _sql("SELECT COUNT(*) FROM audit_log WHERE user_id = ?", (user_id,))[0][0] == rows
        assert (await client.get("/api/changes")).json()["cursor"] == before.json()["cursor"]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                           headers={"Authorization": f"Bearer {token}"}) as agent:
        resp = await agent.get("/api/needs-you-count")
        assert resp.status_code == 403 and resp.json()["detail"] == "session_required"
