"""Step-1.5 S3-A,B,C — profile version + JSON Resume endpoints.

Cohort Z agent-Endpoints. The storage helpers (list_profile_versions,
restore_profile_version, CVData.to_json_resume) already existed; these
tests prove they are now reachable through authenticated HTTP under
proper user_id scoping.
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_list_versions_returns_empty_for_new_user(authenticated_async_context):
    """A fresh user with no profile yet must get a 200 + empty list, not 404."""
    async with authenticated_async_context() as client:
        resp = await client.get("/api/profile/versions")
    assert resp.status_code == 200
    body = resp.json()
    assert body["versions"] == []
    assert body["total"] == 0


@pytest.mark.asyncio
async def test_list_versions_returns_user_snapshots(authenticated_async_context):
    """Saving a profile creates a snapshot row that surfaces here."""
    async with authenticated_async_context() as client:
        resp = await client.post(
            "/api/profile",
            data={"preferences": '{"target_job_titles": ["Engineer"]}'},
        )
        assert resp.status_code == 200
        resp = await client.get("/api/profile/versions")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 1
    first = body["versions"][0]
    assert "id" in first
    assert "created_at" in first
    assert "source_action" in first
    assert "cv_data" in first
    assert "preferences" in first


@pytest.mark.asyncio
async def test_restore_version_404_when_missing(authenticated_async_context):
    """Restoring a non-existent version id is a 404 (existence-hiding pattern)."""
    async with authenticated_async_context() as client:
        resp = await client.post("/api/profile/versions/9999/restore")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_restore_version_round_trip(authenticated_async_context):
    """Save → list → restore produces a 200 with the restored profile body."""
    async with authenticated_async_context() as client:
        save_resp = await client.post(
            "/api/profile",
            data={"preferences": '{"target_job_titles": ["Restored Role"]}'},
        )
        assert save_resp.status_code == 200
        list_resp = await client.get("/api/profile/versions")
        version_id = list_resp.json()["versions"][0]["id"]
        restore_resp = await client.post(f"/api/profile/versions/{version_id}/restore")
    assert restore_resp.status_code == 200
    body = restore_resp.json()
    assert "summary" in body
    assert "preferences" in body


@pytest.mark.asyncio
async def test_json_resume_404_when_no_profile(authenticated_async_context):
    """No profile → 404 (existence-hiding under auth)."""
    async with authenticated_async_context() as client:
        resp = await client.get("/api/profile/json-resume")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_json_resume_returns_canonical_schema(authenticated_async_context):
    """A profile with a name must export the JSON Resume root keys."""
    async with authenticated_async_context() as client:
        await client.post(
            "/api/profile",
            data={
                "preferences": '{"target_job_titles": ["Engineer"]}',
            },
        )
        resp = await client.get("/api/profile/json-resume")
    assert resp.status_code == 200
    body = resp.json()
    assert "resume" in body
    resume = body["resume"]
    # JSON Resume canonical root keys (https://jsonresume.org/schema/)
    for key in ("basics", "work", "education", "skills"):
        assert key in resume
