"""Slice 5 (#483) review fix batch — each test pins one finding so it cannot
come back.

The sourcing-era deletion took `tests/test_design_rules.py` with it, and that
file was the only guard on owner rule #29 (an EMPTY user field stays SILENT —
never a penalty, never a guess, never a default we invent). The scorer that
rule was written against is gone; the rule is not. Today its reader is the
profile the agent fetches (`GET /api/profile` → `_build_profile_response`,
and `get_profile` in the MCP server), so that is where the guard now lives.
"""
from __future__ import annotations

import re
from pathlib import Path

from src.api.routes.profile import _build_profile_response
from src.services.profile import seniority
from src.services.profile.models import UserPreferences, UserProfile

_BACKEND = Path(__file__).resolve().parents[1]
_ROOT = _BACKEND.parent


# ── Rule #29: an unset preference serialises as absent, not as a number ────


def test_rule_29_unset_preferences_serialise_absent_not_default() -> None:
    """A brand-new profile has stated nothing. The response must say exactly
    that — None / "" / [] — not 0, not a fallback level, not a guessed range.
    A reader (the user's own agent) that sees `salary_min: 0` will treat it as
    a fact the user typed."""
    resp = _build_profile_response(UserProfile(), "user-1", [])
    prefs = resp.preferences

    assert prefs["salary_min"] is None
    assert prefs["salary_max"] is None
    assert prefs["experience_level"] == ""
    assert prefs["preferred_locations"] == []
    assert prefs["about_me"] == ""
    assert resp.summary.experience_level == ""


def test_rule_29_inferred_level_never_masquerades_as_typed() -> None:
    """Seniority read off the CV lives in `experience_level_inferred`; the
    typed field stays empty until the user types. The summary the agent
    reads reports the TYPED field, so an inference can never look like a
    preference the user stated (rule #29's one real trap for this module —
    see `services/profile/seniority.py`'s header)."""
    profile = UserProfile(preferences=UserPreferences(experience_level_inferred="senior"))
    resp = _build_profile_response(profile, "user-1", [])

    assert resp.summary.experience_level == ""
    assert resp.preferences["experience_level"] == ""
    assert resp.preferences["experience_level_inferred"] == "senior"


# ── Rule #28: the seniority noise list is data, not a hand-typed regex ────


def test_seniority_noise_lives_in_a_data_file_not_code() -> None:
    """Conventions review (slice 5): `_SENIORITY_NOISE` was a hand-typed regex
    of trap phrases next to a vocabulary that already ships as a committed
    file (rule #28). Same shape now: `src/data/job_signals/seniority_noise.txt`
    is the list, code only loads it."""
    data = _BACKEND / "src" / "data" / "job_signals" / "seniority_noise.txt"
    assert data.exists(), "seniority_noise.txt must ship with the job_signals package data"
    source = (_BACKEND / "src" / "services" / "profile" / "seniority.py").read_text(encoding="utf-8")
    assert "_SENIORITY_NOISE" not in source
    assert 'r"\\bjunior\\s+schools?\\b"' not in source

    # And it still does its job: a trap phrase feeds no tier, the real thing does.
    assert seniority.detect_seniority("Head of Year 9").value.value == "unknown"
    assert seniority.detect_seniority("Junior School Teacher").value.value == "unknown"
    assert seniority.detect_seniority("Lead Generation Executive").value.value == "unknown"
    assert seniority.detect_seniority("Head of Engineering").value.value != "unknown"


def test_seniority_noise_missing_file_degrades_to_strip_nothing(monkeypatch) -> None:
    """A missing data file must not crash a profile save (issue #260's shape):
    it is reported by `_load_terms` and the matcher carries on without
    stripping."""
    seniority._seniority_noise.cache_clear()
    monkeypatch.setattr(seniority, "_DATA", Path("/nonexistent/job_signals"))
    seniority._seniority_terms.cache_clear()
    try:
        assert seniority._seniority_noise() is None
    finally:
        seniority._seniority_noise.cache_clear()
        seniority._seniority_terms.cache_clear()


# ── Bugs review: nothing on the surviving surface links to a deleted page ──


def test_no_surviving_link_to_deleted_pages() -> None:
    """`/dashboard` and `/jobs/{id}` went with the sourcing era. Two spots
    kept pointing at them (the prod smoke walk and the notifications ledger)
    — a link to a 404 is a bug the user finds, and a monitor that walks a
    deleted page is permanently red."""
    smoke = (_ROOT / "frontend" / "tests" / "synthetic" / "live-smoke.mjs").read_text(encoding="utf-8")
    walked = set(re.findall(r'gotoAuthed\("(/[^"]*)"\)', smoke))
    assert "/dashboard" not in walked
    assert "/jobs" not in walked
    assert "/applications" in walked

    notifications = (_ROOT / "frontend" / "src" / "app" / "notifications" / "page.tsx").read_text(encoding="utf-8")
    assert "/jobs/${" not in notifications


def test_dump_db_no_longer_reads_run_log() -> None:
    """Migration 0039 drops `run_log`; the dev dump script queried it before
    anything else printed, so it died on line one against every migrated DB."""
    script = (_BACKEND / "scripts" / "dump_db.py").read_text(encoding="utf-8")
    assert "FROM run_log" not in script
    assert "table_info(run_log)" not in script


def test_watchdog_workflow_has_a_schedule() -> None:
    """The surviving half of absence.yml watches whether the harness's own
    workflows stopped running. Main had paused its schedule with the
    sourcing check; a watchdog with no schedule is the silence it exists to
    catch."""
    wf = (_ROOT / ".github" / "workflows" / "absence.yml").read_text(encoding="utf-8")
    assert re.search(r"^\s+schedule:\s*$", wf, re.MULTILINE), "absence.yml needs an `on.schedule` cron"
    assert "workflow_dispatch" in wf
