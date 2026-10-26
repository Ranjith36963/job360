"""Owner bug 2026-10-26 (job360.uk/needs-you, applications 113 and 127): an Edit on a "Needs your
click" row (gender, ethnicity, demographic consent) was lost on reload. The job card saved nothing
until EVERY row was checked, and a row the assistant had not answered kept that from ever happening.

Now one row saves on its own (``POST /api/asks/{id}/answers/save``, the user's web session only):
the ask stays open, a reload reads the user's word back, and the assistant's hourly re-ask keeps it.
Helpers are copied, never imported from another test module.
"""
from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

_AD = {
    "title": "AI Engineer I", "company": "Globalization Partners", "location": "London",
    "apply_url": "https://gp.example/careers/1", "description": "Build agents.",
}
ROWS = [
    {"key": "equality.gender", "question": "Use 'Male' for gender on this form?", "answer": "Male", "source": "memory"},
    {"key": "equality.ethnicity", "question": "Use 'Indian' for ethnicity on this form?", "answer": "Indian",
     "source": "memory"},
    {"key": "demographic_consent", "question": "Tick the demographic-data consent checkbox?", "answer": "Yes",
     "source": "guess", "choices": ["Yes", "No"]},
    {"key": "cv_ok", "question": "CV 87 OK to use?", "answer": "Draft: yes", "source": "draft"},
]


async def _bring(client: AsyncClient) -> int:
    resp = await client.post("/api/jobs/bring", json=_AD)
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _agent(token: str) -> AsyncClient:
    from src.api.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"})


async def _agent_asks(token: str, app_id: int, rows: list[dict[str, Any]]) -> dict[str, Any]:
    async with await _agent(token) as agent:
        resp = await agent.post("/api/asks", json={"application_id": app_id, "question": "G-P: your OK on these",
                                                   "kind": "question", "questions": rows})
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _open_ask(client: AsyncClient, app_id: int) -> dict[str, Any]:
    asks = [a for a in (await client.get(f"/api/applications/{app_id}")).json()["asks"] if a["status"] == "open"]
    assert len(asks) == 1, asks
    return asks[0]


@pytest.mark.asyncio
async def test_an_edited_sensitive_row_saves_alone_and_reads_back_after_a_reload(authenticated_async_context):
    async with authenticated_async_context() as client:
        token = (await client.post("/api/tokens", json={"name": "claude-code"})).json()["token"]
        app_id = await _bring(client)
        ask = await _agent_asks(token, app_id, ROWS)
        resp = await client.post(f"/api/asks/{ask['id']}/answers/save",
                                 json={"answers": {"equality.ethnicity": "South Asian"}})
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "open", "the rows still waiting keep the ask open"
        reloaded = await _open_ask(client, app_id)
        assert reloaded["answers"] == {"equality.ethnicity": "South Asian"}
        # A second row (the consent box, the owner's own click) adds to it; the first stays.
        resp = await client.post(f"/api/asks/{ask['id']}/answers/save", json={"answers": {"demographic_consent": "No"}})
        assert resp.status_code == 200, resp.text
        assert (await _open_ask(client, app_id))["answers"] == {"equality.ethnicity": "South Asian",
                                                                "demographic_consent": "No"}


@pytest.mark.asyncio
async def test_the_assistants_hourly_re_ask_keeps_the_users_saved_rows(authenticated_async_context):
    async with authenticated_async_context() as client:
        token = (await client.post("/api/tokens", json={"name": "claude-code"})).json()["token"]
        app_id = await _bring(client)
        ask = await _agent_asks(token, app_id, ROWS)
        await client.post(f"/api/asks/{ask['id']}/answers/save", json={"answers": {"equality.gender": "Prefer not to say"}})
        again = await _agent_asks(token, app_id, ROWS[:2])  # the next run asks again, typing memory's old value
        assert again["merged_from"] == [ask["id"]]
        assert (await _open_ask(client, app_id))["answers"] == {"equality.gender": "Prefer not to say"}


@pytest.mark.asyncio
async def test_only_the_users_session_saves_rows_and_bad_rows_write_nothing(authenticated_async_context):
    async with authenticated_async_context() as client:
        token = (await client.post("/api/tokens", json={"name": "claude-code"})).json()["token"]
        app_id = await _bring(client)
        ask = await _agent_asks(token, app_id, ROWS)
        url = f"/api/asks/{ask['id']}/answers/save"
        async with await _agent(token) as agent:
            refused = await agent.post(url, json={"answers": {"equality.gender": "Female"}})
        assert refused.status_code in (401, 403), "an assistant never saves the user's word for them"
        assert (await client.post(url, json={"answers": {"nope": "x"}})).status_code == 422
        assert (await client.post(url, json={"answers": {"demographic_consent": "Maybe"}})).status_code == 422
        assert (await client.post(url, json={"answers": {"equality.gender": "  "}})).status_code == 422
        assert (await client.post(url, json={"answers": {}})).status_code == 422
        assert (await client.post("/api/asks/999999/answers/save", json={"answers": {"x": "y"}})).status_code == 404
        assert (await _open_ask(client, app_id))["answers"] == {}
        full = {r["key"]: r["answer"] for r in ROWS}
        assert (await client.post(f"/api/asks/{ask['id']}/answer", json={"answers": full})).status_code == 200
        assert (await client.post(url, json={"answers": {"equality.gender": "Female"}})).status_code == 409
