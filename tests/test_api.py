"""Tests for FastAPI backend API."""
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
    assert data["sources_total"] == 48


@pytest.mark.asyncio
async def test_sources_returns_48():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/sources")
    assert resp.status_code == 200
    assert len(resp.json()["sources"]) == 48


@pytest.mark.asyncio
async def test_jobs_list_empty():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/jobs")
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


@pytest.mark.asyncio
async def test_actions_counts_empty():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/actions/counts")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_profile_404_when_none():
    with patch("src.api.routes.profile.load_profile", return_value=None):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/profile")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_pipeline_counts_empty():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/pipeline/counts")
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("applied", 0) == 0


@pytest.mark.asyncio
async def test_pipeline_list_empty():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/pipeline")
    assert resp.status_code == 200
    assert resp.json()["applications"] == []


@pytest.mark.asyncio
async def test_full_api_workflow():
    """Integration test: health → status → sources → jobs → actions → pipeline → profile."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Health
        resp = await client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

        # Status
        resp = await client.get("/api/status")
        assert resp.status_code == 200
        assert resp.json()["sources_total"] == 48

        # Sources
        resp = await client.get("/api/sources")
        assert resp.status_code == 200
        assert len(resp.json()["sources"]) == 48

        # Jobs (empty DB)
        resp = await client.get("/api/jobs")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

        # Jobs export (empty CSV)
        resp = await client.get("/api/jobs/export")
        assert resp.status_code == 200
        assert "text/csv" in resp.headers.get("content-type", "")

        # Action counts (empty)
        resp = await client.get("/api/actions/counts")
        assert resp.status_code == 200

        # Actions list (empty)
        resp = await client.get("/api/actions")
        assert resp.status_code == 200

        # Pipeline counts (empty)
        resp = await client.get("/api/pipeline/counts")
        assert resp.status_code == 200

        # Pipeline list (empty)
        resp = await client.get("/api/pipeline")
        assert resp.status_code == 200
        assert resp.json()["applications"] == []

        # Pipeline reminders (empty)
        resp = await client.get("/api/pipeline/reminders")
        assert resp.status_code == 200

        # Profile (404 when none)
        with patch("src.api.routes.profile.load_profile", return_value=None):
            resp = await client.get("/api/profile")
            assert resp.status_code == 404
