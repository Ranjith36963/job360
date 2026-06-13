"""Tests for src/services/rescore.py and JobDatabase.get_catalog_jobs_for_rescore."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from src.models import Job
from src.repositories.database import JobDatabase
from src.services.profile.models import SearchConfig
from src.services.skill_matcher import JobScorer


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_NOW = datetime.now(timezone.utc).isoformat()


@pytest.fixture
def db():
    database = JobDatabase(":memory:")
    asyncio.run(database.init_db())
    yield database
    asyncio.run(database.close())


def _make_job(**overrides):
    defaults = dict(
        title="ML Engineer",
        company="Acme Corp",
        apply_url="https://example.com/job",
        source="reed",
        date_found=_NOW,
        location="London, UK",
        description="Python machine learning role.",
    )
    defaults.update(overrides)
    return Job(**defaults)


def _make_scorer():
    config = SearchConfig(
        job_titles=["ML Engineer", "AI Engineer"],
        primary_skills=["python", "machine learning"],
        secondary_skills=["pytorch"],
    )
    return JobScorer(config)


# ---------------------------------------------------------------------------
# score_catalog_row tests
# ---------------------------------------------------------------------------


def test_score_catalog_row_returns_non_none_match_score():
    """score_catalog_row produces a ScoreBreakdown with a non-None match_score."""
    from src.services.rescore import score_catalog_row

    scorer = _make_scorer()
    row = {
        "title": "ML Engineer",
        "company": "Acme",
        "apply_url": "https://example.com/1",
        "source": "reed",
        "date_found": _NOW,
        "location": "London, UK",
        "description": "Python machine learning role with pytorch.",
        "salary_min": None,
        "salary_max": None,
        "posted_at": None,
        "date_confidence": "low",
    }
    breakdown = score_catalog_row(scorer, row)
    assert breakdown is not None
    assert breakdown.match_score is not None
    assert isinstance(breakdown.match_score, int)


def test_score_catalog_row_matching_job_scores_above_threshold():
    """A clearly matching row should score well above 0."""
    from src.services.rescore import score_catalog_row

    scorer = _make_scorer()
    row = {
        "title": "Senior ML Engineer",
        "company": "DeepMind",
        "apply_url": "https://example.com/2",
        "source": "greenhouse",
        "date_found": _NOW,
        "location": "London, UK",
        "description": "We need an ML Engineer with deep Python and machine learning skills.",
        "salary_min": 70000,
        "salary_max": 110000,
        "posted_at": None,
        "date_confidence": "medium",
    }
    breakdown = score_catalog_row(scorer, row)
    assert breakdown.match_score > 0


def test_score_catalog_row_tolerates_missing_optional_fields():
    """score_catalog_row must not raise when optional fields are absent in the row."""
    from src.services.rescore import score_catalog_row

    scorer = _make_scorer()
    # Only required fields
    row = {
        "title": "AI Engineer",
        "company": "Corp",
        "apply_url": "https://example.com/3",
        "source": "lever",
        "date_found": _NOW,
    }
    breakdown = score_catalog_row(scorer, row)
    assert breakdown is not None


# ---------------------------------------------------------------------------
# get_catalog_jobs_for_rescore loader tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_catalog_loader_returns_inserted_jobs(db):
    """Insert 3 jobs, assert loader returns 3 dicts."""
    for i in range(3):
        await db.insert_job(_make_job(
            title=f"Engineer {i}",
            company=f"Corp{i}",
            apply_url=f"https://example.com/{i}",
        ))
    rows = await db.get_catalog_jobs_for_rescore()
    assert len(rows) == 3


@pytest.mark.asyncio
async def test_catalog_loader_respects_limit(db):
    """Insert 3 jobs, limit=2 must return exactly 2."""
    for i in range(3):
        await db.insert_job(_make_job(
            title=f"Role {i}",
            company=f"Firm{i}",
            apply_url=f"https://example.com/limit/{i}",
        ))
    rows = await db.get_catalog_jobs_for_rescore(limit=2)
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_catalog_loader_returns_expected_keys(db):
    """Each returned dict must contain all keys needed by score_catalog_row."""
    await db.insert_job(_make_job())
    rows = await db.get_catalog_jobs_for_rescore()
    assert len(rows) == 1
    required_keys = {
        "id", "title", "company", "apply_url", "source", "date_found",
        "location", "description", "salary_min", "salary_max",
        "posted_at", "date_confidence",
    }
    assert required_keys <= set(rows[0].keys())


@pytest.mark.asyncio
async def test_catalog_loader_empty_when_no_jobs(db):
    """No jobs in DB → empty list, no error."""
    rows = await db.get_catalog_jobs_for_rescore()
    assert rows == []
