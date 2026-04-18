"""Tests for the 99% pre-filter cascade."""
from datetime import datetime, timezone

from src.models import Job
from src.services.prefilter import (
    FilterProfile,
    experience_ok,
    location_ok,
    passes_prefilter,
    skill_overlap_ok,
)


def _job(title: str = "Senior Software Engineer", location: str = "London, UK",
         description: str = "Python Django AWS") -> Job:
    return Job(
        title=title,
        company="Acme",
        apply_url="https://x",
        source="test",
        date_found=datetime.now(timezone.utc),
        location=location,
        description=description,
    )


# --- location -----------------------------------------------------------

def test_location_empty_preferences_passes():
    p = FilterProfile()
    assert location_ok(p, _job(location="Edinburgh"))


def test_location_remote_job_accepted_by_remote_user():
    p = FilterProfile(preferred_locations=["London"], work_arrangement="remote")
    assert location_ok(p, _job(location="Remote - UK"))


def test_location_substring_match():
    p = FilterProfile(preferred_locations=["London"])
    assert location_ok(p, _job(location="London, Greater London"))


def test_location_mismatch_rejected():
    p = FilterProfile(preferred_locations=["London"])
    assert not location_ok(p, _job(location="Edinburgh, Scotland"))


# --- experience ---------------------------------------------------------

def test_experience_no_preference_passes():
    p = FilterProfile()
    assert experience_ok(p, _job(title="Senior Engineer"))


def test_junior_candidate_skips_principal_role():
    p = FilterProfile(experience_level="junior")
    assert not experience_ok(p, _job(title="Principal Software Engineer"))


def test_senior_candidate_accepts_senior_role():
    p = FilterProfile(experience_level="senior")
    assert experience_ok(p, _job(title="Senior Software Engineer"))


def test_mid_candidate_within_one_band_of_senior():
    p = FilterProfile(experience_level="mid")
    assert experience_ok(p, _job(title="Senior Python Engineer"))


def test_title_without_level_token_passes_through():
    p = FilterProfile(experience_level="junior")
    assert experience_ok(p, _job(title="Software Engineer"))


# --- skills -------------------------------------------------------------

def test_skill_overlap_none_declared_passes():
    p = FilterProfile()
    assert skill_overlap_ok(p, _job(description="Accountancy role"))


def test_skill_overlap_hit_in_description():
    p = FilterProfile(skills={"Python", "Django"})
    assert skill_overlap_ok(p, _job(description="We need Django experts"))


def test_skill_overlap_zero_match_rejected():
    p = FilterProfile(skills={"Python", "Kubernetes"})
    assert not skill_overlap_ok(p, _job(description="Accounting and Excel"))


# --- cascade ------------------------------------------------------------

def test_cascade_passes_valid_match():
    p = FilterProfile(
        preferred_locations=["London"],
        experience_level="senior",
        skills={"Python"},
    )
    j = _job(title="Senior Python Engineer", location="London, UK",
             description="Python and Django")
    assert passes_prefilter(p, j)


def test_cascade_blocks_on_location_only():
    p = FilterProfile(
        preferred_locations=["London"],
        experience_level="senior",
        skills={"Python"},
    )
    j = _job(title="Senior Python Engineer", location="Edinburgh",
             description="Python")
    assert not passes_prefilter(p, j)


def test_cascade_blocks_on_skill_mismatch():
    p = FilterProfile(
        preferred_locations=["London"],
        experience_level="senior",
        skills={"Python"},
    )
    j = _job(title="Senior Accountant", location="London",
             description="Excel, VBA, audit")
    assert not passes_prefilter(p, j)
