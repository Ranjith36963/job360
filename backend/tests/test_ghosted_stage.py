"""Tests for the ``ghosted`` pipeline stage — the first-party ghost-job signal.

WHY THIS STAGE EXISTS, and why it is not just another label:

``rejected`` means the employer REPLIED no. ``ghosted`` means SILENCE — the
application went in and nothing ever came back. Only the second one is evidence
that a posting collected applications and answered nobody, which is what a ghost
job actually looks like from the applicant's side.

This matters because ``services/ghost_detection.py`` infers staleness from a
listing DISAPPEARING from its source — which usually means the role was FILLED,
i.e. the opposite of a ghost job. User-confirmed silence is the stronger, more
honest signal, and it is the seed of a dataset nobody else has (every published
ghost-job statistic today comes from surveys, not observed applications).

All HTTP calls use ``authenticated_async_context`` so the routes pass
``require_user`` (CLAUDE.md rule #12).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.api import dependencies as api_deps
from src.api.routes.pipeline import _VALID_STAGES
from src.repositories.database import JobDatabase


async def _insert_job_row(db: JobDatabase, *, title: str = "AI Engineer", company: str = "Ghost Corp") -> int:
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


def test_ghosted_is_a_valid_stage():
    """The stage must be accepted by the route's allow-list."""
    assert "ghosted" in _VALID_STAGES


def test_ghosted_is_distinct_from_rejected():
    """Both exist and are separate — collapsing them would destroy the signal.

    If ``ghosted`` were ever folded into ``rejected`` the dataset becomes
    worthless: "they said no" and "they said nothing" would be indistinguishable.
    """
    assert "rejected" in _VALID_STAGES
    assert "ghosted" in _VALID_STAGES
    assert len({"rejected", "ghosted"} & _VALID_STAGES) == 2


@pytest.mark.asyncio
async def test_application_can_be_marked_ghosted(authenticated_async_context):
    """apply → mark ghosted → the stage persists as 'ghosted'."""
    db = await api_deps.get_db()
    job_id = await _insert_job_row(db)

    async with authenticated_async_context() as client:
        resp = await client.post(f"/api/pipeline/{job_id}")
        assert resp.status_code == 200, resp.text

        resp = await client.post(f"/api/pipeline/{job_id}/advance", json={"stage": "ghosted"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["stage"] == "ghosted"

        # And it survives a re-read — this is a recorded signal, not UI state.
        resp = await client.get("/api/pipeline")
        assert resp.status_code == 200, resp.text
        rows = resp.json()
        entries = rows if isinstance(rows, list) else rows.get("applications", [])
        match = [a for a in entries if a["job_id"] == job_id]
        assert match, "ghosted application missing from the pipeline listing"
        assert match[0]["stage"] == "ghosted"


@pytest.mark.asyncio
async def test_ghosted_appears_in_stage_counts(authenticated_async_context):
    """Counts must include ghosted so the dataset is measurable."""
    db = await api_deps.get_db()
    job_id = await _insert_job_row(db, title="Data Scientist", company="Silent Ltd")

    async with authenticated_async_context() as client:
        await client.post(f"/api/pipeline/{job_id}")
        await client.post(f"/api/pipeline/{job_id}/advance", json={"stage": "ghosted"})

        resp = await client.get("/api/pipeline/counts")
        assert resp.status_code == 200, resp.text
        assert resp.json().get("ghosted", 0) >= 1


@pytest.mark.asyncio
async def test_invalid_stage_still_rejected(authenticated_async_context):
    """Adding 'ghosted' must not have opened the allow-list to anything else."""
    db = await api_deps.get_db()
    job_id = await _insert_job_row(db, title="QA Engineer", company="Bad Stage Inc")

    async with authenticated_async_context() as client:
        await client.post(f"/api/pipeline/{job_id}")
        resp = await client.post(f"/api/pipeline/{job_id}/advance", json={"stage": "haunted"})
        assert resp.status_code == 422, resp.text
        assert "haunted" in resp.json()["detail"]
