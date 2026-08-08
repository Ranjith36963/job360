"""Snapshot id — a human-readable identity for one profile "intake"
(migration 0030, owner request 2026-08-08).

``make_snapshot_id`` is a pure function (no DB, no clock surprises via the
``when`` kwarg), so these tests exercise it directly rather than through
storage — no DB fixture needed, fully offline (root rule #4).

The load-bearing property under test: the content segment hashes the RAW
INPUTS the user supplied, never the extracted output. See
``src/services/profile/snapshot.py``'s module docstring for the full
rationale.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from src.services.profile.models import CVData, UserPreferences
from src.services.profile.snapshot import make_snapshot_id

SNAPSHOT_ID_RE = re.compile(r"^SNAP-(\d{8})-([0-9a-f]{4})-([0-9a-f]{8})$")

WHEN = datetime(2026, 8, 8, 12, 0, 0, tzinfo=timezone.utc)


def _cv(**overrides: object) -> CVData:
    defaults: dict[str, object] = {
        "raw_text": "Senior Python Engineer, 8 years experience.",
        "linkedin_raw_text": "Ada Lovelace — Software Engineer at Analytical Engines.",
    }
    defaults.update(overrides)
    return CVData(**defaults)  # type: ignore[arg-type]


def _prefs(**overrides: object) -> UserPreferences:
    defaults: dict[str, object] = {
        "target_job_titles": ["Software Engineer"],
        "additional_skills": ["Python", "FastAPI"],
        "preferred_locations": ["London"],
        "github_username": "ada",
        "about_me": "I like building things.",
    }
    defaults.update(overrides)
    return UserPreferences(**defaults)  # type: ignore[arg-type]


# ── format ────────────────────────────────────────────────────────────────


def test_format_matches_documented_shape():
    snap_id = make_snapshot_id("user-1", _cv(), _prefs(), when=WHEN)
    match = SNAPSHOT_ID_RE.match(snap_id)
    assert match is not None, f"{snap_id!r} does not match SNAP-YYYYMMDD-<user4>-<content8>"
    date_segment, user_segment, content_segment = match.groups()
    assert date_segment == "20260808"
    assert len(user_segment) == 4
    assert len(content_segment) == 8


def test_raw_user_id_never_appears_in_the_id():
    user_id = "super-secret-account-key-12345"
    snap_id = make_snapshot_id(user_id, _cv(), _prefs(), when=WHEN)
    assert user_id not in snap_id


# ── the load-bearing property: content, not output ──────────────────────────


def test_same_inputs_same_day_identical_id():
    id1 = make_snapshot_id("user-1", _cv(), _prefs(), when=WHEN)
    id2 = make_snapshot_id("user-1", _cv(), _prefs(), when=WHEN)
    assert id1 == id2


def test_reextraction_same_input_different_output_same_id():
    """Two CVData snapshots with IDENTICAL raw_text/linkedin_raw_text but
    DIFFERENT extracted fields (skills, job_titles, career_domain, ...) —
    exactly what a re-parse / model swap / bug-fixed extractor produces —
    must yield the SAME snapshot id. The user submitted nothing new; only
    what we understood from it changed.
    """
    raw_text = "Senior Python Engineer, 8 years experience."
    linkedin_raw_text = "Ada Lovelace — Software Engineer at Analytical Engines."

    cv_before_extraction = CVData(
        raw_text=raw_text,
        linkedin_raw_text=linkedin_raw_text,
        skills=[],
        job_titles=[],
        career_domain=None,
    )
    cv_after_reextraction = CVData(
        raw_text=raw_text,
        linkedin_raw_text=linkedin_raw_text,
        skills=["Python", "FastAPI", "PostgreSQL"],
        job_titles=["Senior Python Engineer", "Backend Engineer"],
        career_domain="software_engineering",
        github_llm_skills=["LangChain"],
        extraction_score={"overall": 0.9},
    )
    prefs = _prefs()

    id_before = make_snapshot_id("user-1", cv_before_extraction, prefs, when=WHEN)
    id_after = make_snapshot_id("user-1", cv_after_reextraction, prefs, when=WHEN)
    assert id_before == id_after


def test_changed_cv_text_changes_content_segment():
    id1 = make_snapshot_id("user-1", _cv(), _prefs(), when=WHEN)
    id2 = make_snapshot_id(
        "user-1", _cv(raw_text="Completely different CV text."), _prefs(), when=WHEN
    )
    assert id1 != id2
    # Only the content segment should move — date + user stay put.
    m1, m2 = SNAPSHOT_ID_RE.match(id1), SNAPSHOT_ID_RE.match(id2)
    assert m1 is not None and m2 is not None
    assert m1.group(1) == m2.group(1)  # same date
    assert m1.group(2) == m2.group(2)  # same user
    assert m1.group(3) != m2.group(3)  # different content


def test_changed_preferences_changes_content_segment():
    id1 = make_snapshot_id("user-1", _cv(), _prefs(), when=WHEN)
    id2 = make_snapshot_id(
        "user-1", _cv(), _prefs(target_job_titles=["Data Scientist"]), when=WHEN
    )
    assert id1 != id2


# ── user segment ─────────────────────────────────────────────────────────


def test_different_user_different_user_segment_same_content_segment():
    id1 = make_snapshot_id("user-1", _cv(), _prefs(), when=WHEN)
    id2 = make_snapshot_id("user-2", _cv(), _prefs(), when=WHEN)
    m1, m2 = SNAPSHOT_ID_RE.match(id1), SNAPSHOT_ID_RE.match(id2)
    assert m1 is not None and m2 is not None
    assert m1.group(2) != m2.group(2)  # different user segment
    assert m1.group(3) == m2.group(3)  # same content segment — same inputs


def test_same_user_stable_user_segment_across_calls():
    id1 = make_snapshot_id("user-1", _cv(), _prefs(), when=WHEN)
    id2 = make_snapshot_id(
        "user-1", _cv(raw_text="different"), _prefs(about_me="different"), when=WHEN
    )
    m1, m2 = SNAPSHOT_ID_RE.match(id1), SNAPSHOT_ID_RE.match(id2)
    assert m1 is not None and m2 is not None
    assert m1.group(2) == m2.group(2)


# ── date segment ──────────────────────────────────────────────────────────


def test_same_inputs_different_day_different_date_segment():
    day1 = datetime(2026, 8, 8, 23, 59, 0, tzinfo=timezone.utc)
    day2 = datetime(2026, 8, 9, 0, 0, 1, tzinfo=timezone.utc)
    id1 = make_snapshot_id("user-1", _cv(), _prefs(), when=day1)
    id2 = make_snapshot_id("user-1", _cv(), _prefs(), when=day2)
    assert id1 != id2
    m1, m2 = SNAPSHOT_ID_RE.match(id1), SNAPSHOT_ID_RE.match(id2)
    assert m1 is not None and m2 is not None
    assert m1.group(1) != m2.group(1)
    assert m1.group(3) == m2.group(3)  # content unchanged — only the date moved


def test_naive_when_is_used_as_is_and_tzaware_normalised_to_utc():
    """A tz-aware ``when`` in a non-UTC zone must normalise to the UTC date
    before formatting, so the id agrees with a UTC-stamped DB row."""
    # 23:30 in UTC+2 is 21:30 UTC — same day either way here, so use a value
    # that actually crosses midnight in UTC to prove normalisation happens.
    plus_two = timezone(timedelta(hours=2))
    late_local = datetime(2026, 8, 9, 1, 0, 0, tzinfo=plus_two)  # 2026-08-08 23:00 UTC
    snap_id = make_snapshot_id("user-1", _cv(), _prefs(), when=late_local)
    match = SNAPSHOT_ID_RE.match(snap_id)
    assert match is not None
    assert match.group(1) == "20260808"


# ── empty / missing inputs ───────────────────────────────────────────────


def test_empty_inputs_still_produce_a_valid_stable_id():
    empty_cv = CVData()
    empty_prefs = UserPreferences()
    id1 = make_snapshot_id("user-1", empty_cv, empty_prefs, when=WHEN)
    id2 = make_snapshot_id("user-1", CVData(), UserPreferences(), when=WHEN)
    assert SNAPSHOT_ID_RE.match(id1) is not None
    assert id1 == id2


def test_default_when_uses_current_utc_date():
    """No explicit ``when`` still produces a well-formed id stamped with
    today's UTC date — proves the default-clock path works, not just the
    testable ``when=`` override."""
    snap_id = make_snapshot_id("user-1", _cv(), _prefs())
    match = SNAPSHOT_ID_RE.match(snap_id)
    assert match is not None
    assert match.group(1) == datetime.now(timezone.utc).strftime("%Y%m%d")
