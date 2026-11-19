"""People hunt, 2026-11-18 22:07 run (Kits overnight orders): whats_due gave five applications; the run searched
one company (Harmonic Security), listed all five ids in mark_done so the leave-no-job-out 409 passed, and never
called Sprouts or Clay for the other four. ``mark_done(reach)`` from an assistant now needs ``searched``: for each
job it leaves under the minimum, what each free lookup found at that company. Helpers are copied, never imported.
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

PEOPLE = "assistant_settings.people_hunt"


def _seed_profile(user_id: str) -> None:
    from src.services.profile.models import CVData, UserPreferences, UserProfile
    from src.services.profile.storage import save_profile

    save_profile(
        UserProfile(
            cv_data=CVData(raw_text="Python data engineer: dbt, Snowflake.", name="Ada Lovelace", skills=["Python"]),
            preferences=UserPreferences(target_job_titles=["Data Engineer"]),
        ),
        user_id,
        source_action="cv_upload",
    )


async def _bring(client: AsyncClient, n: int) -> int:
    ad = {"title": f"Data Engineer {n}", "company": f"Northwind {n}", "location": "Remote",
          "apply_url": f"https://northwind{n}.example/careers/{n}",
          "description": "Build the pipelines. Python, dbt, Snowflake. Fully remote."}
    resp = await client.post("/api/jobs/bring", json=ad)
    assert resp.status_code in (200, 201), resp.text
    return int(resp.json()["application_id"])


async def _agent(client: AsyncClient) -> AsyncClient:
    from src.api.main import app

    token = (await client.post("/api/tokens", json={"name": "agent"})).json()["token"]
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"})


@pytest.mark.asyncio
async def test_a_job_listed_but_never_searched_is_refused(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        resp = await client.patch("/api/profile", json={"edits": [{"path": PEOPLE, "value": True}]})
        assert resp.status_code == 200, resp.text
        one, two = await _bring(client, 1), await _bring(client, 2)
        async with await _agent(client) as agent:
            await agent.get("/api/schedule/due", params={"duties": ["reach"]})
            body = {"duty": "reach", "application_ids": [one, two], "outcome": "partial",
                    "note": "Sprouts: none. Clay: none.", "searched": {str(one): {"sprouts": 0, "clay": 0, "web": 0}}}
            refused = await agent.post("/api/schedule/done", json=body)
            assert refused.status_code == 409, refused.text
            detail = refused.json()["detail"]
            assert f"no searched entry: {two}" in detail and "searched" in detail, detail
            half = {**body, "searched": {**body["searched"], str(two): {"web": 0}}}
            still = await agent.post("/api/schedule/done", json=half)
            assert still.status_code == 409 and f"{two}: sprouts, clay" in still.json()["detail"], still.text
            body["searched"][str(two)] = {"sprouts": 0, "clay": 0, "team_page": 0}
            ok = await agent.post("/api/schedule/done", json=body)
            assert ok.status_code == 200, ok.text
            assert ok.json()["outcome"] == "partial", ok.json()


@pytest.mark.asyncio
async def test_searched_is_only_for_reach_and_the_website_is_not_asked(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client, 3)
        web = await client.post("/api/schedule/done", json={"duty": "reach", "application_ids": [app_id]})
        assert web.status_code == 200, web.text
        async with await _agent(client) as agent:
            wrong = await agent.post("/api/schedule/done", json={"duty": "inbox", "searched": {"1": {"clay": 0}}})
            assert wrong.status_code == 422, wrong.text
