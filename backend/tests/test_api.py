"""Tests for the FastAPI backend's remaining always-on routes.

Slice 5 (#483) deleted `/api/jobs*`, `/api/actions*`, `/api/search*`,
`/api/runs*` and `/api/sources`, and the mission sweep deleted the Kanban
`/api/pipeline*` API — with them went almost every test that used to live
here. What is left is the small set that is still true: the public probes,
the spine reads, the profile 404, and the per-request-connection guard.
"""

import pytest
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
async def test_status_returns_counts(authenticated_async_context):
    # Use the fixture's isolated, fully-migrated DB rather than the ambient
    # data/jobs.db: the bare AsyncClient hit whatever DB_PATH resolved to (a
    # stale local data/jobs.db -> 500), and writing to the real dev DB is a
    # data-pollution risk. /api/status is public, so the authenticated
    # client's cookie is simply unused.
    async with authenticated_async_context() as client:
        resp = await client.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "jobs_total" in data
    # Slice 5 — nothing fetches jobs, so there is no source count and no last
    # run to report. Their absence is the assertion.
    assert "sources_total" not in data
    assert "last_run" not in data


@pytest.mark.asyncio
async def test_profile_404_when_none(authenticated_async_context):
    """With no profile row for the authenticated user, GET /profile is 404."""
    async with authenticated_async_context() as client:
        resp = await client.get("/api/profile")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_full_api_workflow(authenticated_async_context):
    """Integration smoke: health → status → applications → receipts → profile."""
    async with authenticated_async_context() as client:
        resp = await client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

        resp = await client.get("/api/status")
        assert resp.status_code == 200

        # The spine, empty.
        resp = await client.get("/api/applications")
        assert resp.status_code == 200
        assert resp.json()["applications"] == []

        resp = await client.get("/api/receipts")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

        # Profile (authed — no row for fixture-user, so 404)
        resp = await client.get("/api/profile")
        assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_request_db_yields_own_connection_not_the_singleton():
    """docs/fable/02 P0 — each request gets its OWN connection via get_request_db,
    distinct from the shared boot singleton, so concurrent requests can't collide on
    one psycopg async connection ('another operation is already in progress')."""
    import src.api.dependencies as _deps

    singleton = await _deps.get_db()
    conns = []
    for _ in range(2):
        agen = _deps.get_request_db()
        db = await agen.__anext__()
        conns.append(db._conn)
        # exhaust the generator so the finally-block closes the connection
        try:
            await agen.__anext__()
        except StopAsyncIteration:
            pass
    assert conns[0] is not conns[1], "each request must get a fresh connection"
    assert conns[0] is not singleton._conn, "request conn must not be the singleton"
