"""Pillar 2 Batch 2.1 — assert date_confidence labels for signal-less sources.

These tests read source files as text and grep the literal `date_confidence=`
assignments. That is the correct scope for Batch 2.1: we are policing the
*label* a source emits, not the source's runtime behaviour. Running the source
would pull in aiohttp which has a pre-existing Python-3.13 × Windows IOCP hang
(see docs/pillar2_progress.md environment note). A static check sidesteps that
entirely and gives a stable regression signal.

Coverage matrix — plan §4 Batch 2.1:

  Signal-less sources (must emit "fabricated")
    - scrapers/linkedin.py
    - ats/workable.py
    - ats/personio.py
    - ats/pinpoint.py

  Wrong-field sources (must emit "low")
    - feeds/nhs_jobs.py             (first_seen_at stamped to now)
    - apis_keyed/jooble.py          (updated flag, not posting date)
    - ats/greenhouse.py             (updated_at, not posted_at)
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_BACKEND = Path(__file__).resolve().parent.parent
_LITERAL_RE = re.compile(r'''date_confidence\s*=\s*(?:"([^"]+)"|'([^']+)')''')


def _literal_confidences(source_path: Path) -> list[str]:
    """Return the list of literal `date_confidence="..."` values assigned in
    the source file. Non-literal assignments (e.g. `date_confidence=confidence`
    where `confidence` is a variable) are ignored — those sources derive the
    label dynamically and are out of scope for this batch."""
    text = source_path.read_text(encoding="utf-8")
    out: list[str] = []
    for match in _LITERAL_RE.finditer(text):
        value = match.group(1) or match.group(2)
        if value is not None:
            out.append(value)
    return out


# ---------------------------------------------------------------------------
# Signal-less sources — must emit "fabricated" (Batch 2.1 fix)
# ---------------------------------------------------------------------------

# 2026-08-09 — workable and personio were REMOVED from this list, and
# nhs_jobs + greenhouse from _WRONG_FIELD below. They are no longer signal-less:
# a live probe of each upstream found a real date field that our extractor was
# ignoring, so all four now derive their label dynamically via
# normalize_posted_at() instead of hardcoding one.
#
#   workable    -> `published_on`    (100% fill on the widget endpoint,
#                                     re-measured 2026-08-24. It was recorded
#                                     here as `published`, which is the OLD
#                                     POST /api/v2 name and is 0% present on
#                                     the widget response the adapter now uses.)
#   personio    -> `createdAt`       (ISO-8601)
#   nhs_jobs    -> `postDate`        (100% fill; the feed comment claiming no
#                                     posted date was simply out of date)
#   greenhouse  -> `first_published` (100% fill across 928 live jobs; the code
#                                     had been reading `updated_at`, a mutation
#                                     timestamp, and labelling it "low")
#
# linkedin and pinpoint stay: their upstreams genuinely expose no date field
# (pinpoint's 24-key schema was checked in full), so "fabricated" remains the
# honest label — it makes _recency_points() return 0 rather than letting
# date_found earn 60% freshness credit for a date nobody published.
_SIGNAL_LESS = [
    ("linkedin", "src/sources/scrapers/linkedin.py"),
    ("pinpoint", "src/sources/ats/pinpoint.py"),
]


@pytest.mark.parametrize(("source_name", "relpath"), _SIGNAL_LESS)
def test_signal_less_source_emits_fabricated(source_name: str, relpath: str) -> None:
    path = _REPO_BACKEND / relpath
    assert path.exists(), f"{relpath} is missing"
    literals = _literal_confidences(path)
    assert literals, (
        f"{source_name}: no literal date_confidence= assignments found — either "
        f"the file was refactored to derive the label dynamically (update this "
        f"test) or the instrumentation was accidentally removed."
    )
    non_fabricated = [v for v in literals if v != "fabricated"]
    assert not non_fabricated, (
        f"{source_name} emits {non_fabricated!r}; Batch 2.1 requires every "
        f"literal to be 'fabricated' because the upstream has no date field."
    )


# ---------------------------------------------------------------------------
# Wrong-field sources — must emit "low" (already correct at 2026-04-20 audit)
# ---------------------------------------------------------------------------

# nhs_jobs and greenhouse moved off this list on 2026-08-09 — see the note on
# _SIGNAL_LESS above. Both now read a real upstream date field and derive the
# label, so there is no literal left to assert.
#
# jooble stays, and deliberately so: its only date-shaped field is `updated`,
# which is a MUTATION timestamp, not a posting date. The source refuses to
# treat it as one and hardcodes "low" — honest-but-empty. That is the correct
# behaviour and this test guards it.
_WRONG_FIELD = [
    ("jooble", "src/sources/apis_keyed/jooble.py"),
]


@pytest.mark.parametrize(("source_name", "relpath"), _WRONG_FIELD)
def test_wrong_field_source_still_emits_low(source_name: str, relpath: str) -> None:
    path = _REPO_BACKEND / relpath
    assert path.exists(), f"{relpath} is missing"
    literals = _literal_confidences(path)
    assert literals, (
        f"{source_name}: no literal date_confidence= assignments found — this "
        f"source is supposed to emit 'low' (wrong-field heuristic). If it was "
        f"refactored to dynamic labels, update this test's coverage."
    )
    offending = [v for v in literals if v not in {"low"}]
    assert not offending, (
        f"{source_name} emits {offending!r}; wrong-field sources must stay on "
        f"'low' (plan §4 Batch 2.1 — Out of scope)."
    )


# ---------------------------------------------------------------------------
# Gate-pass integration — the JobScorer's recency_score_for_job must zero-out
# 'fabricated' so the label change in the 4 signal-less sources actually
# translates to a scoring penalty downstream.
# ---------------------------------------------------------------------------


def test_recency_score_fabricated_returns_zero_via_helper() -> None:
    """Wiring check — the 'fabricated' branch of recency_score_for_job is the
    mechanism that turns the Batch 2.1 label change into a visible score
    reduction for linkedin/workable/personio/pinpoint jobs."""
    from datetime import datetime, timezone

    from src.models import Job
    from src.services.skill_matcher import recency_score_for_job

    today = datetime.now(timezone.utc).isoformat()
    fabricated_job = Job(
        title="Dummy",
        company="Dummy",
        apply_url="https://example.com",
        source="linkedin",
        date_found=today,
        posted_at=today,
        date_confidence="fabricated",
    )
    assert recency_score_for_job(fabricated_job) == 0
