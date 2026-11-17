"""People hunt (owner, 2026-11-17): a contact's ``source_url`` is the page that proves the person works there.

People runs kept saving people "proven" only on a contact-data broker or people directory (rocketreach,
theorg, tal.club, wellfound, insideorg) although every prompt version said never to. Job360 now refuses
those hosts on ``add_contact`` and ``update_contact`` with a 422 that says what to pass instead. The bring
path (people listed in a job ad) is not affected. Helpers are copied, never imported.
"""
from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

_AD = {
    "title": "Data Engineer",
    "company": "Northwind",
    "location": "Remote",
    "apply_url": "https://northwind.example/careers/7",
    "description": "Build the pipelines. Python, dbt, Snowflake. Fully remote.",
}


async def _bring(client: AsyncClient) -> int:
    resp = await client.post("/api/jobs/bring", json=_AD)
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _add_contact(client: AsyncClient, application_id: int, **body: Any):
    payload = {"name": "Priya Shah", "role": "Talent Partner", **body}
    return await client.post(f"/api/applications/{application_id}/contacts", json=payload)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "https://rocketreach.co/priya-shah-email_123",
        "https://theorg.com/org/northwind/person/priya-shah",
        "https://www.insideorg.com/northwind/priya",
        "https://tal.club/meet/priya-shah",
        "https://wellfound.com/u/priya-shah",
        "https://www.zoominfo.com/p/Priya-Shah/1",
        "https://contactout.com/priya-shah",
        "https://www.signalhire.com/profiles/priya",
        "https://www.lusha.com/profile/priya",
        "https://app.apollo.io/#/people/1",
    ],
)
async def test_directory_proof_is_refused_with_what_to_do(authenticated_async_context, url):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        resp = await _add_contact(client, app_id, source_url=url)
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"].lower()
        assert "source_url" in detail and "never proves" in detail and "company's own page" in detail
        assert not (await client.get("/api/people")).json()["people"]


@pytest.mark.asyncio
async def test_company_pages_and_linkedin_still_count(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        for i, url in enumerate((
            "https://northwind.example/team",
            "https://www.linkedin.com/in/priya-shah",
            "https://github.com/priya",
            "https://notrocketreach.co.example/page",
        )):
            resp = await _add_contact(client, app_id, name=f"Ok Person {i}", source_url=url)
            assert resp.status_code == 201, resp.text


@pytest.mark.asyncio
async def test_update_contact_refuses_a_directory_page_too(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        added = await _add_contact(client, app_id, source_url="https://northwind.example/team")
        cid = added.json()["contact_id"]
        bad = await client.patch(f"/api/contacts/{cid}", json={"source_url": "https://theorg.com/org/northwind"})
        assert bad.status_code == 422 and "never proves" in bad.text
        good = await client.patch(f"/api/contacts/{cid}", json={"source_url": "https://northwind.example/blog/7"})
        assert good.status_code == 200 and good.json()["source_url"] == "https://northwind.example/blog/7"


@pytest.mark.asyncio
async def test_a_malformed_url_is_a_422_not_a_500(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        resp = await _add_contact(client, app_id, source_url="http://[bad")
        assert resp.status_code == 422 and "not a valid url" in resp.text.lower(), resp.text
