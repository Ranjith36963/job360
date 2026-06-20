"""Engine 2 dim-scoring id bug — the enrichment lookup is keyed on job.id, so
a Job reconstructed for re-scoring MUST carry its id or all enrichment dims
(seniority/salary/visa/workplace) silently score 0.

Root cause: `_personalize_dims` (jobs.py) rebuilt the Job without `id`.
"""
from __future__ import annotations

import pytest

from src.api.routes.jobs import _row_to_scoring_job


def test_row_to_scoring_job_sets_id_for_enrichment_lookup():
    """The reconstructed Job must carry row['id'] — the enrichment lookup keys
    on it (without it, every enrichment dim scores 0)."""
    row = {
        "id": 42,
        "title": "ML Engineer",
        "company": "Acme",
        "apply_url": "https://example.com/1",
        "source": "greenhouse",
        "date_found": "2026-01-01",
        "location": "London",
        "description": "python pytorch",
        "salary_min": 50000,
        "salary_max": 70000,
        "posted_at": None,
        "date_confidence": "low",
    }
    job = _row_to_scoring_job(row)
    assert getattr(job, "id", None) == 42
    assert job.title == "ML Engineer"
    assert job.location == "London"
    assert job.salary_min == 50000


def test_row_to_scoring_job_handles_missing_id():
    job = _row_to_scoring_job(
        {"title": "X", "company": "Y", "apply_url": "u", "source": "s", "date_found": "d"}
    )
    assert getattr(job, "id", None) is None
    assert job.title == "X"


@pytest.mark.asyncio
async def test_update_job_scores_persists_match_and_dims(tmp_path):
    """After re-scoring with enrichment, the new match_score + dim columns must
    persist back to the catalog row (so the feed/display reflect the dims)."""
    from src.models import Job
    from src.repositories.database import JobDatabase

    db = JobDatabase(str(tmp_path / "t.db"))
    await db.init_db()
    try:
        job = Job(
            title="ML Engineer",
            company="Acme",
            apply_url="https://example.com/1",
            source="greenhouse",
            date_found="2026-01-01",
            match_score=30,
        )
        await db.insert_job(job)
        await db.commit()

        cur = await db._conn.execute(
            "SELECT id FROM jobs WHERE normalized_company = ? AND normalized_title = ?",
            job.normalized_key(),
        )
        jid = (await cur.fetchone())[0]

        # Simulate a post-enrichment re-score that lifted the dims.
        job.id = jid  # type: ignore[attr-defined]
        job.match_score = 72
        job.seniority_score = 8
        job.role = 20

        await db.update_job_scores(job)
        await db.commit()

        cur = await db._conn.execute(
            "SELECT match_score, seniority_score, role FROM jobs WHERE id = ?", (jid,)
        )
        row = await cur.fetchone()
        assert row[0] == 72
        assert row[1] == 8
        assert row[2] == 20
    finally:
        await db.close()
