"""2026-11-03 live run: own_browser_queue release(contact:57, weekly_limit) answered released:true and the very next
take handed contact:57 straight back, so a run that takes until empty looped. Now any release keeps that person out
of take for own_browser.RELEASE_BACKOFF_MINUTES, and weekly_limit parks every LinkedIn invite until
own_browser.weekly_limit_until (seven days). Real rows through the real routes; helpers copied.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.core import settings
from src.repositories import pgsync
from src.services.applications import own_browser

_AD = {
    "title": "Data Engineer", "company": "Northwind", "location": "London", "country": "GB", "remote": False,
    "apply_url": "https://northwind.example/careers/7", "description": "Build the pipelines.",
}


# ── helpers (copied, never imported from another test module) ────────────────


async def _person(client: AsyncClient, n: int) -> tuple[int, int]:
    """A contact with one LinkedIn draft the user clicked Send on; returns (contact_id, message_id)."""
    resp = await client.post(
        "/api/jobs/bring", json={**_AD, "title": f"Engineer {n}", "apply_url": f"https://northwind.example/c/{n}"}
    )
    assert resp.status_code == 200, resp.text
    app_id = int(resp.json()["application_id"])
    added = await client.post(
        f"/api/applications/{app_id}/contacts",
        json={"name": f"Priya {n}", "role": "Recruiter", "linkedin_url": f"https://www.linkedin.com/in/priya{n}"},
    )
    assert added.status_code == 201, added.text
    contact_id = int(added.json()["contact"]["id"])
    resp = await client.post(
        f"/api/applications/{app_id}/artifacts",
        json={"kind": "outreach", "text": "Hi, a note", "contact_id": contact_id, "channel": "linkedin"},
    )
    assert resp.status_code == 201, resp.text
    message_id = int((await client.get(f"/api/people?contact_id={contact_id}")).json()["person"]["outreach"]
                     ["messages"][-1]["id"])
    await _click(client, contact_id, message_id)
    return contact_id, message_id


async def _click(client: AsyncClient, contact_id: int, message_id: int) -> None:
    resp = await client.post(
        f"/api/contacts/{contact_id}/own-browser-send", json={"message_id": message_id, "decision": "approve"}
    )
    assert resp.status_code == 200, resp.text


async def _agent(client: AsyncClient) -> AsyncClient:
    from src.api.main import app

    token = (await client.post("/api/tokens", json={"name": "agent"})).json()["token"]
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"})


async def _take(agent: AsyncClient) -> dict[str, Any]:
    resp = await agent.post("/api/own-browser/take", json={"session": "s1"})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _release(agent: AsyncClient, contact_id: int, reason: str) -> None:
    body = {"session": "s1", "task_id": f"contact:{contact_id}", "reason": reason}
    resp = await agent.post("/api/own-browser/release", json=body)
    assert resp.status_code == 200, resp.text


def _age_rows(contact_id: int, minutes: int) -> None:
    """Move this person's outreach rows ``minutes`` into the past (the backoff and the 120 s pace both elapse)."""
    conn = pgsync.connect(str(settings.DB_PATH))
    conn.execute(
        "UPDATE contact_outreach SET recorded_at = ? WHERE contact_id = ? AND entry IN ('send_claimed', 'send_released')",
        ((datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat(), contact_id),
    )
    conn.commit()
    conn.close()


@pytest.fixture(autouse=True)
def _always_open(monkeypatch):
    async def window(_db, _user_id, _now):
        return {"open_now": True, "why": "any_time", "opens_at": None}

    monkeypatch.setattr(own_browser, "window", window)


# ── tests ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", [r for r in own_browser.RELEASE_REASONS if r not in ("done", "weekly_limit")])
async def test_release_then_take_never_returns_the_same_person(authenticated_async_context, reason):
    async with authenticated_async_context() as client:
        first, _ = await _person(client, 1)
        async with await _agent(client) as agent:
            took = await _take(agent)
            assert took["status"] == "task" and took["task"]["contact_id"] == first
            await _release(agent, first, reason)
            again = await _take(agent)
            assert again["status"] == "wait" and again["why"] == "released_recently", again
            assert 0 < again["wait_seconds"] <= own_browser.RELEASE_BACKOFF_MINUTES * 60
            # another person still waiting is handed out instead of the one just released
            second, _ = await _person(client, 2)
            nxt = await _take(agent)
            assert nxt["status"] == "task" and nxt["task"]["contact_id"] == second
            await _release(agent, second, reason)
            # once the backoff is over the first person comes back
            _age_rows(first, own_browser.RELEASE_BACKOFF_MINUTES + 1)
            back = await _take(agent)
            assert back["status"] == "task" and back["task"]["contact_id"] == first


@pytest.mark.asyncio
async def test_weekly_limit_parks_every_linkedin_send_for_seven_days(authenticated_async_context):
    async with authenticated_async_context() as client:
        first, _ = await _person(client, 4)
        second, _ = await _person(client, 5)
        async with await _agent(client) as agent:
            took = await _take(agent)
            assert took["status"] == "task"
            await _release(agent, took["task"]["contact_id"], "weekly_limit")
            parked = await _take(agent)
            assert parked["status"] == "wait" and parked["why"] == "weekly_limit", parked
            assert 6 * 86400 < parked["wait_seconds"] <= 7 * 86400  # neither person, until the week is over
            for contact_id in (first, second):
                _age_rows(contact_id, 7 * 24 * 60 + 1)
            back = await _take(agent)
            assert back["status"] == "task" and back["task"]["contact_id"] in (first, second)


@pytest.mark.asyncio
async def test_weekly_limit_still_hands_out_a_connected_persons_message(authenticated_async_context):
    """The limit is on invitations: a message to someone already connected still goes (review on #1489)."""
    async with authenticated_async_context() as client:
        await _person(client, 6)
        connected, _ = await _person(client, 7)
        patched = await client.patch(f"/api/contacts/{connected}", json={"linkedin_state": "connected"})
        assert patched.status_code == 200, patched.text
        async with await _agent(client) as agent:
            took = await _take(agent)
            assert took["status"] == "task"
            if took["task"]["contact_id"] == connected:  # take the invitee first, so it is the one released
                await _release(agent, connected, "other")
                _age_rows(connected, 61)
                took = await _take(agent)
            assert took["task"]["contact_id"] != connected
            await _release(agent, took["task"]["contact_id"], "weekly_limit")
            got = await _take(agent)
            assert got["status"] == "task" and got["task"]["contact_id"] == connected, got
