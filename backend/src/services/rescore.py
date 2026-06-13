"""Shared scoring helper for catalog-row rescoring.

Provides ``score_catalog_row``, which reconstructs a ``Job`` from a plain
dict row (as returned by ``JobDatabase.get_catalog_jobs_for_rescore``) and
scores it with a prepared ``JobScorer``.

This module intentionally contains NO side effects, no DB writes, and no
network calls — it is a pure computation helper.
"""
from __future__ import annotations


def score_catalog_row(scorer, row: dict):
    """Score one stored catalog row against a prepared JobScorer.

    Reconstructs a Job exactly like api/routes/jobs.py does, then calls
    ``scorer.score(job)`` and returns the resulting ``ScoreBreakdown``.

    Args:
        scorer: a prepared ``JobScorer`` instance (already initialised with
            the user's ``SearchConfig``, optional ``user_preferences``, and
            optional ``enrichment_lookup``).
        row: a plain dict with the catalog columns produced by
            ``JobDatabase.get_catalog_jobs_for_rescore`` (title, company,
            apply_url, source, date_found, location, description,
            salary_min, salary_max, posted_at, date_confidence).

    Returns:
        A ``ScoreBreakdown`` namedtuple/dataclass as returned by
        ``JobScorer.score()``.
    """
    from src.models import Job  # lazy per rule #16

    job = Job(
        title=row.get("title", "") or "",
        company=row.get("company", "") or "",
        apply_url=row.get("apply_url", "") or "",
        source=row.get("source", "") or "",
        date_found=row.get("date_found", "") or "",
        location=row.get("location", "") or "",
        description=row.get("description", "") or "",
        salary_min=row.get("salary_min"),
        salary_max=row.get("salary_max"),
        posted_at=row.get("posted_at"),
        date_confidence=row.get("date_confidence") or "low",
    )
    return scorer.score(job)
