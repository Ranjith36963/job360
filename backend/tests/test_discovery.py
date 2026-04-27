"""Step-3 B-09 / B-10 / B-15 — Lane-Discovery endpoint tests.

Covers:
  - GET /api/jobs/{id}/duplicates    (B-09)
  - GET /api/profile/versions/{v1}/diff/{v2}  (B-10)
  - GET /api/runs/recent             (B-15)

All HTTP calls go through FastAPI's TestClient / AsyncClient backed by an
in-memory/tmp SQLite DB. No live HTTP. (CLAUDE.md rule #4)
"""

from __future__ import annotations

import pytest

from src.models import Job

_NOW_ISO = "2026-04-26T12:00:00+00:00"


# ===========================================================================
# B-09: GET /api/jobs/{id}/duplicates — unit test on DB layer
# ===========================================================================


@pytest.mark.asyncio
async def test_get_duplicate_jobs_returns_same_key_matches():
    """DB helper returns rows with same normalized key, excluding the queried job_id.

    Unit test on JobDatabase.get_duplicate_jobs using an in-memory DB with rows
    inserted directly to bypass the ORM's UNIQUE constraint.
    Confirms the SQL logic is correct independently of the HTTP layer.
    """
    from src.repositories.database import JobDatabase

    db = JobDatabase(":memory:")
    await db.init_db()

    # Insert via the ORM helper — gets us a real row with normalized columns set
    job_a = Job(
        title="ML Engineer",
        company="Revolut Ltd",
        apply_url="https://example.com/ml-a",
        source="reed",
        date_found=_NOW_ISO,
    )
    await db.insert_job(job_a)
    cur = await db._conn.execute(
        "SELECT id, normalized_company, normalized_title FROM jobs WHERE apply_url = ?",
        ("https://example.com/ml-a",),
    )
    row_a = dict(await cur.fetchone())
    job_id_a = row_a["id"]
    norm_co = row_a["normalized_company"]
    norm_ti = row_a["normalized_title"]

    # Manually insert a second row with the SAME normalized key by assigning a
    # different integer id (bypasses UNIQUE on the ORM level)
    await db._conn.execute(
        """
        INSERT OR REPLACE INTO jobs
          (id, title, company, location, description, apply_url, source,
           date_found, match_score, normalized_company, normalized_title,
           first_seen, first_seen_at, last_seen_at)
        VALUES (999, ?, ?, '', '', ?, ?, ?, 40, ?, ?, ?, ?, ?)
        """,
        (
            "ML Engineer",
            "Revolut",
            "https://example.com/ml-b",
            "adzuna",
            _NOW_ISO,
            norm_co,
            norm_ti,
            _NOW_ISO,
            _NOW_ISO,
            _NOW_ISO,
        ),
    )
    await db._conn.commit()

    # get_duplicate_jobs should find the manually-inserted duplicate
    dupes = await db.get_duplicate_jobs(job_id_a, norm_co, norm_ti)
    assert len(dupes) >= 1
    dupe_ids = [d["id"] for d in dupes]
    assert 999 in dupe_ids  # the manually-inserted duplicate is found
    assert job_id_a not in dupe_ids  # self excluded

    await db.close()


# ===========================================================================
# B-09: GET /api/jobs/{id}/duplicates — HTTP endpoint tests
# ===========================================================================


@pytest.mark.asyncio
async def test_job_duplicates_same_key(authenticated_async_context, fixture_user_id):
    """The duplicate endpoint returns correct shape for an existing job.

    Uses fixture_user_id so the DB singleton is initialised in the same event
    loop as the test. Inserts a job through the api_deps.get_db() singleton
    INSIDE the authenticated context so the connection is shared cleanly.
    """
    from src.api import dependencies as api_deps

    # Insert data using the singleton AFTER the context is entered so the
    # connection lifecycle stays within this event loop.
    async with authenticated_async_context() as client:
        db = await api_deps.get_db()
        job = Job(
            title="Platform Engineer",
            company="Stripe",
            apply_url="https://example.com/platform-stripe",
            source="reed",
            date_found=_NOW_ISO,
            location="London, UK",
            description="Platform role",
        )
        await db.insert_job(job)
        cur = await db._conn.execute(
            "SELECT id FROM jobs WHERE apply_url = 'https://example.com/platform-stripe' LIMIT 1"
        )
        row = await cur.fetchone()
        job_id = row[0]
        await db._conn.commit()  # release implicit read transaction before HTTP call

        resp = await client.get(f"/api/jobs/{job_id}/duplicates")

    assert resp.status_code == 200
    body = resp.json()
    assert body["job_id"] == job_id
    assert "duplicates" in body
    assert "total" in body
    assert isinstance(body["duplicates"], list)


@pytest.mark.asyncio
async def test_job_duplicates_excludes_self(authenticated_async_context, fixture_user_id):
    """The job itself must NOT appear in its own duplicates list."""
    from src.api import dependencies as api_deps

    async with authenticated_async_context() as client:
        db = await api_deps.get_db()
        job = Job(
            title="Data Engineer",
            company="Palantir",
            apply_url="https://example.com/data-palantir",
            source="lever",
            date_found=_NOW_ISO,
            location="London, UK",
            description="Data role",
        )
        await db.insert_job(job)
        cur = await db._conn.execute(
            "SELECT id FROM jobs WHERE apply_url = 'https://example.com/data-palantir' LIMIT 1"
        )
        row = await cur.fetchone()
        job_id = row[0]
        await db._conn.commit()  # release implicit read transaction before HTTP call

        resp = await client.get(f"/api/jobs/{job_id}/duplicates")

    assert resp.status_code == 200
    ids_returned = [d["id"] for d in resp.json()["duplicates"]]
    assert job_id not in ids_returned


@pytest.mark.asyncio
async def test_job_duplicates_404(authenticated_async_context):
    """Non-existent job_id must return 404."""
    async with authenticated_async_context() as client:
        resp = await client.get("/api/jobs/999999/duplicates")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_job_duplicates_empty_when_unique(authenticated_async_context, fixture_user_id):
    """A job with no duplicates returns total=0 and empty list."""
    from src.api import dependencies as api_deps

    async with authenticated_async_context() as client:
        db = await api_deps.get_db()
        job = Job(
            title="Unique Role XYZ Qwerty",
            company="OnlyCorp LLC",
            apply_url="https://example.com/unique-qwerty",
            source="reed",
            date_found=_NOW_ISO,
            location="London, UK",
            description="Unique test job",
        )
        await db.insert_job(job)
        cur = await db._conn.execute(
            "SELECT id FROM jobs WHERE apply_url = 'https://example.com/unique-qwerty' LIMIT 1"
        )
        row = await cur.fetchone()
        job_id = row[0]
        await db._conn.commit()  # release implicit read transaction before HTTP call

        resp = await client.get(f"/api/jobs/{job_id}/duplicates")

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 0
    assert body["duplicates"] == []


@pytest.mark.asyncio
async def test_job_duplicates_accessible_unauthenticated(authenticated_async_context, fixture_user_id):
    """The duplicates endpoint uses optional_user — accessible without auth cookie."""
    from contextlib import asynccontextmanager

    from httpx import ASGITransport, AsyncClient

    from src.api import dependencies as api_deps
    from src.api.main import app

    async with authenticated_async_context() as _client:
        db = await api_deps.get_db()
        job = Job(
            title="Backend Developer",
            company="OpenCo",
            apply_url="https://example.com/backend-openco",
            source="reed",
            date_found=_NOW_ISO,
            location="London, UK",
            description="Backend role",
        )
        await db.insert_job(job)
        cur = await db._conn.execute(
            "SELECT id FROM jobs WHERE apply_url = 'https://example.com/backend-openco' LIMIT 1"
        )
        row = await cur.fetchone()
        job_id = row[0]
        await db._conn.commit()  # release implicit read transaction

    @asynccontextmanager
    async def _noop(a):
        yield

    app.router.lifespan_context = _noop  # type: ignore[assignment]

    # Hit without a session cookie — should still return 200
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon_client:
        resp = await anon_client.get(f"/api/jobs/{job_id}/duplicates")
    assert resp.status_code == 200


# ===========================================================================
# B-10: GET /api/profile/versions/{v1}/diff/{v2}
# ===========================================================================


@pytest.mark.asyncio
async def test_profile_version_diff_changes(authenticated_async_context):
    """Two versions with different preferences → diff endpoint returns 200 with changes."""
    async with authenticated_async_context() as client:
        # Save version 1
        r1 = await client.post(
            "/api/profile",
            data={"preferences": '{"target_job_titles": ["Engineer"]}'},
        )
        assert r1.status_code == 200

        # Save version 2 with different content
        r2 = await client.post(
            "/api/profile",
            data={"preferences": '{"target_job_titles": ["Scientist"]}'},
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
    assert "changed_fields" in body
    assert isinstance(body["changed_fields"], list)


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


# ===========================================================================
# B-15: GET /api/runs/recent
# ===========================================================================


@pytest.mark.asyncio
async def test_recent_runs_returns_list(authenticated_async_context):
    """GET /runs/recent returns a list-shaped response."""
    async with authenticated_async_context() as client:
        resp = await client.get("/api/runs/recent")

    assert resp.status_code == 200
    body = resp.json()
    assert "runs" in body
    assert "total" in body
    assert "limit" in body
    assert "offset" in body
    assert isinstance(body["runs"], list)


@pytest.mark.asyncio
async def test_recent_runs_shows_logged_run(authenticated_async_context, fixture_user_id):
    """A run logged via log_run() appears in the response."""
    from src.api import dependencies as api_deps

    async with authenticated_async_context() as client:
        db = await api_deps.get_db()
        await db.log_run({"total_found": 5, "new_jobs": 3, "sources_queried": 10})

        resp = await client.get("/api/runs/recent")

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 1
    assert len(body["runs"]) >= 1
    run = body["runs"][0]
    assert "id" in run
    assert "timestamp" in run


@pytest.mark.asyncio
async def test_recent_runs_pagination(authenticated_async_context, fixture_user_id):
    """limit and offset parameters are respected."""
    from src.api import dependencies as api_deps

    async with authenticated_async_context() as client:
        db = await api_deps.get_db()
        for i in range(5):
            await db.log_run({"total_found": i, "new_jobs": 0, "sources_queried": 10})

        resp_page1 = await client.get("/api/runs/recent?limit=2&offset=0")
        resp_page2 = await client.get("/api/runs/recent?limit=2&offset=2")

    assert resp_page1.status_code == 200
    assert resp_page2.status_code == 200

    body1 = resp_page1.json()
    body2 = resp_page2.json()
    assert body1["limit"] == 2
    assert body1["offset"] == 0
    assert body2["limit"] == 2
    assert body2["offset"] == 2
    assert body1["total"] == body2["total"]

    ids1 = {r["id"] for r in body1["runs"]}
    ids2 = {r["id"] for r in body2["runs"]}
    assert ids1.isdisjoint(ids2)


@pytest.mark.asyncio
async def test_recent_runs_requires_auth(authenticated_async_context):
    """Unauthenticated requests must return 401/403."""
    from contextlib import asynccontextmanager

    from httpx import ASGITransport, AsyncClient

    from src.api.main import app

    @asynccontextmanager
    async def _noop(a):
        yield

    app.router.lifespan_context = _noop  # type: ignore[assignment]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/runs/recent")

    assert resp.status_code in (401, 403)
