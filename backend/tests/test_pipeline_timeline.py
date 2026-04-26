"""Tests for Step-3 B-06..B-08: application stage history, timeline endpoint,
and notes archiving.

All HTTP calls use the authenticated_async_context fixture so the pipeline
routes pass require_user (CLAUDE.md rule #12). DB rows are inserted directly
via api_deps.get_db() — the same pattern used in test_api.py.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from src.api import dependencies as api_deps
from src.repositories.database import JobDatabase

# ── helpers ──────────────────────────────────────────────────────────────────


async def _insert_job_row(db: JobDatabase, *, title: str = "ML Engineer", company: str = "Acme AI") -> int:
    """Insert a minimal job row and return its id."""
    now = datetime(2026, 4, 23, 12, 0, 0, tzinfo=timezone.utc).isoformat()
    suffix = title.lower().replace(" ", "_")
    cur = await db._conn.execute(
        """INSERT INTO jobs
           (title, company, location, description, apply_url, source, date_found,
            match_score, visa_flag, experience_level,
            normalized_company, normalized_title, first_seen,
            first_seen_at, last_seen_at, date_confidence, staleness_state)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            title,
            company,
            "London, UK",
            "A test job",
            f"https://example.com/jobs/{suffix}",
            "greenhouse",
            now,
            80,
            0,
            "mid",
            company.lower(),
            title.lower(),
            now,
            now,
            now,
            "high",
            "active",
        ),
    )
    await db._conn.commit()
    return cur.lastrowid  # type: ignore[return-value]


# ── test_timeline_creates_entry_on_advance ───────────────────────────────────


@pytest.mark.asyncio
async def test_timeline_creates_entry_on_advance(authenticated_async_context):
    """Advance one stage → GET /pipeline/{id}/timeline returns exactly 1 entry."""
    db = await api_deps.get_db()
    job_id = await _insert_job_row(db)

    async with authenticated_async_context() as client:
        # Create the application first.
        resp = await client.post(f"/api/pipeline/{job_id}")
        assert resp.status_code == 200, resp.text

        # Advance it to "interview".
        resp = await client.post(f"/api/pipeline/{job_id}/advance", json={"stage": "interview"})
        assert resp.status_code == 200, resp.text

        # Timeline should have one entry.
        resp = await client.get(f"/api/pipeline/{job_id}/timeline")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["job_id"] == job_id
        assert len(data["timeline"]) == 1
        entry = data["timeline"][0]
        assert entry["to_stage"] == "interview"
        assert entry["from_stage"] == "applied"


# ── test_timeline_multiple_advances ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_timeline_multiple_advances(authenticated_async_context):
    """Two advances → timeline returns 2 entries in ascending order."""
    db = await api_deps.get_db()
    job_id = await _insert_job_row(db, title="Data Engineer", company="DataCo")

    async with authenticated_async_context() as client:
        await client.post(f"/api/pipeline/{job_id}")

        # First advance: applied → interview
        resp = await client.post(f"/api/pipeline/{job_id}/advance", json={"stage": "interview"})
        assert resp.status_code == 200

        # Second advance: interview → offer
        resp = await client.post(f"/api/pipeline/{job_id}/advance", json={"stage": "offer"})
        assert resp.status_code == 200

        resp = await client.get(f"/api/pipeline/{job_id}/timeline")
        assert resp.status_code == 200
        data = resp.json()
        timeline = data["timeline"]
        assert len(timeline) == 2
        # Entries must be in chronological order.
        assert timeline[0]["to_stage"] == "interview"
        assert timeline[1]["to_stage"] == "offer"
        assert timeline[1]["from_stage"] == "interview"


# ── test_timeline_404_no_application ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_timeline_404_no_application(authenticated_async_context):
    """GET timeline for a job that has no application → 404."""
    async with authenticated_async_context() as client:
        resp = await client.get("/api/pipeline/999999/timeline")
    assert resp.status_code == 404


# ── test_timeline_idor ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_timeline_idor(authenticated_async_context):
    """User B cannot see user A's timeline — returns 404 (existence-hiding)."""
    from contextlib import asynccontextmanager

    from fastapi.testclient import TestClient
    from httpx import ASGITransport, AsyncClient

    from src.api.main import app

    # User A: create an application and advance it.
    db = await api_deps.get_db()
    job_id = await _insert_job_row(db, title="DevOps Eng", company="OpsLtd")

    async with authenticated_async_context() as client_a:
        await client_a.post(f"/api/pipeline/{job_id}")
        await client_a.post(f"/api/pipeline/{job_id}/advance", json={"stage": "interview"})
        # User A can see their own timeline.
        resp = await client_a.get(f"/api/pipeline/{job_id}/timeline")
        assert resp.status_code == 200

    # Register user B using the SAME app (so same DB / session store).
    @asynccontextmanager
    async def _noop_lifespan(a):
        yield

    app.router.lifespan_context = _noop_lifespan  # type: ignore[assignment]

    sync_client_b = TestClient(app)
    r = sync_client_b.post(
        "/api/auth/register",
        json={"email": "userb_idor_timeline@example.com", "password": "password_B_123"},
    )
    assert r.status_code == 201, r.text
    session_b = sync_client_b.cookies.get("job360_session")
    sync_client_b.close()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={"job360_session": session_b},
    ) as client_b:
        # User B tries to see user A's timeline — must get 404.
        resp = await client_b.get(f"/api/pipeline/{job_id}/timeline")
    assert resp.status_code == 404


# ── test_notes_update_archives_previous ──────────────────────────────────────


@pytest.mark.asyncio
async def test_notes_update_archives_previous(authenticated_async_context):
    """PATCH notes — previous note is archived in notes_history JSON field."""
    db = await api_deps.get_db()
    job_id = await _insert_job_row(db, title="Backend Eng", company="BackCo")

    async with authenticated_async_context() as client:
        # Create application.
        resp = await client.post(f"/api/pipeline/{job_id}")
        assert resp.status_code == 200

        # Set an initial note.
        resp = await client.patch(f"/api/pipeline/{job_id}/notes", json={"notes": "First note"})
        assert resp.status_code == 200

        # Update the note — previous should now be in notes_history.
        resp = await client.patch(f"/api/pipeline/{job_id}/notes", json={"notes": "Second note"})
        assert resp.status_code == 200
        assert resp.json()["notes"] == "Second note"

        # Verify notes_history directly in DB.
        cursor = await db._conn.execute(
            "SELECT notes, notes_history FROM applications WHERE job_id = ?",
            (job_id,),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] == "Second note"
        history = json.loads(row[1] or "[]")
        assert len(history) == 1
        assert history[0]["note"] == "First note"
        assert "timestamp" in history[0]


# ── test_notes_update_404 ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_notes_update_404(authenticated_async_context):
    """PATCH notes on a non-existent application → 404."""
    async with authenticated_async_context() as client:
        resp = await client.patch("/api/pipeline/999999/notes", json={"notes": "hello"})
    assert resp.status_code == 404
