"""People hunt, 2026-11-19 02:21 and 02:56 runs (Kits orders, 03:05): the fresh People Doctor check failed people
(Goodenough, Agarwal; then the Digiflex pair) and the run added them anyway, against "add only those it passes".
An assistant's add_contact now carries that check's verdict as ``people_check``: ``fail`` is refused, and while a
scheduled people run holds reach or network the verdict is required. The website's own adds are unchanged.
Helpers are copied, never imported.
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

PEOPLE = "assistant_settings.people_hunt"
_AD = {
    "title": "Data Engineer",
    "company": "Northwind",
    "location": "Remote",
    "apply_url": "https://northwind.example/careers/7",
    "description": "Build the pipelines. Python, dbt, Snowflake. Fully remote.",
}


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


async def _agent(client: AsyncClient) -> AsyncClient:
    from src.api.main import app

    token = (await client.post("/api/tokens", json={"name": "agent"})).json()["token"]
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"})


def _person(name: str, **extra: str) -> dict[str, str]:
    return {"name": name, "role": "Talent Partner", "found_via": "company_team_page",
            "source_url": "https://northwind.example/team", **extra}


@pytest.mark.asyncio
async def test_a_person_the_people_check_failed_is_refused(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = int((await client.post("/api/jobs/bring", json=_AD)).json()["application_id"])
        async with await _agent(client) as agent:
            refused = await agent.post(f"/api/applications/{app_id}/contacts",
                                       json=_person("Rosa Downing", people_check="fail"))
            assert refused.status_code == 422, refused.text
            assert "people check failed" in refused.json()["detail"].lower(), refused.text
            cold = await agent.post("/api/contacts", json={**_person("Tom Reed", people_check="fail"),
                                                           "company": "Northwind"})
            assert cold.status_code == 422, cold.text
            ok = await agent.post(f"/api/applications/{app_id}/contacts", json=_person("Ana Silva", people_check="pass"))
            assert ok.status_code == 201, ok.text
        names = [p["name"] for p in (await client.get("/api/people")).json()["people"]]
        assert names == ["Ana Silva"], names


@pytest.mark.asyncio
async def test_a_scheduled_people_run_passes_the_verdict(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        resp = await client.patch("/api/profile", json={"edits": [{"path": PEOPLE, "value": True}]})
        assert resp.status_code == 200, resp.text
        app_id = int((await client.post("/api/jobs/bring", json=_AD)).json()["application_id"])
        async with await _agent(client) as agent:
            plain = await agent.post(f"/api/applications/{app_id}/contacts", json=_person("Lee Park"))
            assert plain.status_code == 201, plain.text  # no people run holds a claim: as before
            claimed = (await agent.get("/api/schedule/due", params={"duties": ["reach"]})).json()
            assert any(d["duty"] == "reach" for d in claimed["due"]), claimed
            missing = await agent.post(f"/api/applications/{app_id}/contacts", json=_person("Sam Cole"))
            assert missing.status_code == 422 and "people_check" in missing.json()["detail"], missing.text
            passed = await agent.post(f"/api/applications/{app_id}/contacts", json=_person("Sam Cole", people_check="pass"))
            assert passed.status_code == 201, passed.text
        web = await client.post(f"/api/applications/{app_id}/contacts", json=_person("Kim Lee"))
        assert web.status_code == 201, web.text  # the website is never asked


@pytest.mark.asyncio
async def test_a_people_list_never_carries_a_failed_person_past_the_check(authenticated_async_context, fixture_user_id):
    """Review of #1700: add_contact(people=[...]) went through POST /contacts/batch, which skipped the check."""
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        resp = await client.patch("/api/profile", json={"edits": [{"path": PEOPLE, "value": True}]})
        assert resp.status_code == 200, resp.text
        app_id = int((await client.post("/api/jobs/bring", json=_AD)).json()["application_id"])
        async with await _agent(client) as agent:
            rows = {"application_id": app_id, "people": [_person("Rosa Downing", people_check="fail"), _person("Ana Silva")]}
            outside = await agent.post("/api/contacts/batch", json=rows)
            assert outside.status_code == 200, outside.text
            assert [r["index"] for r in outside.json()["invalid"]] == [0], outside.json()
            assert "people check failed" in outside.json()["invalid"][0]["detail"].lower()
            await agent.get("/api/schedule/due", params={"duties": ["reach"]})
            during = await agent.post("/api/contacts/batch", json={**rows, "people": [_person("Tom Reed")]})
            assert during.status_code == 422 and "one at a time" in during.json()["detail"], during.text
            preview = await agent.post("/api/contacts/batch", json={**rows, "people": [_person("Tom Reed")], "dry_run": True})
            assert preview.status_code == 200, preview.text
        names = sorted(p["name"] for p in (await client.get("/api/people")).json()["people"])
        assert names == ["Ana Silva"], names
