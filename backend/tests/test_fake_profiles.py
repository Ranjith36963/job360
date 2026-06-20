"""Tests for the fake-profile builder used by the multi-profile engine eval."""
from __future__ import annotations

from scripts.fake_profiles import PROFILES, build_user_profile


def test_ten_distinct_profiles():
    assert len(PROFILES) == 10
    ids = [p["id"] for p in PROFILES]
    assert len(set(ids)) == 10                       # all distinct
    roles = {p["role"] for p in PROFILES}
    assert len(roles) == 10                          # 10 distinct roles


def test_build_user_profile_fills_every_corner():
    spec = next(p for p in PROFILES if p["id"] == "data_engineer")
    prof = build_user_profile(spec)
    assert prof.is_complete                           # has cv raw_text + target titles
    assert "Data Engineer" in prof.cv_data.job_titles
    assert "Apache Spark" in prof.cv_data.skills
    assert prof.cv_data.summary                       # CV summary present
    assert prof.cv_data.raw_text                      # full CV text present
    assert prof.cv_data.github_languages              # GitHub signal present
    assert prof.cv_data.linkedin_positions            # LinkedIn work history present
    assert prof.preferences.experience_level == "mid"
    assert prof.preferences.salary_min == 55000
    assert prof.preferences.github_username == "alexr-data"


def test_all_profiles_build_complete():
    for spec in PROFILES:
        prof = build_user_profile(spec)
        assert prof.is_complete, spec["id"]
        assert prof.cv_data.job_titles and prof.cv_data.skills, spec["id"]
        assert prof.preferences.target_job_titles, spec["id"]
