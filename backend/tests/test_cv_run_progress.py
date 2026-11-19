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


@pytest.mark.asyncio
async def test_a_first_shaped_cv_work_read_mid_run_keeps_the_runs_count(
    authenticated_async_context, fixture_user_id
):
    """CV run 05:33 (2026-11-19): "N of 10 stored strong" restarted at 1 after each record call and the run never
    saw "CV run complete". A cv_work read with no cursor and include_profile true in the middle of an open run
    called begin() again, which moved the run's start to now, so every CV stored strong before it stopped counting.
    A run begun under RUN_MINUTES ago is the same run: such a read continues it."""
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        for n in range(5):
            await _queued(client, 40 + n)
        async with await _agent(client) as agent:
            page = (await agent.get("/api/applications/cv-work", params={"limit": 2})).json()  # the run begins
            first = [i["application_id"] for i in page["items"]]
            cvs = [await _save_cv(client, a, CV) for a in first]
            batch = await agent.post("/api/applications/record-events", json={"events": [
                {"application_id": a, "event_type": "cv_reviewed", "payload": _review(c, CV)}
                for a, c in zip(first, cvs)]})
            assert [e["event"]["next"].split(".")[0] for e in batch.json()["events"]] == [
                "CV run: 1 of 10 stored strong", "CV run: 2 of 10 stored strong"]
            again = (await agent.get("/api/applications/cv-work", params={"limit": 2})).json()  # first-shaped, mid-run
            assert again["next"].startswith("CV run: 2 of 10 stored strong so far.")
            third = next(i["application_id"] for i in again["items"] if i["application_id"] not in first)
            cv3 = await _save_cv(client, third, CV)
            reply = await _review_reply(agent, third, cv3)
            assert reply["next"].startswith("CV run: 3 of 10 stored strong. Not done.")


@pytest.mark.asyncio
async def test_a_first_read_after_run_minutes_begins_a_new_run(authenticated_async_context, fixture_user_id):
    """The next hourly run still starts fresh: a slot older than RUN_MINUTES is replaced by its first read."""
    from src.api import dependencies
    from src.services.applications import cv_run

    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        for n in range(3):
            await _queued(client, 60 + n)
        async with await _agent(client) as agent:
            page = (await agent.get("/api/applications/cv-work", params={"limit": 1})).json()
            app_id = page["items"][0]["application_id"]
            cv_id = await _save_cv(client, app_id, CV)
            assert (await _review_reply(agent, app_id, cv_id))["next"].startswith("CV run: 1 of 10")
            db = await dependencies.get_db()
            await db._db.execute(
                "UPDATE assistant_duty_runs SET done_at = ? WHERE user_id = ? AND duty = ?",
                ("2000-01-01T00:00:00+00:00", fixture_user_id, cv_run.SLOT))
            await db._db.commit()
            fresh = (await agent.get("/api/applications/cv-work", params={"limit": 1})).json()
            assert fresh["next"].startswith("CV run: 0 of 10 stored strong so far.")
