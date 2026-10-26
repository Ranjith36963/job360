"""One queue (owner, 2026-10-26): ``GET /applications?queued=true`` lists the queued applications AND the
LinkedIn sends the user said yes to and that have not gone yet, merged by when the user said yes (oldest
first), each with its browser chip; ``by_browser`` counts both. Read-only: nothing here sends. Real doors,
real Postgres. Helpers are copied (schema isolation)."""
from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from src.services.browser_agent import routing


def _ad(slug: str) -> dict[str, Any]:
    return {
        "title": f"Engineer {slug}", "company": f"Co {slug}", "location": "Remote",
        "apply_url": f"https://boards.greenhouse.io/acme/jobs/{slug}",
        "description": "Build the pipelines. Python, dbt. Fully remote.",
    }


async def _bring(client: AsyncClient, slug: str) -> int:
    resp = await client.post("/api/jobs/bring", json=_ad(slug))
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _queue(client: AsyncClient, app_id: int) -> None:
    resp = await client.post(f"/api/applications/{app_id}/events", json={"event_type": "queued"})
    assert resp.status_code == 201, resp.text


async def _person(client: AsyncClient, app_id: int, n: int) -> tuple[int, int]:
    """A LinkedIn contact on ``app_id`` with one drafted note; returns (contact_id, message_id)."""
    added = await client.post(
        f"/api/applications/{app_id}/contacts",
        json={"name": f"Priya {n}", "role": "Recruiter", "linkedin_url": f"https://www.linkedin.com/in/priya{n}"},
    )
    assert added.status_code == 201, added.text
    contact_id = int(added.json()["contact"]["id"])
    saved = await client.post(
        f"/api/applications/{app_id}/artifacts",
        json={"kind": "outreach", "text": "Hi, a note", "contact_id": contact_id, "channel": "linkedin"},
    )
    assert saved.status_code == 201, saved.text
    person = (await client.get(f"/api/people?contact_id={contact_id}")).json()["person"]
    return contact_id, int(person["outreach"]["messages"][-1]["id"])


async def _click(client: AsyncClient, contact_id: int, message_id: int, decision: str = "approve") -> None:
    resp = await client.post(
        f"/api/contacts/{contact_id}/own-browser-send", json={"message_id": message_id, "decision": decision}
    )
    assert resp.status_code == 200, resp.text


async def _list(client: AsyncClient) -> dict[str, Any]:
    resp = await client.get("/api/applications", params={"queued": True})
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_an_approved_send_sits_between_two_applications_in_approval_order(authenticated_async_context):
    async with authenticated_async_context() as client:
        first = await _bring(client, "a1")
        second = await _bring(client, "a2")
        holder = await _bring(client, "holder")  # not queued: only the person's job
        contact_id, message_id = await _person(client, holder, 1)
        await _queue(client, first)
        await _click(client, contact_id, message_id)
        await _queue(client, second)

        body = await _list(client)
        assert body["queue_order"] == [f"application:{first}", f"contact:{contact_id}", f"application:{second}"]
        assert [r["id"] for r in body["applications"]] == [first, second]
        [item] = body["outreach"]
        assert item["task_id"] == f"contact:{contact_id}" and item["kind"] == "outreach"
        assert item["contact_id"] == contact_id and item["application_id"] == holder
        assert item["message_id"] == message_id and item["state"] == "queued" and item["approved_at"]
        assert item["outreach_kind"] == "linkedin_note" and item["title"] == "LinkedIn note to Priya 1"
        assert item["browser"] == {
            "kind": "own_browser", "label": routing.ROUTE_LABELS["linkedin_own_browser"],
            "reason": "linkedin_own_browser",
        }
        assert "draft_text" not in item
        s = body["queue_summary"]
        assert s["queued"] == 2 and s["outreach"] == 1
        assert s["by_browser"] == {"job360": 2, "own_browser": 1, "human": 0}
        # outside the queue view there is no outreach section
        plain = (await client.get("/api/applications")).json()
        assert plain["outreach"] is None and plain["queue_order"] is None


@pytest.mark.asyncio
async def test_a_declined_or_sent_send_is_not_listed(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client, "j")
        declined, m1 = await _person(client, app_id, 1)
        sent, m2 = await _person(client, app_id, 2)
        waiting, _m3 = await _person(client, app_id, 3)  # never clicked: waiting on the user, not queued
        await _click(client, declined, m1)
        await _click(client, sent, m2)
        assert {o["contact_id"] for o in (await _list(client))["outreach"]} == {declined, sent}

        await _click(client, declined, m1, "decline")
        mark = await client.post(f"/api/contacts/{sent}/outreach", json={"entry": "sent", "channel": "linkedin"})
        assert mark.status_code == 201, mark.text
        body = await _list(client)
        assert body["outreach"] == [] and body["queue_order"] == []
        assert body["queue_summary"]["outreach"] == 0
        assert body["queue_summary"]["by_browser"] == {"job360": 0, "own_browser": 0, "human": 0}
        assert waiting not in {o["contact_id"] for o in body["outreach"]}


@pytest.mark.asyncio
async def test_another_users_outreach_is_never_listed(authenticated_async_context):
    from src.api import dependencies as api_deps
    from src.services.applications import spine

    async with authenticated_async_context() as client:
        app_id = await _bring(client, "mine")
        contact_id, message_id = await _person(client, app_id, 1)
        await _click(client, contact_id, message_id)
        db = await api_deps.get_db()
        other = await spine.list_applications(db, "someone-else", queued=True)
        assert other["outreach"] == [] and other["queue_order"] == []
        assert other["queue_summary"]["outreach"] == 0
        assert other["queue_summary"]["by_browser"] == {"job360": 0, "own_browser": 0, "human": 0}
        mine = await spine.list_applications(db, authenticated_async_context.fixture_user_id, queued=True)
        assert [o["contact_id"] for o in mine["outreach"]] == [contact_id]
        assert mine["queue_summary"]["by_browser"] == {"job360": 0, "own_browser": 1, "human": 0}
