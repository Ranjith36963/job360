"""The applications list reports how long each step took in a ``Server-Timing`` header (2026-11-13):
the queued list took ~1.8 s on a large account and nothing outside the server could say which step.
Durations and fixed step names only; the JSON body is unchanged."""
from __future__ import annotations

import re
from typing import Any

import pytest
from httpx import AsyncClient

ENTRY = re.compile(r"^[a-z_]+;dur=[0-9]+(\.[0-9]+)?$")


def _entries(resp: Any) -> dict[str, float]:
    parts = [e.strip() for e in resp.headers["server-timing"].split(",")]
    assert all(ENTRY.match(p) for p in parts), parts
    return {p.split(";dur=")[0]: float(p.split(";dur=")[1]) for p in parts}


async def _bring_and_queue(client: AsyncClient) -> int:
    ad = {"title": "Data Engineer", "company": "Acme", "description": "Build pipelines. " * 20,
          "apply_url": "https://acme.example/jobs/1", "location": "London, UK"}
    resp = await client.post("/api/jobs/bring", json=ad)
    assert resp.status_code == 200, resp.text
    app_id = int(resp.json()["application_id"])
    queued = await client.post(f"/api/applications/{app_id}/events", json={"event_type": "queued"})
    assert queued.status_code == 201, queued.text
    return app_id


@pytest.mark.asyncio
async def test_the_list_reports_how_long_each_step_took(authenticated_async_context):
    async with authenticated_async_context() as client:
        await _bring_and_queue(client)
        resp = await client.get("/api/applications", params={"limit": 5})
    assert resp.status_code == 200, resp.text
    raw = [e.strip() for e in resp.headers["server-timing"].split(",")]
    assert raw[-1].startswith("total;dur=")
    timings = _entries(resp)
    assert any(name.startswith("l_") for name in timings)
    assert timings["total"] >= max(v for k, v in timings.items() if k != "total")
    assert not any(name.startswith("q_") for name in timings), "the queue steps run only for queued=true"


@pytest.mark.asyncio
async def test_the_queued_list_names_the_preflight_steps_and_keeps_its_body(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring_and_queue(client)
        resp = await client.get("/api/applications", params={"limit": 5, "queued": True})
    assert resp.status_code == 200, resp.text
    timings = _entries(resp)
    assert any(name.startswith("q_") for name in timings)
    assert timings["total"] >= max(v for k, v in timings.items() if k != "total")
    body = resp.json()
    assert set(body) == {"applications", "total", "queue_summary", "outreach", "queue_order"}
    assert [r["id"] for r in body["applications"]] == [app_id] and body["total"] == 1
    assert body["applications"][0]["preflight"] is not None
