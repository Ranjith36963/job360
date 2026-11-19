"""A CV run's review replies hand back the next step (2026-11-19).

The 03:33 CV run stored 4 strong, then ended its turn after one batch of 5 with ~181 items left, although
cv.md says to keep fetching pages until 10 are stored strong. Now each ``cv_reviewed`` reply of an open CV run
(record_event, and each item of record_events) carries ``next``: the strong count and the exact next
``cv_work`` call, or "CV run complete". Web callers and users with no open run get no ``next``.
"""
from __future__ import annotations

from typing import Any

import pytest

from tests._cv_review import blank_ad
from tests.test_cv_own_browser_start_links import _ad, _agent
from tests.test_cv_review import CV, _review, _save_cv, _seed


async def _queued(client: Any, n: int) -> int:
    brought = await client.post("/api/jobs/bring", json={**_ad(n), "description": "Python, Snowflake. " * 6})
    app_id = int(brought.json()["application_id"])
    queued = await client.post(f"/api/applications/{app_id}/events", json={"event_type": "queued", "payload": {}})
    assert queued.status_code == 201, queued.text
    await blank_ad(app_id)  # no ad text = jd_no_ad, so the test's review can be stored strong
    return app_id


async def _review_reply(agent: Any, app_id: int, cv_id: int) -> dict[str, Any]:
    resp = await agent.post(f"/api/applications/{app_id}/events",
                            json={"event_type": "cv_reviewed", "payload": _review(cv_id, CV)})
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_an_open_cv_run_reply_says_not_done_then_complete(
    authenticated_async_context, fixture_user_id, monkeypatch
):
    from src.services.applications import cv_run

    monkeypatch.setattr(cv_run, "TARGET", 2)
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        first, second = await _queued(client, 31), await _queued(client, 32)
        async with await _agent(client) as agent:
            page = (await agent.get("/api/applications/cv-work", params={"limit": 1})).json()  # the run begins
            assert page["total_needing_work"] == 2
            last = page["items"][-1]["application_id"]
            assert page["next"].startswith("CV run: 0 of 2 stored strong so far. Keep going")
            other = second if last == first else first
            cv_a = await _save_cv(client, last, CV)
            reply = await _review_reply(agent, last, cv_a)
            assert reply["next"] == (f"CV run: 1 of 2 stored strong. Not done. Call cv_work(limit=5, after={last}, "
                                     "include_profile=false) now and work that page.")
            cv_b = await _save_cv(client, other, CV)
            batch = await agent.post("/api/applications/record-events", json={"events": [
                {"application_id": other, "event_type": "cv_reviewed", "payload": _review(cv_b, CV)}]})
            assert batch.status_code == 200, batch.text
            item = batch.json()["events"][0]
            assert item["ok"], item
            assert item["event"]["next"] == "CV run complete: 2 stored strong. Write the summary."


@pytest.mark.asyncio
async def test_no_open_cv_run_or_a_web_caller_gets_no_next(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        app_id = await _queued(client, 33)
        cv_id = await _save_cv(client, app_id, CV)
        async with await _agent(client) as agent:
            assert (await _review_reply(agent, app_id, cv_id)).get("next") is None  # no run begun
            assert (await agent.get("/api/applications/cv-work")).status_code == 200  # the run begins
        web = await client.post(f"/api/applications/{app_id}/events",
                                json={"event_type": "cv_reviewed", "payload": _review(cv_id, CV)})
        assert web.status_code == 201, web.text
        assert web.json().get("next") is None  # the web app's own review never gets the run's next step
