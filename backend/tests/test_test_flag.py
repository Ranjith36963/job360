"""S7 (owner 2026-10-29): a test flag at birth, honoured by the core surfaces; no word regex.

Real Postgres, real HTTP doors. ``bring_job(test=true)`` writes ``simulation`` as event 1; that
application is left out of GET /applications, /asks, /whats-new (events and open_asks) and
/needs-you-count, and still opens at GET /applications/{id} with ``is_test`` true. A note that
corrects the mark brings it back. A real job whose words say "Simulation" and "do not apply" counts.
"""
from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient


async def _bring(client: AsyncClient, title: str, company: str, **extra: Any) -> int:
    ad = {
        "title": title, "company": company, "location": "London", "country": "GB",
        "apply_url": f"https://{company.lower()}.example/careers/1", "description": "Build things.", **extra,
    }
    resp = await client.post("/api/jobs/bring", json=ad)
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _ask(client: AsyncClient, app_id: int, question: str) -> int:
    resp = await client.post("/api/asks", json={"application_id": app_id, "question": question})
    assert resp.status_code == 201, resp.text
    return int(resp.json()["id"])


async def _listed(client: AsyncClient) -> list[int]:
    body = (await client.get("/api/applications", params={"limit": 200})).json()
    return [int(a["id"]) for a in body["applications"]]


async def _asked(client: AsyncClient) -> list[int]:
    return [int(a["id"]) for a in (await client.get("/api/asks")).json()["asks"]]


async def _new(client: AsyncClient) -> dict[str, Any]:
    resp = await client.get("/api/whats-new")
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _count(client: AsyncClient) -> dict[str, int]:
    resp = await client.get("/api/needs-you-count")
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_a_test_bring_is_marked_first_and_kept_out_of_every_core_surface(authenticated_async_context):
    async with authenticated_async_context() as client:
        real = await _bring(client, "Engineer", "Realco")
        test = await _bring(client, "Practice job", "Testco", test=True)
        real_ask = await _ask(client, real, "Notice period?")
        test_ask = await _ask(client, test, "Salary?")

        detail = (await client.get(f"/api/applications/{test}")).json()
        assert detail["is_test"] is True
        assert [e["event_type"] for e in detail["events"]][:2] == ["simulation", "brought"]
        assert (await client.get(f"/api/applications/{real}")).json()["is_test"] is False

        assert await _listed(client) == [real]
        assert (await client.get("/api/applications", params={"limit": 200})).json()["total"] == 1
        assert await _asked(client) == [real_ask]
        new = await _new(client)
        assert {int(e["application_id"]) for e in new["events"]} == {real}
        assert [int(a["id"]) for a in new["open_asks"]] == [real_ask]
        assert [int(a["id"]) for a in new["applications"]] == [real]
        assert await _count(client) == {"n": 1, "jobs": 1, "other": 0}

        # The undo: a note correcting the mark makes it a real application again.
        sim_id = int(detail["events"][0]["id"])
        undo = await client.post(
            f"/api/applications/{test}/events",
            json={"event_type": "note", "detail": "Not a test.", "corrects_event_id": sim_id},
        )
        assert undo.status_code == 201, undo.text
        assert (await client.get(f"/api/applications/{test}")).json()["is_test"] is False
        assert sorted(await _listed(client)) == sorted([real, test])
        assert sorted(await _asked(client)) == sorted([real_ask, test_ask])
        assert sorted(int(a["id"]) for a in (await _new(client))["open_asks"]) == sorted([real_ask, test_ask])
        assert await _count(client) == {"n": 2, "jobs": 2, "other": 0}


@pytest.mark.asyncio
async def test_a_plain_bring_writes_no_mark_and_a_rebring_never_adds_one(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client, "Engineer", "Plainco")
        assert await _bring(client, "Engineer", "Plainco", test=True) == app_id
        detail = (await client.get(f"/api/applications/{app_id}")).json()
        assert [e["event_type"] for e in detail["events"]] == ["brought"]
        assert detail["is_test"] is False
        assert await _listed(client) == [app_id]


@pytest.mark.asyncio
async def test_a_real_job_whose_words_say_simulation_is_counted_and_listed(authenticated_async_context):
    """Why the word regex went: it hid this real job from the user's own lists."""
    async with authenticated_async_context() as client:
        app_id = await _bring(client, "Simulation Engineer", "Physico")
        ask_id = await _ask(client, app_id, "The ad says do not apply by agency. Apply directly?")
        assert await _listed(client) == [app_id]
        assert await _asked(client) == [ask_id]
        assert [int(a["id"]) for a in (await _new(client))["open_asks"]] == [ask_id]
        assert await _count(client) == {"n": 1, "jobs": 1, "other": 0}


@pytest.mark.asyncio
async def test_test_must_be_a_real_boolean(authenticated_async_context):
    async with authenticated_async_context() as client:
        resp = await client.post(
            "/api/jobs/bring",
            json={"title": "Engineer", "company": "Strictco", "description": "Build things.", "test": "yes"},
        )
        assert resp.status_code == 422, resp.text
