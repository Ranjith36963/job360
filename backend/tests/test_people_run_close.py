"""People hunt (owner, 2026-11-17, the 20:08 run): a run saved 10 people, wrote no outreach note for any of
them, and marked network failed with ``tool_unavailable`` because one lookup was out of credits. The recipe
said both; the run obeyed neither. ``mark_done`` for ``reach`` and ``network`` now refuses (409, with the next
step) while people this assistant saved this run have no note, and refuses ``failed`` for one tool being out.
The website's own marks are unchanged. Helpers are copied, never imported.
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

_AD = {
    "title": "Data Engineer",
    "company": "Northwind",
    "location": "Remote",
    "apply_url": "https://northwind.example/careers/7",
    "description": "Build the pipelines. Python, dbt, Snowflake. Fully remote.",
}


async def _agent(client: AsyncClient) -> AsyncClient:
    from src.api.main import app

    token = (await client.post("/api/tokens", json={"name": "agent"})).json()["token"]
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"})


async def _person(agent: AsyncClient, app_id: int, name: str) -> int:
    resp = await agent.post(f"/api/applications/{app_id}/contacts", json={
        "name": name, "role": "Talent Partner", "source_url": "https://northwind.example/team",
        "linkedin_url": f"https://www.linkedin.com/in/{name.lower().replace(' ', '-')}",
    })
    assert resp.status_code == 201, resp.text
    return int(resp.json()["contact_id"])


@pytest.mark.asyncio
async def test_reach_is_refused_until_every_new_person_has_a_note(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = int((await client.post("/api/jobs/bring", json=_AD)).json()["application_id"])
        async with await _agent(client) as agent:
            first = await _person(agent, app_id, "Priya Shah")
            second = await _person(agent, app_id, "Tom Reed")
            done = {"duty": "reach", "application_ids": [app_id], "outcome": "done"}
            refused = await agent.post("/api/schedule/done", json=done)
            assert refused.status_code == 409, refused.text
            detail = refused.json()["detail"]
            assert "2 people saved this run have no outreach note" in detail
            assert f"contact {first}" in detail and "save_artifact" in detail
            note = {"entry": "message", "channel": "linkedin", "text": "Hi Priya, I build data pipelines in Python."}
            assert (await agent.post(f"/api/contacts/{first}/outreach", json=note)).status_code in (200, 201)
            still = await agent.post("/api/schedule/done", json=done)
            assert still.status_code == 409 and f"contact {second}" in still.json()["detail"]
            note["text"] = "Hi Tom, I build data pipelines in Python."
            assert (await agent.post(f"/api/contacts/{second}/outreach", json=note)).status_code in (200, 201)
            ok = await agent.post("/api/schedule/done", json=done)
            assert ok.status_code == 200, ok.text


@pytest.mark.asyncio
async def test_one_tool_out_is_not_a_failed_people_run(authenticated_async_context):
    async with authenticated_async_context() as client:
        async with await _agent(client) as agent:
            for reason in ("tool_unavailable", "paid_plan_needed", "sign_in_needed", "no_work", "needs_user"):
                body = {"duty": "network", "outcome": "failed", "reason": reason}
                refused = await agent.post("/api/schedule/done", json=body)
                assert refused.status_code == 409 and "not a failed people run" in refused.text, refused.text
            said = "Sprouts: 0 at these domains; Clay: none; Vibe: no names; company pages 403."
            partial = await agent.post("/api/schedule/done", json={"duty": "network", "outcome": "partial", "note": said})
            assert partial.status_code == 200, partial.text
            blocked = await agent.post("/api/schedule/done", json={
                "duty": "network", "outcome": "failed", "reason": "network_blocked", "note": said})
            assert blocked.status_code == 200, blocked.text


@pytest.mark.asyncio
async def test_stopping_short_names_what_each_free_lookup_returned(authenticated_async_context):
    """2026-11-17 20:48 run: Sprouts loaded but never called, Clay and Vibe never loaded, nothing found."""
    async with authenticated_async_context() as client:
        app_id = int((await client.post("/api/jobs/bring", json=_AD)).json()["application_id"])
        async with await _agent(client) as agent:
            body = {"duty": "reach", "application_ids": [app_id], "outcome": "partial",
                    "note": "Apollo people search not on plan; about pages 403; web search gave no proven names."}
            refused = await agent.post("/api/schedule/done", json=body)
            assert refused.status_code == 409, refused.text
            assert "missing: sprouts, clay, vibe" in refused.json()["detail"]
            body["note"] = "Sprouts: no one at the domain. Clay: none. Vibe: company matched, no names."
            assert (await agent.post("/api/schedule/done", json=body)).status_code == 200
            done = await agent.post("/api/schedule/done", json={"duty": "network"})
            assert done.status_code == 200, done.text  # a run that reached 5 needs no report


@pytest.mark.asyncio
async def test_the_website_marks_as_before(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = int((await client.post("/api/jobs/bring", json=_AD)).json()["application_id"])
        added = await client.post(f"/api/applications/{app_id}/contacts", json={"name": "Priya Shah"})
        assert added.status_code == 201, added.text
        ok = await client.post("/api/schedule/done", json={"duty": "reach", "application_ids": [app_id]})
        assert ok.status_code == 200, ok.text
        failed = await client.post("/api/schedule/done", json={
            "duty": "network", "outcome": "failed", "reason": "tool_unavailable"})
        assert failed.status_code == 200, failed.text
