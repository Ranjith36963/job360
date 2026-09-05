"""Step-3 B-10 — profile-version diff endpoint tests.

Covers `GET /api/profile/versions/{v1}/diff/{v2}`.

Slice 5 (#483) deleted the other two lanes this file used to cover —
`GET /api/jobs/{id}/duplicates` (cross-source dedup: there are no sources) and
`GET /api/runs/recent` (there are no runs).

All HTTP calls go through FastAPI's AsyncClient against a real Postgres
schema. No live HTTP. (CLAUDE.md rule #4)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

_NOW_ISO = "2026-04-26T12:00:00+00:00"


# ===========================================================================
# B-10: GET /api/profile/versions/{v1}/diff/{v2}
# ===========================================================================


@pytest.mark.asyncio
async def test_profile_version_diff_changes(authenticated_async_context):
    """Two versions with different CV text → diff endpoint DETECTS the change.

    The diff endpoint compares ``cv_data`` (profile_json) between versions, not
    ``preferences`` (see ``diff_profile_versions`` in routes/profile.py) — so the
    two saves below change the CV text (which lands in ``cv_data.raw_text``) to
    actually produce a non-empty diff. A shape-only check (`"changed_fields" in
    body`) would pass even for a diff engine that always returns `[]`; this
    asserts the specific changed field name is present.
    """
    with patch(
        "src.api.routes.profile.run_two_pass_extraction",
        new=AsyncMock(return_value=None),
    ):
        async with authenticated_async_context() as client:
            # Save version 1 with CV text A
            with patch(
                "src.api.routes.profile.extract_text",
                new=lambda path: "Software Engineer with Python experience.",
            ):
                r1 = await client.post(
                    "/api/profile",
                    files={"cv": ("cv1.pdf", b"%PDF-1.4 minimal", "application/pdf")},
                )
            assert r1.status_code == 200

            # Save version 2 with different CV text
            with patch(
                "src.api.routes.profile.extract_text",
                new=lambda path: "Data Scientist with R and machine learning experience.",
            ):
                r2 = await client.post(
                    "/api/profile",
                    files={"cv": ("cv2.pdf", b"%PDF-1.4 minimal", "application/pdf")},
                )
            assert r2.status_code == 200

            list_resp = await client.get("/api/profile/versions")
            assert list_resp.status_code == 200
            versions = list_resp.json()["versions"]
            assert len(versions) >= 2

            v_new = versions[0]["id"]
            v_old = versions[1]["id"]

            diff_resp = await client.get(f"/api/profile/versions/{v_old}/diff/{v_new}")

    assert diff_resp.status_code == 200
    body = diff_resp.json()
    assert body["version_id1"] == v_old
    assert body["version_id2"] == v_new
    assert "changes" in body
    # The changed CV text must actually be detected, not just an empty shape.
    assert "raw_text" in body["changed_fields"]
    assert body["changes"]["raw_text"]["from"] != body["changes"]["raw_text"]["to"]


@pytest.mark.asyncio
async def test_profile_version_diff_404_v1(authenticated_async_context):
    """Non-existent version_id1 → 404."""
    async with authenticated_async_context() as client:
        await client.post(
            "/api/profile",
            data={"preferences": '{"target_job_titles": ["Engineer"]}'},
        )
        list_resp = await client.get("/api/profile/versions")
        real_id = list_resp.json()["versions"][0]["id"]

        resp = await client.get(f"/api/profile/versions/999999/diff/{real_id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_profile_version_diff_404_v2(authenticated_async_context):
    """Non-existent version_id2 → 404."""
    async with authenticated_async_context() as client:
        await client.post(
            "/api/profile",
            data={"preferences": '{"target_job_titles": ["Engineer"]}'},
        )
        list_resp = await client.get("/api/profile/versions")
        real_id = list_resp.json()["versions"][0]["id"]

        resp = await client.get(f"/api/profile/versions/{real_id}/diff/999999")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_profile_version_diff_idor(authenticated_async_context):
    """Can't diff versions that don't belong to the caller → 404 (IDOR protection).

    The diff endpoint uses list_profile_versions(user_id) which is user-scoped.
    Any version_id not in the caller's list returns None → 404, regardless of
    whether it belongs to another user or simply doesn't exist.
    """
    async with authenticated_async_context() as client:
        # Create a real version for the authenticated user
        await client.post(
            "/api/profile",
            data={"preferences": '{"target_job_titles": ["Engineer"]}'},
        )
        list_resp = await client.get("/api/profile/versions")
        version_a = list_resp.json()["versions"][0]["id"]

        # Use a large made-up ID that wouldn't belong to this user
        version_other = 9_999_999

        # diff between user's version and a non-existent/other-user version → 404
        resp = await client.get(f"/api/profile/versions/{version_a}/diff/{version_other}")

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_profile_version_diff_requires_auth(authenticated_async_context):
    """The diff endpoint requires authentication — 401/403 without a session cookie."""
    from contextlib import asynccontextmanager

    from httpx import ASGITransport, AsyncClient

    from src.api.main import app

    @asynccontextmanager
    async def _noop(a):
        yield

    app.router.lifespan_context = _noop  # type: ignore[assignment]

    # Make the request WITHOUT the session cookie
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/profile/versions/1/diff/2")

    assert resp.status_code in (401, 403)
