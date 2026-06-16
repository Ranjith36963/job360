"""Step-1.5 S3-A,B,C — profile version + JSON Resume endpoints.

Cohort Z agent-Endpoints. The storage helpers (list_profile_versions,
restore_profile_version, CVData.to_json_resume) already existed; these
tests prove they are now reachable through authenticated HTTP under
proper user_id scoping.

V-04 upload cap tests are also included here (same endpoint, same fixture).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.services.profile.models import CVData


@pytest.mark.asyncio
async def test_storage_db_path_redirected_to_test_db(authenticated_async_context):
    """Regression guard for the import-time DB_PATH binding bug.

    ``services/profile/storage.py`` does ``from src.core.settings import
    DB_PATH``, binding the value at import. The fixture must redirect that
    module's ``DB_PATH`` to the per-test migrated DB; otherwise profile queries
    hit the production DB and fail in full-suite ordering with
    ``no such table: user_profiles``. Asserting equality with the patched
    ``settings.DB_PATH`` proves the redirect reached the storage module.
    """
    from src.core import settings
    from src.services.profile import storage

    assert storage.DB_PATH == settings.DB_PATH


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


# ── V-04: upload cap + MIME allowlist ────────────────────────────────────────


@pytest.mark.asyncio
async def test_cv_upload_rejects_oversized_file(authenticated_async_context):
    """A CV file larger than 10 MB must be rejected with HTTP 413."""
    oversized_content = b"%PDF-1.4 " + b"x" * (10 * 1024 * 1024 + 1)
    async with authenticated_async_context() as client:
        resp = await client.post(
            "/api/profile",
            files={"cv": ("big.pdf", oversized_content, "application/pdf")},
        )
    assert resp.status_code == 413
    assert "10 MB" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_cv_upload_rejects_wrong_mime(authenticated_async_context):
    """A CV with an unsupported extension/content-type must return HTTP 415."""
    async with authenticated_async_context() as client:
        resp = await client.post(
            "/api/profile",
            files={"cv": ("resume.txt", b"hello", "text/plain")},
        )
    assert resp.status_code == 415
    assert "PDF or DOCX" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_cv_upload_accepts_valid_pdf(authenticated_async_context):
    """A small valid PDF passes the size+MIME gate and reaches the parser."""
    fake_cv = CVData(raw_text="Software Engineer with 5 years Python experience.")

    with patch(
        "src.api.routes.profile.parse_cv_async",
        new=AsyncMock(return_value=fake_cv),
    ):
        async with authenticated_async_context() as client:
            resp = await client.post(
                "/api/profile",
                files={"cv": ("cv.pdf", b"%PDF-1.4 minimal", "application/pdf")},
            )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_linkedin_upload_rejects_oversized_file(authenticated_async_context):
    """A LinkedIn PDF larger than 10 MB must be rejected with HTTP 413."""
    oversized_content = b"%PDF-1.4 " + b"x" * (10 * 1024 * 1024 + 1)
    async with authenticated_async_context() as client:
        resp = await client.post(
            "/api/profile/linkedin",
            files={"file": ("linkedin.pdf", oversized_content, "application/pdf")},
        )
    assert resp.status_code == 413
    assert "10 MB" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_linkedin_upload_rejects_wrong_type(authenticated_async_context):
    """A LinkedIn upload with a non-PDF file must return HTTP 415."""
    async with authenticated_async_context() as client:
        resp = await client.post(
            "/api/profile/linkedin",
            files={"file": ("profile.docx", b"PK\x03\x04", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        )
    assert resp.status_code == 415
