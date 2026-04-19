"""Batch 1.4 (Pillar 1) — provenance-tracked skill entries + merge tests.

Exercises SkillEntry construction + the SOURCE_CONFIDENCE table +
merge_skill_entries conflict resolution + build_skill_entries_from_profile.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.services.profile.models import CVData, UserPreferences, UserProfile
from src.services.profile.skill_entry import (
    SOURCE_CONFIDENCE,
    SkillEntry,
    build_skill_entries_from_profile,
    merge_skill_entries,
)


# ── SOURCE_CONFIDENCE + SkillEntry.from_source ──────────────────────


def test_source_confidence_table_has_expected_sources():
    expected = {"user_declared", "cv_explicit", "linkedin",
                "github_dep", "github_lang", "github_topic"}
    assert set(SOURCE_CONFIDENCE.keys()) == expected


def test_source_confidence_ordering_matches_intent():
    """user_declared > linkedin > cv_explicit > github_dep > github_lang > github_topic."""
    c = SOURCE_CONFIDENCE
    assert c["user_declared"] > c["linkedin"]
    assert c["linkedin"] > c["cv_explicit"]
    assert c["cv_explicit"] > c["github_dep"]
    assert c["github_dep"] > c["github_lang"]
    assert c["github_lang"] > c["github_topic"]


def test_from_source_pulls_confidence_from_table():
    e = SkillEntry.from_source("Python", "cv_explicit")
    assert e.name == "Python"
    assert e.source == "cv_explicit"
    assert e.confidence == SOURCE_CONFIDENCE["cv_explicit"]
    assert e.esco_uri is None
    assert e.last_seen is None


def test_from_source_unknown_source_defaults_to_half():
    e = SkillEntry.from_source("X", "brand_new_future_source")
    assert e.confidence == 0.5


def test_from_source_accepts_optional_last_seen_and_esco_uri():
    e = SkillEntry.from_source(
        "Python", "linkedin",
        esco_uri="http://data.europa.eu/esco/skill/abc",
        last_seen="2025-01-15T00:00:00Z",
    )
    assert e.esco_uri.endswith("abc")
    assert e.last_seen.startswith("2025")


# ── merge_skill_entries — conflict resolution ───────────────────────


def test_merge_single_entry_passes_through():
    entries = [SkillEntry.from_source("Python", "cv_explicit")]
    merged = merge_skill_entries(entries)
    assert merged == entries


def test_merge_highest_confidence_wins():
    entries = [
        SkillEntry.from_source("Python", "github_lang"),   # 0.5
        SkillEntry.from_source("Python", "user_declared"), # 1.0
        SkillEntry.from_source("Python", "cv_explicit"),   # 0.85
    ]
    merged = merge_skill_entries(entries)
    assert len(merged) == 1
    assert merged[0].source == "user_declared"
    assert merged[0].confidence == 1.0


def test_merge_ties_broken_by_recency():
    """Same confidence, different last_seen — later wins."""
    older_iso = "2024-01-01T00:00:00Z"
    newer_iso = "2025-06-01T00:00:00Z"
    entries = [
        SkillEntry(name="Docker", source="linkedin", confidence=0.9, last_seen=older_iso),
        SkillEntry(name="Docker", source="linkedin", confidence=0.9, last_seen=newer_iso),
    ]
    merged = merge_skill_entries(entries)
    assert len(merged) == 1
    assert merged[0].last_seen == newer_iso


def test_merge_dedup_by_esco_uri_takes_precedence_over_name():
    """Same ESCO URI → treated as same skill even with different surface names."""
    uri = "http://data.europa.eu/esco/skill/1234"
    entries = [
        SkillEntry(name="Python programming", source="linkedin", confidence=0.9, esco_uri=uri),
        SkillEntry(name="Python", source="cv_explicit", confidence=0.85, esco_uri=uri),
    ]
    merged = merge_skill_entries(entries)
    assert len(merged) == 1
    # linkedin (0.9) wins
    assert merged[0].name == "Python programming"
    assert merged[0].esco_uri == uri


def test_merge_different_skills_all_preserved():
    entries = [
        SkillEntry.from_source("Python", "cv_explicit"),
        SkillEntry.from_source("Docker", "cv_explicit"),
        SkillEntry.from_source("Rust", "github_lang"),
    ]
    merged = merge_skill_entries(entries)
    names = [e.name for e in merged]
    assert names == ["Python", "Docker", "Rust"]


def test_merge_preserves_first_sighting_insertion_order():
    """Even after merging, the output reflects the order each distinct skill first appeared."""
    entries = [
        SkillEntry.from_source("Python", "github_lang"),  # first seen: Python
        SkillEntry.from_source("Docker", "github_lang"),  # first seen: Docker
        SkillEntry.from_source("Python", "user_declared"),  # wins for Python
        SkillEntry.from_source("Rust", "github_lang"),   # first seen: Rust
    ]
    merged = merge_skill_entries(entries)
    assert [e.name for e in merged] == ["Python", "Docker", "Rust"]
    # Python winner must be user_declared
    assert merged[0].source == "user_declared"


def test_merge_skips_empty_name():
    entries = [
        SkillEntry.from_source("", "cv_explicit"),
        SkillEntry.from_source("Docker", "cv_explicit"),
    ]
    merged = merge_skill_entries(entries)
    assert len(merged) == 1
    assert merged[0].name == "Docker"


def test_merge_handles_missing_last_seen_without_crash():
    entries = [
        SkillEntry(name="Docker", source="linkedin", confidence=0.9, last_seen=None),
        SkillEntry(name="Docker", source="linkedin", confidence=0.9, last_seen="2025-01-01T00:00:00Z"),
    ]
    merged = merge_skill_entries(entries)
    # Timestamped entry wins the recency tiebreak over missing last_seen
    assert merged[0].last_seen == "2025-01-01T00:00:00Z"


# ── build_skill_entries_from_profile ────────────────────────────────


def test_build_entries_walks_all_five_source_fields():
    prefs = UserPreferences(additional_skills=["Product Strategy"])
    cv = CVData(
        skills=["Python"],
        linkedin_skills=["Docker"],
        github_frameworks=["FastAPI"],
        github_skills_inferred=["Rust"],
    )
    profile = UserProfile(cv_data=cv, preferences=prefs)
    entries = build_skill_entries_from_profile(profile)
    sources = {e.source for e in entries}
    assert sources == {"user_declared", "cv_explicit", "linkedin", "github_dep", "github_lang"}


def test_build_entries_emits_one_row_per_source_for_same_skill():
    """Same skill name across 2 sources → 2 entries (merge happens AFTER this)."""
    prefs = UserPreferences(additional_skills=["Python"])
    cv = CVData(skills=["Python"])  # cv_explicit too
    profile = UserProfile(cv_data=cv, preferences=prefs)
    entries = build_skill_entries_from_profile(profile)
    python_entries = [e for e in entries if e.name == "Python"]
    assert len(python_entries) == 2
    assert {e.source for e in python_entries} == {"user_declared", "cv_explicit"}


def test_build_entries_stamps_last_seen_when_provided():
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    prefs = UserPreferences(additional_skills=["Python"])
    profile = UserProfile(cv_data=CVData(), preferences=prefs)
    entries = build_skill_entries_from_profile(profile, last_seen=now_iso)
    assert all(e.last_seen == now_iso for e in entries)


def test_build_entries_drops_empty_strings():
    prefs = UserPreferences(additional_skills=["", "   "])
    cv = CVData(skills=["Real"])
    profile = UserProfile(cv_data=cv, preferences=prefs)
    entries = build_skill_entries_from_profile(profile)
    assert len(entries) == 1
    assert entries[0].name == "Real"


def test_build_then_merge_end_to_end_produces_audit_trail():
    """Pipeline smoke: build → merge. A user-declared Python should beat a cv-explicit Python."""
    prefs = UserPreferences(additional_skills=["Python"])
    cv = CVData(skills=["Python", "Docker"], linkedin_skills=["Python"])
    profile = UserProfile(cv_data=cv, preferences=prefs)

    raw = build_skill_entries_from_profile(profile)
    assert len(raw) == 4  # 1 declared + 2 cv + 1 linkedin
    merged = merge_skill_entries(raw)
    names = {e.name: e for e in merged}
    assert set(names.keys()) == {"Python", "Docker"}
    # Python winner must be user_declared (highest conf 1.0)
    assert names["Python"].source == "user_declared"
    # Docker only source is cv_explicit (0.85)
    assert names["Docker"].source == "cv_explicit"
