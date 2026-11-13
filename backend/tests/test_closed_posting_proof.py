"""A closed posting needs proof, and an unproven one is re-checked once (production 2026-11-13: apps 998, 146).

Apps 998 and 146 sat on Needs you as "posting closed: check it, then mark resolved" (asks 578, 580): the
agents recorded ``blocked`` {reason: posting_closed} with no ``evidence``, so ``auto_close.close_if_proven``
answered ``no_proof``, the queue skipped the job, and nothing ever looked at it again. App 146 was taken off
the queue as ``closed_posting`` and an agent queued it again: five more CVs. Pinned here:
* ``posting_closed`` without ``evidence`` is refused (422 naming the three values and the ad's own site);
* a ``posting_closed`` block opens no ask (the blocker alone shows; ``auto_close`` closes it when proven);
* an old unproven block lets the job back into the queue ONCE, so the next run re-checks it with proof;
* a job unqueued as ``closed_posting`` is queued again only by the user;
* the user's own Needs-you read withdraws an open unproven posting_closed ask.
Helpers are defined here, never imported from another test module.
"""
from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.routes import browser as browser_routes
from src.services.applications import asks as asks_service
from src.services.applications import spine
from src.services.profile.models import CVData, UserPreferences, UserProfile

HOST = "northwind.example"
AGENT = "token:claude-code"


def _seed(user_id: str) -> None:
    from src.services.profile.storage import save_profile

    save_profile(UserProfile(cv_data=CVData(raw_text="Python.", name="Ada"),
                             preferences=UserPreferences(target_job_titles=["Data Engineer"])),
                 user_id, source_action="cv_upload")


async def _agent(web: AsyncClient) -> AsyncClient:
    from src.api.main import app

    resp = await web.post("/api/tokens", json={"name": "claude-code"})
    assert resp.status_code == 201, resp.text
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers={"Authorization": f"Bearer {resp.json()['token']}"})


async def _job(web: AsyncClient, n: int, *, queue: bool = True) -> int:
    resp = await web.post("/api/jobs/bring", json={
        "title": f"Data Engineer {n}", "company": f"Northwind {n}", "location": "Remote", "queue": queue,
        "apply_url": f"https://{HOST}/careers/{n}", "description": "Build the pipelines. " * 8,
    })
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _event(client: AsyncClient, app_id: int, event_type: str, payload: dict[str, Any]) -> Any:
    return await client.post(f"/api/applications/{app_id}/events", json={"event_type": event_type, "payload": payload})


async def _legacy_block(user_id: str, app_id: int, *, evidence: str = "", ask: bool = False) -> int:
    """The production shape of blocks 8275 / 8470: written before proof was required, so straight into the
    spine (no door accepts it now). With ``ask`` it also carries the plain ask it opened then. Returns the ask id."""
    ask_id = 0
    async with browser_routes._db() as db:  # noqa: SLF001
        if ask:
            row = await asks_service.create_ask(
                db, user_id, app_id, "Your assistant got stuck: a closed posting on northwind.example.", "", AGENT)
            ask_id = int(row["id"])
        payload: dict[str, Any] = {"reason": "posting_closed", "step": "open the ad", "page_host": HOST,
                                   "detail": "HTTP 404", "assistant": AGENT, "ask_id": ask_id or None}
        if evidence:
            payload["evidence"] = evidence
        await spine.append_event(db, user_id=user_id, application_id=app_id, event_type="blocked", payload=payload,
                                 occurred_at=spine.parse_occurred_at(None), recorded_by=AGENT)
    return ask_id


async def _stop_batch(client: AsyncClient) -> Any:
    return await client.post("/api/schedule/done", json={"duty": "batch", "outcome": "partial", "reason": "needs_user"})


async def _open_asks(web: AsyncClient, app_id: int) -> list[dict[str, Any]]:
    asks = (await web.get("/api/asks")).json()["asks"]
    return [a for a in asks if a.get("application_id") == app_id and a["status"] == "open"]


@pytest.mark.asyncio
async def test_posting_closed_without_evidence_is_refused(authenticated_async_context):
    async with authenticated_async_context() as web:
        app_id = await _job(web, 1, queue=False)
        async with await _agent(web) as agent:
            resp = await _event(agent, app_id, "blocked", {"reason": "posting_closed", "page_host": HOST})
        assert resp.status_code == 422, resp.text
        for word in ("page_says_closed", "http_404", "http_410", "page_host must be the ad's own site"):
            assert word in resp.text
        events = (await web.get(f"/api/applications/{app_id}")).json()["events"]
        assert [e for e in events if e["event_type"] == "blocked"] == []


@pytest.mark.asyncio
async def test_a_closed_posting_opens_no_ask_and_the_blocker_alone_shows(authenticated_async_context):
    async with authenticated_async_context() as web:
        app_id = await _job(web, 2, queue=False)
        async with await _agent(web) as agent:
            # Proof read on another site: auto_close keeps it for the user (not_the_ad_page).
            resp = await _event(agent, app_id, "blocked", {"reason": "posting_closed", "page_host": "mail.google.com",
                                                            "evidence": "page_says_closed"})
        assert resp.status_code == 201 and resp.json()["status"] == "considering", resp.text
        assert await _open_asks(web, app_id) == []
        count = (await web.get("/api/needs-you-count")).json()
        assert count["job_ids_by_kind"]["posting_closed"] == [app_id]


@pytest.mark.asyncio
async def test_an_unproven_closed_block_goes_back_to_the_queue_once(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as web:
        _seed(fixture_user_id)
        app_id = await _job(web, 3)
        await web.post(f"/api/applications/{app_id}/artifacts", json={"kind": "cv", "text": "Ada, engineer."})
        await _legacy_block(fixture_user_id, app_id)
        refused = await _stop_batch(web)
        assert refused.status_code == 409, refused.text
        assert f"next: application {app_id} (Northwind 3)" in refused.json()["detail"]
        # The run looked at the page again (a form step after the block): its one re-check is used.
        async with browser_routes._db() as db:  # noqa: SLF001
            await spine.append_event(db, user_id=fixture_user_id, application_id=app_id, event_type="form_checked",
                                     payload={}, occurred_at=spine.parse_occurred_at(None), recorded_by=AGENT)
        assert (await _stop_batch(web)).status_code == 200


@pytest.mark.asyncio
async def test_the_re_check_with_proof_closes_it(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as web:
        _seed(fixture_user_id)
        app_id = await _job(web, 4)
        await _legacy_block(fixture_user_id, app_id)
        async with await _agent(web) as agent:
            resp = await _event(agent, app_id, "blocked", {"reason": "posting_closed", "page_host": HOST,
                                                            "evidence": "http_404"})
        assert resp.status_code == 201 and resp.json()["status"] == "withdrawn", resp.text
        assert (await _stop_batch(web)).status_code == 200


@pytest.mark.asyncio
async def test_a_proven_closed_block_stays_out_of_the_queue(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as web:
        _seed(fixture_user_id)
        app_id = await _job(web, 5)
        await _legacy_block(fixture_user_id, app_id, evidence="http_404")
        assert (await _stop_batch(web)).status_code == 200


@pytest.mark.asyncio
async def test_closed_posting_unqueue_is_undone_only_by_the_user(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as web:
        app_id = await _job(web, 6)
        async with await _agent(web) as agent:
            off = await _event(agent, app_id, "unqueued", {"reason": "closed_posting"})
            assert off.status_code == 201, off.text
            again = await _event(agent, app_id, "queued", {})
            assert again.status_code == 409 and "closed posting" in again.text, again.text
        async with browser_routes._db() as db:  # noqa: SLF001
            queued, note = await spine.queue_if_open(db, fixture_user_id, app_id, recorded_by=AGENT)
            assert queued is False and note and "closed posting" in note
        mine = await _event(web, app_id, "queued", {})
        assert mine.status_code == 201, mine.text


@pytest.mark.asyncio
async def test_the_users_read_withdraws_an_unproven_closed_ask(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as web:
        unproven, proven = await _job(web, 7, queue=False), await _job(web, 8, queue=False)
        ask_id = await _legacy_block(fixture_user_id, unproven, ask=True)
        kept_id = await _legacy_block(fixture_user_id, proven, evidence="http_410", ask=True)
        assert await _open_asks(web, unproven) == []
        assert [a["id"] for a in await _open_asks(web, proven)] == [kept_id]
        async with browser_routes._db() as db:  # noqa: SLF001
            cur = await db._db.execute(  # noqa: SLF001
                "SELECT withdrawn_reason FROM application_asks WHERE id = ? AND user_id = ?",
                (ask_id, fixture_user_id))
            assert (await cur.fetchone())[0] == "Job360 is re-checking the posting"


async def _correcting(client: AsyncClient, app_id: int, event_type: str, payload: dict[str, Any], eid: int) -> Any:
    return await client.post(f"/api/applications/{app_id}/events",
                             json={"event_type": event_type, "payload": payload, "corrects_event_id": eid})


@pytest.mark.asyncio
async def test_an_agent_cannot_correct_its_way_past_a_closed_unqueue(authenticated_async_context, fixture_user_id):
    """Checker 2026-11-13: a ``queued`` (or a reason-less ``unqueued``) that corrects the ``closed_posting``
    unqueue retracted it and put the job back on the queue with no user action."""
    async with authenticated_async_context() as web:
        app_id = await _job(web, 9)
        async with await _agent(web) as agent:
            off = await _event(agent, app_id, "unqueued", {"reason": "closed_posting"})
            assert off.status_code == 201, off.text
            eid = int(off.json()["event_id"])
            back = await _correcting(agent, app_id, "queued", {}, eid)
            assert back.status_code == 409 and "closed posting" in back.text, back.text
            hide = await _correcting(agent, app_id, "unqueued", {}, eid)
            assert hide.status_code == 409 and "closed posting" in hide.text, hide.text
            again = await _event(agent, app_id, "queued", {})
            assert again.status_code == 409 and "closed posting" in again.text, again.text
        async with browser_routes._db() as db:  # noqa: SLF001
            assert app_id not in await spine.queued_at_by_app(db, fixture_user_id, [app_id])
        mine = await _correcting(web, app_id, "queued", {}, eid)
        assert mine.status_code == 201, mine.text

