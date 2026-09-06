"""Bring-a-job (POST /jobs/bring) — the pivot's front door.

Rule under test (product rule 4): the user brings the ad; we never source it,
and since slice 5 (#483) we never score it either. The route stores the paste
in the shared catalog under `source='user_brought'` (rule #10: no user_id on
`jobs`), births the Application, and — because bring-your-own-job is global —
never refuses on location.
"""
from __future__ import annotations

import pytest

from src.api import dependencies as api_deps

_AD = {
    "title": "Senior Python Engineer",
    "company": "Acme Robotics Ltd",
    "location": "Berlin, Germany",
    "apply_url": "https://acme.example/jobs/42",
    "description": (
        "We build warehouse robots. You will own the Python services that "
        "schedule fleets. Requirements: 5+ years Python, FastAPI, Postgres, "
        "Kubernetes. Hybrid, 3 days in the Berlin office. Salary EUR 90,000 - "
        "110,000 per year."
    ),
}


@pytest.mark.asyncio
async def test_bring_stores_the_ad_and_births_the_application(authenticated_async_context):
    async with authenticated_async_context() as client:
        resp = await client.post("/api/jobs/bring", json=_AD)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["existing"] is False
        job = body["job"]
        assert job["title"] == _AD["title"]
        assert job["company"] == _AD["company"]
        assert job["source"] == "user_brought"
        assert job["apply_url"] == _AD["apply_url"]

        # The Application is born in the same request — that id, not the job
        # id, is what the caller works against from here on.
        detail = await client.get(f"/api/applications/{body['application_id']}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["job_id"] == job["id"]
        assert body["status"] == "considering"

        # The ad reads back per-user, by job id, off the application.
        read_back = await client.get(f"/api/applications/job/{job['id']}")
        assert read_back.status_code == 200, read_back.text
        assert read_back.json()["description"] == _AD["description"]

    db = await api_deps.get_db()
    # Rule #10: shared catalog row, no per-user column.
    cur = await db._conn.execute("SELECT source FROM jobs WHERE id = ?", (job["id"],))
    assert (await cur.fetchone())[0] == "user_brought"
    # The per-user fact is the APPLICATION now — bring writes no feed row.
    # `user_feed` itself was dropped by the mission-sweep migration (0040), so
    # that now holds by construction rather than by query.


@pytest.mark.asyncio
async def test_bring_is_global_no_uk_door(authenticated_async_context):
    """A Berlin ad must be accepted, not refused by the UK gate (rule #30 is a
    door on the SEARCH pipeline; a job the user brings is theirs to track)."""
    async with authenticated_async_context() as client:
        resp = await client.post("/api/jobs/bring", json={**_AD, "location": "Tokyo, Japan"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["job"]["location"] == "Tokyo, Japan"


@pytest.mark.asyncio
async def test_bring_twice_lands_on_same_row(authenticated_async_context):
    async with authenticated_async_context() as client:
        first = await client.post("/api/jobs/bring", json=_AD)
        again = await client.post("/api/jobs/bring", json={**_AD, "description": "Re-pasted ad."})
        assert first.status_code == 200 and again.status_code == 200
        assert again.json()["existing"] is True
        assert again.json()["job"]["id"] == first.json()["job"]["id"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [
        {**_AD, "apply_url": "javascript:alert(1)"},     # only web links may become <a href>
        {**_AD, "apply_url": "/relative/path"},
        {**_AD, "title": "   "},                          # blank after strip
        {k: v for k, v in _AD.items() if k != "company"},  # missing required
        {**_AD, "description": "x" * 40_001},             # pasted-PDF guard
    ],
)
async def test_bring_rejects_bad_input(authenticated_async_context, bad):
    async with authenticated_async_context() as client:
        resp = await client.post("/api/jobs/bring", json=bad)
        assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_bring_requires_login(authenticated_async_context):
    async with authenticated_async_context() as client:
        client.cookies.clear()
        resp = await client.post("/api/jobs/bring", json=_AD)
        assert resp.status_code in (401, 403), resp.text
