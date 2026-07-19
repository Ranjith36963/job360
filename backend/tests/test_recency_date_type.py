"""Regression: ``Job.date_found`` must be a STRING, or recency silently scores 0.

The bug this pins
-----------------
``Job.date_found`` is declared ``str`` (models.py:23). ``workers/tasks.py`` built
its Job with ``date_found=_parse_dt(...)``, which returns a ``datetime``.

Nothing crashed. ``skill_matcher._recency_score`` does
``datetime.fromisoformat(date_found)``, a ``datetime`` raises ``TypeError``, and
the surrounding ``except (ValueError, TypeError): return 0`` swallowed it.

Net effect: every job scored through ``score_and_ingest`` got ZERO freshness
points. A job posted an hour ago ranked identically to one from last week, and
there was no error anywhere to notice — the score was simply, quietly, wrong.

That is why these tests assert on the VALUE, not just that the call succeeds
(CLAUDE.md rule #21: value-presence beats schema-presence). A test that only
checked "returns an int" would have passed against the bug.
"""

from datetime import datetime, timedelta, timezone

from src.models import Job
from src.services.skill_matcher import _recency_score, recency_score_for_job


def _job(**kw):
    base = dict(
        title="Data Engineer",
        company="Acme",
        apply_url="https://example.test/1",
        source="test",
        date_found="",
    )
    base.update(kw)
    return Job(**base)


def test_recency_scores_a_fresh_iso_string() -> None:
    """Baseline: a fresh ISO string must score > 0, or the rest proves nothing."""
    fresh = datetime.now(timezone.utc).isoformat()
    assert _recency_score(fresh) > 0


def test_recency_is_zero_for_a_datetime_object() -> None:
    """A datetime silently scores 0 — this is the trap, pinned so it stays visible.

    We are not asserting desired behaviour here; we are documenting the sharp
    edge. ``_recency_score`` takes a STRING. Hand it a datetime and you get 0
    with no exception, which is exactly how the bug hid.
    """
    now = datetime.now(timezone.utc)
    assert _recency_score(now) == 0  # type: ignore[arg-type]


def test_job_built_with_datetime_date_found_loses_all_recency() -> None:
    """The actual production path: Job(date_found=<datetime>) -> 0 recency."""
    now = datetime.now(timezone.utc)
    bad = _job(date_found=now)  # type: ignore[arg-type]
    assert recency_score_for_job(bad) == 0

    good = _job(date_found=now.isoformat())
    assert recency_score_for_job(good) > 0, (
        "a job found right now must earn recency points; if this is 0 the "
        "date_found type regressed back to datetime"
    )


def test_score_and_ingest_builds_job_with_string_date_found() -> None:
    """THE REGRESSION GUARD: tasks.py must hand Job a str, never a datetime.

    Asserts against the real helper tasks.py uses, so if someone reintroduces
    ``date_found=_parse_dt(row)`` this fails immediately.
    """
    from src.workers.tasks import _job_from_row, _parse_dt

    row = {
        "title": "Data Engineer",
        "company": "Acme",
        "apply_url": "https://example.test/1",
        "source": "test",
        "date_found": datetime.now(timezone.utc) - timedelta(hours=2),
        "location": "London",
        "description": "",
        "salary_min": None,
        "salary_max": None,
        "match_score": 0,
        "visa_flag": False,
    }

    job = _job_from_row(row)
    assert isinstance(job.date_found, str), (
        f"Job.date_found must be a str (models.py:23) — got {type(job.date_found).__name__}. "
        "A datetime here silently zeroes every recency score."
    )
    # and it must actually score, end to end
    assert recency_score_for_job(job) > 0

    # _parse_dt itself still returns a datetime — that is fine, it is a parser.
    assert isinstance(_parse_dt(row["date_found"]), datetime)
