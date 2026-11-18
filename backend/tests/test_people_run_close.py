"""People hunt (owner, 2026-11-17, the 20:08 run): a run saved 10 people, wrote no outreach note for any of
them, and marked network failed with ``tool_unavailable`` because one lookup was out of credits. The recipe
said both; the run obeyed neither. ``mark_done`` for ``reach`` and ``network`` now refuses (409, with the next
step) while people this assistant saved this run have no note, and refuses ``failed`` for one tool being out.
The website's own marks are unchanged. Helpers are copied, never imported.
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from tests._outreach_review import seed_strong_review

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
            unreviewed = await agent.post("/api/schedule/done", json=done)
            assert unreviewed.status_code == 409 and "no Doctor review" in unreviewed.json()["detail"], unreviewed.text
            seed_strong_review(first), seed_strong_review(second)
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
            assert "missing: sprouts, clay." in refused.json()["detail"]  # Vibe needs credits (2026-11-18)
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


async def _noted(agent: AsyncClient, app_id: int, names: list[str]) -> None:
    for name in names:
        cid = await _person(agent, app_id, name)
        note = {"entry": "message", "channel": "linkedin", "text": f"Hi {name.split()[0]}, I build data pipelines."}
        assert (await agent.post(f"/api/contacts/{cid}/outreach", json=note)).status_code in (200, 201)


async def _reviewed(agent: AsyncClient, app_id: int, names: list[str]) -> None:
    await _noted(agent, app_id, names)
    people = (await agent.get("/api/people")).json()["people"]
    for cid in (c for p in people if p["name"] in names for c in p["contact_ids"]):
        seed_strong_review(cid)


@pytest.mark.asyncio
async def test_notes_without_a_doctor_review_are_refused(authenticated_async_context):
    """2026-11-18 (09:13, 12:05 and 13:56 runs): every run said "self-review only"; reviewed=true was only a flag.
    Job360 now asks for the stored outreach_reviewed of each note this run saved, and the flag alone proves nothing."""
    async with authenticated_async_context() as client:
        app_id = int((await client.post("/api/jobs/bring", json=_AD)).json()["application_id"])
        async with await _agent(client) as agent:
            await _noted(agent, app_id, ["Priya Shah", "Tom Reed", "Ana Silva"])
            done = {"duty": "reach", "application_ids": [app_id], "reviewed": True}
            refused = await agent.post("/api/schedule/done", json=done)
            assert refused.status_code == 409, refused.text
            detail = refused.json()["detail"]
            assert "3 notes saved this run have no Doctor review" in detail and "fresh helper agent" in detail
            people = (await client.get("/api/people")).json()["people"]
            for cid in sorted(c for p in people for c in p["contact_ids"]):
                seed_strong_review(cid)
            ok = await agent.post("/api/schedule/done", json={"duty": "reach", "application_ids": [app_id]})
            assert ok.status_code == 200 and ok.json()["outcome"] == "done", ok.text  # the reviews are the evidence
            wrong = await agent.post("/api/schedule/done", json={"duty": "inbox", "reviewed": True})
            assert wrong.status_code == 422, wrong.text
        web = await client.post("/api/schedule/done", json={"duty": "network"})
        assert web.status_code == 200 and web.json()["outcome"] == "done", web.text


@pytest.mark.asyncio
async def test_job360_counts_the_people_never_the_run(authenticated_async_context):
    """Owner 2026-11-18: 5 people per job (at least 3) and 5 network people; the 09:13 run found 4 of 25 and 0 of 5
    and its own words said done. Job360 counts, records partial below_target, never refuses, and returns `score`."""
    async with authenticated_async_context() as client:
        full = int((await client.post("/api/jobs/bring", json=_AD)).json()["application_id"])
        thin = int((await client.post("/api/jobs/bring", json={**_AD, "title": "Platform Engineer", "apply_url": "https://northwind.example/c/8"})
                    ).json()["application_id"])
        async with await _agent(client) as agent:
            await _reviewed(agent, full, ["Priya Shah", "Tom Reed", "Ana Silva"])
            await _reviewed(agent, thin, ["Lee Park"])
            resp = await agent.post("/api/schedule/done", json={
                "duty": "reach", "application_ids": [full, thin], "outcome": "done", "reviewed": True})
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert full != thin, (full, thin)
            assert body["outcome"] == "partial", body["score"]
            assert body["score"] == {"per_job": {str(full): 3, str(thin): 1}, "network": None, "target": 5, "minimum": 3}
            assert f"{thin}: 1 of 5" in body["note"] and f"{full}:" not in body["note"]
            runs = (await client.get("/api/schedule/status")).json()["duties"]
            row = next(r for r in runs if r["duty"] == "reach")
            assert (row["last_outcome"], row["last_reason"]) == ("partial", "below_target"), row
            net = await agent.post("/api/schedule/done", json={"duty": "network", "reviewed": True})
            assert net.status_code == 200 and net.json()["outcome"] == "partial", net.text
            assert net.json()["score"]["network"] == 0 and "network: 0 of 5" in net.json()["note"]
            ok = await agent.post("/api/schedule/done", json={
                "duty": "reach", "application_ids": [full], "reviewed": True})
            assert ok.json()["outcome"] == "done" and ok.json()["score"]["per_job"] == {str(full): 3}, ok.text


@pytest.mark.asyncio
async def test_a_short_unreviewed_run_hears_both(authenticated_async_context):
    """2026-11-18 12:05 run: 2 of 5 jobs under 3 and no separate reviewer; the reply named only below_target."""
    async with authenticated_async_context() as client:
        app_id = int((await client.post("/api/jobs/bring", json=_AD)).json()["application_id"])
        added = await client.post(f"/api/applications/{app_id}/contacts", json={"name": "Lee Park"})
        assert added.status_code == 201, added.text  # saved on the website: nothing for this run to review
        async with await _agent(client) as agent:
            resp = await agent.post("/api/schedule/done", json={"duty": "reach", "application_ids": [app_id]})
            assert resp.status_code == 200, resp.text
            note = resp.json()["note"]
            assert "below_target" in note and "no_review" in note and "reviewed=true" in note, note
