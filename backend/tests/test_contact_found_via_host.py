"""People hunt (Kits review, 2026-11-18): an assistant saved a news page as found_via company_team_page and a
person proven on a news site as found_via other next to one labelled company_team_page. ``add_contact`` from an
assistant now refuses (422, saying what to pass) a found_via that contradicts the host of its source_url. The
website's own adds are not checked. Helpers are copied, never imported.
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

_AD = {
    "title": "Data Engineer",
    "company": "Ellison Institute of Technology",
    "location": "Oxford",
    "apply_url": "https://jobs.example-ats.com/eit/7",
    "description": "Build the pipelines. Python, dbt, Snowflake.",
}


async def _agent(client: AsyncClient) -> AsyncClient:
    from src.api.main import app

    token = (await client.post("/api/tokens", json={"name": "agent"})).json()["token"]
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"})


async def _add(agent: AsyncClient, app_id: int, name: str, found_via: str, source_url: str):
    return await agent.post(f"/api/applications/{app_id}/contacts", json={
        "name": name, "role": "Recruiter", "found_via": found_via, "source_url": source_url,
    })


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("found_via", "source_url", "hint"),
    [
        ("company_team_page", "https://www.bernews.com/2026/09/eit-hires/", "news article is found_via other"),
        ("other", "https://www.linkedin.com/in/priya-shah", "linkedin page is found_via linkedin"),
        ("apollo", "https://www.bernews.com/2026/09/eit-hires/", "needs the linkedin profile"),
        ("linkedin", "https://eit.org/team", "needs the linkedin profile"),
        ("company_site", "https://github.com/priya", "github profile is found_via github"),
        ("github", "https://eit.org/people/priya", "github profile is found_via github"),
    ],
)
async def test_a_found_via_that_contradicts_its_page_is_refused(authenticated_async_context, found_via, source_url, hint):
    async with authenticated_async_context() as client:
        app_id = int((await client.post("/api/jobs/bring", json=_AD)).json()["application_id"])
        async with await _agent(client) as agent:
            resp = await _add(agent, app_id, "Priya Shah", found_via, source_url)
            assert resp.status_code == 422, resp.text
            detail = resp.json()["detail"].lower()
            assert f"found_via {found_via} does not match" in detail and hint in detail, detail
        assert not (await client.get("/api/people")).json()["people"]


@pytest.mark.asyncio
async def test_matching_pages_still_save(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = int((await client.post("/api/jobs/bring", json=_AD)).json()["application_id"])
        async with await _agent(client) as agent:
            for i, (via, url) in enumerate((
                ("company_team_page", "https://eit.org/team"),  # the company's initials
                ("company_site", "https://www.ellisoninstitute.com/about"),  # a name word
                ("company_site", "https://jobs.example-ats.com/eit/about"),  # the ad's own host
                ("other", "https://www.bernews.com/2026/09/eit-hires/"),
                ("linkedin", "https://www.linkedin.com/in/ok-person-4"),
                ("apollo", "https://uk.linkedin.com/in/ok-person-5"),
                ("github", "https://github.com/ok-person-6"),
                ("speaker_page", "https://pydata.org/london2026/speaker/ok"),
            )):
                resp = await _add(agent, app_id, f"Ok Person {i}", via, url)
                assert resp.status_code == 201, (via, url, resp.text)


@pytest.mark.asyncio
async def test_the_website_is_not_checked(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = int((await client.post("/api/jobs/bring", json=_AD)).json()["application_id"])
        resp = await _add(client, app_id, "Priya Shah", "company_team_page", "https://www.bernews.com/x")
        assert resp.status_code == 201, resp.text
