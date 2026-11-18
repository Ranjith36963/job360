"""CV review and own browser start links (owner 2026-11-18): hunt -> CV review -> apply.

Two more Ignition duties: ``cv`` (the user's CV review routine, get_recipe("cv")) and ``own_browser``
(their own-browser routine on their computer, get_recipe("own_browser")). A finished hunt and a hunted job queued start ``cv`` and ``reach``;
with no ``cv`` link (or a ``cv`` start that failed) they start the batch as before. A CV run's
server-side end (cv_work: no items left, or the run's 10th new CV reviewed) starts the batch, once.
The routine endpoints are mocked (rule #4); helpers are defined here or copied from the CV review tests.
"""
from __future__ import annotations

import socket
from typing import Any

import pytest
from aioresponses import aioresponses
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient

from tests.test_cv_review import CV, _review, _save_cv, _seed, _stored

LINKS = {
    "hunt": "https://api.anthropic.com/v1/claude_code/routines/trig_HUNT/fire",
    "batch": "https://api.anthropic.com/v1/claude_code/routines/trig_SEND/fire",
    "reach": "https://api.anthropic.com/v1/claude_code/routines/trig_REACH/fire",
    "cv": "https://api.anthropic.com/v1/claude_code/routines/trig_CV/fire",
    "own_browser": "https://api.anthropic.com/v1/claude_code/routines/trig_OWN/fire",
}


@pytest.fixture(autouse=True)
def key(monkeypatch):
    monkeypatch.setenv("BROWSER_SIGN_IN_KEY", Fernet.generate_key().decode())


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    from src.services.fetch import fetcher

    async def resolve(host, port=0, family=socket.AF_UNSPEC):
        return [{"hostname": host, "host": "93.184.216.34", "port": port, "family": socket.AF_INET, "proto": 0,
                 "flags": 0}]

    monkeypatch.setattr(fetcher, "DEFAULT_RESOLVE", resolve)


async def _link(client: AsyncClient, *duties: str) -> None:
    for duty in duties:
        resp = await client.put(f"/api/assistant-duty-triggers/{duty}", json={"url": LINKS[duty], "token": "tok"})
        assert resp.status_code == 200, resp.text


def _mock(mocked, status: dict[str, int] | None = None) -> None:
    for duty, url in LINKS.items():
        mocked.post(url, status=(status or {}).get(duty, 200), payload={}, repeat=True)


def _started(mocked) -> dict[str, list[str]]:
    """{duty: [text, ...]} for every start the mock received."""
    by_url = {v: k for k, v in LINKS.items()}
    out: dict[str, list[str]] = {}
    for (_method, url), calls in mocked.requests.items():
        for call in calls:
            out.setdefault(by_url[str(url)], []).append(call.kwargs["json"]["text"])
    return out


async def _settle() -> None:
    from src.services.browser_agent import duty_trigger

    await duty_trigger.drain()


def _ad(n: int, url: str = "") -> dict[str, Any]:
    return {"title": f"Data Engineer {n}", "company": "Northwind", "location": "London",
            "apply_url": url or f"https://northwind.example/careers/{n}", "description": "Build the pipelines. " * 8}


async def _agent(client: AsyncClient) -> AsyncClient:
    from src.api.main import app

    token = (await client.post("/api/tokens", json={"name": "agent"})).json()["token"]
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"})


async def _slots(user_id: str) -> set[str]:
    import contextlib

    from src.api.dependencies import get_request_db

    async with contextlib.asynccontextmanager(get_request_db)() as db:
        cur = await db._db.execute("SELECT duty FROM assistant_duty_runs WHERE user_id = ?", (user_id,))
        return {str(r[0]) for r in await cur.fetchall()}


# ── the two new start links ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cv_and_own_browser_links_save_test_and_run_now_with_their_recipe(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        listed = (await client.get("/api/assistant-duty-triggers")).json()["duties"]
        assert [(d["duty"], d["label"]) for d in listed][-2:] == [
            ("cv", "CV review"), ("own_browser", "Assistant browser (your Chrome)")]
        await _link(client, "cv", "own_browser")
        with aioresponses() as mocked:
            _mock(mocked)
            tested = (await client.post("/api/assistant-duty-triggers/cv/run", json={"test": True})).json()
            again = (await client.post("/api/assistant-duty-triggers/cv/run")).json()
            own = (await client.post("/api/assistant-duty-triggers/own_browser/run")).json()
            started = _started(mocked)
        assert tested["fired"] is True and again == {**again, "fired": False, "reason": "cooldown"}
        assert own["fired"] is True
        assert len(started["cv"]) == 1 and len(started["own_browser"]) == 1
        cv_text, own_text = started["cv"][0], started["own_browser"][0]
        assert "pressed Test" in cv_text and "CV review" in cv_text and 'get_recipe("cv")' in cv_text
        assert "pressed Run now" in own_text and 'get_recipe("own_browser")' in own_text
        for text in (cv_text, own_text):
            assert "whats_due" not in text and "check_submit" in text  # not a whats_due duty; never a yes
        # Run now for a duty whats_due does not list leaves no run_now slot behind; the own-browser one opens the queue
        assert not {s for s in await _slots(fixture_user_id) if s.startswith("run_now:")} - {"run_now:own_browser"}
        assert (await client.get("/api/own-browser/queue")).json()["run_now_until"]
        view = {d["duty"]: d for d in (await client.get("/api/assistant-duty-triggers")).json()["duties"]}
        assert view["cv"]["last_result"] == "started" and view["own_browser"]["last_result"] == "started"


@pytest.mark.asyncio
async def test_no_token_can_read_change_or_fire_a_cv_or_own_browser_link(authenticated_async_context):
    async with authenticated_async_context() as client:
        async with await _agent(client) as agent:
            for duty in ("cv", "own_browser"):
                assert (await agent.put(f"/api/assistant-duty-triggers/{duty}", json={"url": LINKS[duty]})
                        ).status_code == 403
                assert (await agent.delete(f"/api/assistant-duty-triggers/{duty}")).status_code == 403
                assert (await agent.post(f"/api/assistant-duty-triggers/{duty}/run")).status_code == 403


# ── the chain: hunt -> CV review -> apply ────────────────────────────────────


@pytest.mark.asyncio
async def test_a_finished_hunt_starts_cv_and_reach_not_the_batch(authenticated_async_context):
    async with authenticated_async_context() as client:
        await _link(client, "batch", "reach", "cv")
        with aioresponses() as mocked:
            _mock(mocked)
            assert (await client.post("/api/schedule/done", json={"duty": "hunt"})).status_code == 200
            await _settle()
            started = _started(mocked)
        assert set(started) == {"cv", "reach"}
        assert "a job hunt finished" in started["cv"][0] and "duty `cv`" in started["cv"][0]


@pytest.mark.asyncio
async def test_with_no_cv_link_a_finished_hunt_still_starts_the_batch(authenticated_async_context):
    async with authenticated_async_context() as client:
        await _link(client, "batch", "reach")
        with aioresponses() as mocked:
            _mock(mocked)
            assert (await client.post("/api/schedule/done", json={"duty": "hunt"})).status_code == 200
            await _settle()
            assert set(_started(mocked)) == {"reach", "batch"}


@pytest.mark.asyncio
async def test_a_cv_start_that_fails_falls_back_to_the_batch(authenticated_async_context):
    async with authenticated_async_context() as client:
        await _link(client, "batch", "reach", "cv")
        with aioresponses() as mocked:
            _mock(mocked, {"cv": 500})
            assert (await client.post("/api/schedule/done", json={"duty": "hunt"})).status_code == 200
            await _settle()
            started = _started(mocked)
        assert set(started) == {"cv", "reach", "batch"} and len(started["batch"]) == 1
        assert "a job hunt finished" in started["batch"][0]


@pytest.mark.asyncio
async def test_a_hunted_job_queued_starts_cv_and_reach_or_the_batch_without_a_cv_link(authenticated_async_context):
    async with authenticated_async_context() as client:
        await _link(client, "batch", "reach", "cv")
        with aioresponses() as mocked:
            _mock(mocked)
            hunted = await client.post("/api/jobs/bring", json={**_ad(1), "queue": True, "origin": "ai_hunt"})
            assert hunted.status_code == 200 and hunted.json()["queued"] is True, hunted.text
            await _settle()
            started = _started(mocked)
        app_id = hunted.json()["application_id"]
        assert set(started) == {"cv", "reach"}
        assert f"Applications: {app_id}." in started["cv"][0] and "a new job was queued" in started["cv"][0]
        assert (await client.delete("/api/assistant-duty-triggers/cv")).json() == {"deleted": True}
        with aioresponses() as mocked:
            _mock(mocked)
            again = await client.post("/api/jobs/bring", json={**_ad(2), "queue": True, "origin": "ai_hunt"})
            assert again.status_code == 200, again.text
            await _settle()
            assert set(_started(mocked)) == {"batch"}  # reach started a moment ago (cool-down); batch stands in for cv


# ── a CV run's end starts the batch, once ────────────────────────────────────


@pytest.mark.asyncio
async def test_a_cv_run_with_no_items_left_starts_the_batch_once(authenticated_async_context):
    async with authenticated_async_context() as client:
        await _link(client, "batch")
        async with await _agent(client) as agent:
            with aioresponses() as mocked:
                _mock(mocked)
                # a later page (no run begun by this call) ends nothing
                assert (await agent.get("/api/applications/cv-work", params={"include_profile": False})).json()[
                    "total_needing_work"] == 0
                await _settle()
                assert _started(mocked) == {}
                # the run's first call (include_profile, no cursor) finds no work: the run is over
                assert (await agent.get("/api/applications/cv-work")).status_code == 200
                await _settle()
                started = _started(mocked)
                assert set(started) == {"batch"} and len(started["batch"]) == 1
                assert "a CV review run finished" in started["batch"][0]
                # a second run ending inside the cool-down never starts a second batch
                assert (await agent.get("/api/applications/cv-work")).status_code == 200
                await _settle()
                assert len(_started(mocked)["batch"]) == 1


@pytest.mark.asyncio
async def test_a_cv_run_that_reviews_its_target_starts_the_batch_once(
    authenticated_async_context, fixture_user_id, monkeypatch
):
    from src.services.applications import cv_run

    monkeypatch.setattr(cv_run, "TARGET", 1)
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        await _link(client, "batch")
        brought = await client.post("/api/jobs/bring", json={**_ad(7), "description": "Python, Snowflake. " * 6})
        app_id = int(brought.json()["application_id"])
        assert (await client.post(f"/api/applications/{app_id}/events",
                                  json={"event_type": "queued", "payload": {}})).status_code == 201
        with aioresponses() as mocked:
            _mock(mocked)
            got = (await client.get("/api/applications/cv-work")).json()  # the run begins: one job needs a CV
            assert got["total_needing_work"] == 1
            await _settle()
            assert _started(mocked) == {}
            cv_id = await _save_cv(client, app_id, CV)
            await _stored(client, app_id, _review(cv_id, CV, confidence="weak"))  # the run's 1st new CV, reviewed
            await _settle()
            started = _started(mocked)
            assert set(started) == {"batch"} and "a CV review run finished" in started["batch"][0]
            await _stored(client, app_id, _review(cv_id, CV, confidence="weak"))  # a re-review: the run is over
            await _settle()
            assert len(_started(mocked)["batch"]) == 1


# ── the own browser ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_send_on_a_linkedin_card_and_own_browser_run_now_start_the_own_browser_routine(authenticated_async_context):
    async with authenticated_async_context() as client:
        await _link(client, "own_browser")
        app_id = int((await client.post("/api/jobs/bring", json=_ad(9, "https://careers.example/c/9"))).json()[
            "application_id"])
        added = await client.post(f"/api/applications/{app_id}/contacts", json={
            "name": "Priya", "role": "Recruiter", "linkedin_url": "https://www.linkedin.com/in/priya"})
        cid = int(added.json()["contact"]["id"])
        for text in ("Hi", "Hello"):
            msg = await client.post(f"/api/applications/{app_id}/artifacts", json={
                "kind": "outreach", "text": text, "contact_id": cid, "channel": "linkedin"})
            assert msg.status_code == 201, msg.text
        mid = int((await client.get(f"/api/people?contact_id={cid}")).json()["person"]["outreach"]["messages"][-1][
            "id"])
        with aioresponses() as mocked:
            _mock(mocked)
            declined = await client.post(f"/api/contacts/{cid}/own-browser-send",
                                         json={"message_id": mid, "decision": "decline"})
            assert declined.status_code == 200, declined.text
            await _settle()
            assert _started(mocked) == {}  # "Don't send" starts nothing
        msg = await client.post(f"/api/applications/{app_id}/artifacts", json={
            "kind": "outreach", "text": "Hi again", "contact_id": cid, "channel": "linkedin"})
        mid = int(msg.json()["artifact_id"])
        with aioresponses() as mocked:
            _mock(mocked)
            sent = await client.post(f"/api/contacts/{cid}/own-browser-send",
                                     json={"message_id": mid, "decision": "approve"})
            assert sent.status_code == 200, sent.text
            await _settle()
            started = _started(mocked)
        assert set(started) == {"own_browser"} and "pressed Send on a LinkedIn message" in started["own_browser"][0]
        assert "Priya" not in started["own_browser"][0] and "Hi again" not in started["own_browser"][0]  # fixed words only


@pytest.mark.asyncio
async def test_run_my_own_browser_tasks_now_opens_the_queue_and_starts_the_routine(authenticated_async_context):
    async with authenticated_async_context() as client:
        with aioresponses() as mocked:
            _mock(mocked)
            assert (await client.post("/api/own-browser/run-now")).status_code == 200  # no link: the window only
            await _settle()
            assert _started(mocked) == {}
        await _link(client, "own_browser")
        with aioresponses() as mocked:
            _mock(mocked)
            ran = await client.post("/api/own-browser/run-now")
            assert ran.status_code == 200 and ran.json()["minutes"] > 0, ran.text
            await _settle()
            started = _started(mocked)
        assert set(started) == {"own_browser"} and len(started["own_browser"]) == 1
        assert "Run my assistant-browser tasks now" in started["own_browser"][0]
