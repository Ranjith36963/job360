"""Tests for FastAPI backend API.

Batch 3.5.4 rehab: routes that require auth (added in Batch 3.5 IDOR
fixes) now use the `authenticated_async_context` fixture from conftest.py.
The 3 always-public endpoints (/health, /status, /sources) stay on the
bare ASGITransport pattern — they don't need auth.
"""
import pytest
from unittest.mock import patch
from httpx import ASGITransport, AsyncClient
from src.api.main import app


@pytest.mark.asyncio
async def test_health_returns_ok():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["version"] == "1.0.0"


@pytest.mark.asyncio
async def test_status_returns_counts():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "jobs_total" in data
    assert data["sources_total"] == 50


@pytest.mark.asyncio
async def test_sources_returns_50():
    """Batch 3 raised the source count from 48 to 50 (+5 new -3 dropped)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/sources")
    assert resp.status_code == 200
    assert len(resp.json()["sources"]) == 50


@pytest.mark.asyncio
async def test_jobs_list_empty(authenticated_async_context):
    async with authenticated_async_context() as client:
        resp = await client.get("/api/jobs")
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


@pytest.mark.asyncio
async def test_actions_counts_empty(authenticated_async_context):
    async with authenticated_async_context() as client:
        resp = await client.get("/api/actions/counts")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_profile_404_when_none(authenticated_async_context):
    """With no profile row for the authenticated user, GET /profile is 404."""
    async with authenticated_async_context() as client:
        # The fresh fixture-user has no profile row yet, so the real
        # load_profile returns None and the route raises 404 — no need
        # to mock load_profile anymore (Batch 3.5.2 made storage
        # per-user).
        resp = await client.get("/api/profile")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_pipeline_counts_empty(authenticated_async_context):
    async with authenticated_async_context() as client:
        resp = await client.get("/api/pipeline/counts")
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("applied", 0) == 0


@pytest.mark.asyncio
async def test_pipeline_list_empty(authenticated_async_context):
    async with authenticated_async_context() as client:
        resp = await client.get("/api/pipeline")
    assert resp.status_code == 200
    assert resp.json()["applications"] == []


@pytest.mark.asyncio
async def test_full_api_workflow(authenticated_async_context):
    """Integration test: health → status → sources → jobs → actions → pipeline → profile."""
    async with authenticated_async_context() as client:
        # Health (public)
        resp = await client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

        # Status (public)
        resp = await client.get("/api/status")
        assert resp.status_code == 200
        assert resp.json()["sources_total"] == 50

        # Sources (public)
        resp = await client.get("/api/sources")
        assert resp.status_code == 200
        assert len(resp.json()["sources"]) == 50

        # Jobs (authed, empty DB)
        resp = await client.get("/api/jobs")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

        # Jobs export (authed, empty CSV)
        resp = await client.get("/api/jobs/export")
        assert resp.status_code == 200
        assert "text/csv" in resp.headers.get("content-type", "")

        # Action counts (authed, empty)
        resp = await client.get("/api/actions/counts")
        assert resp.status_code == 200

        # Actions list (authed, empty)
        resp = await client.get("/api/actions")
        assert resp.status_code == 200

        # Pipeline counts (authed, empty)
        resp = await client.get("/api/pipeline/counts")
        assert resp.status_code == 200

        # Pipeline list (authed, empty)
        resp = await client.get("/api/pipeline")
        assert resp.status_code == 200
        assert resp.json()["applications"] == []

        # Pipeline reminders (authed, empty)
        resp = await client.get("/api/pipeline/reminders")
        assert resp.status_code == 200

        # Profile (authed — no row for fixture-user, so 404)
        resp = await client.get("/api/profile")
        assert resp.status_code == 404
