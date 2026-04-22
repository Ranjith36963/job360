"""Pillar 2 Batch 2.4 — tests for the domain classifier + source routing."""
from __future__ import annotations

import pytest

from src.services.profile.models import CVData, UserPreferences, UserProfile
from src.services.domain_classifier import (
    classify_user_domain,
    source_matches_user_domains,
)


# ---------------------------------------------------------------------------
# classify_user_domain — profile → set[str]
# ---------------------------------------------------------------------------


def _profile(titles=None, skills=None, industry="", linkedin_skills=None):
    return UserProfile(
        cv_data=CVData(
            raw_text="",
            job_titles=list(titles or []),
            skills=list(skills or []),
            linkedin_skills=list(linkedin_skills or []),
            linkedin_industry=industry,
        ),
        preferences=UserPreferences(
            target_job_titles=[],
            additional_skills=[],
        ),
    )


def test_zero_profile_returns_empty_set():
    """None profile → empty set → graceful fallback in _build_sources."""
    assert classify_user_domain(None) == set()


def test_empty_profile_returns_empty_set():
    """A profile with no titles/skills/industry has no domain signals."""
    assert classify_user_domain(_profile()) == set()


def test_tech_titles_classify_as_tech():
    p = _profile(titles=["Senior Software Engineer"])
    assert "tech" in classify_user_domain(p)


def test_tech_skills_classify_as_tech():
    p = _profile(skills=["Python", "Kubernetes", "Docker"])
    assert "tech" in classify_user_domain(p)


def test_healthcare_nurse_classifies_as_healthcare():
    p = _profile(titles=["Senior Staff Nurse"])
    assert "healthcare" in classify_user_domain(p)


def test_healthcare_gp_classifies_as_healthcare():
    p = _profile(titles=["General Practitioner"])
    assert "healthcare" in classify_user_domain(p)


def test_healthcare_pharmaceutical_classifies():
    p = _profile(titles=["Pharmaceutical Scientist"])
    assert "healthcare" in classify_user_domain(p)


def test_academia_postdoctoral_classifies_as_academia():
    p = _profile(titles=["Postdoctoral Research Fellow"])
    assert "academia" in classify_user_domain(p)


def test_academia_lecturer_classifies():
    p = _profile(titles=["Senior Lecturer in Chemistry"])
    assert "academia" in classify_user_domain(p)


def test_education_teacher_classifies_as_education():
    p = _profile(titles=["Primary School Teacher"])
    assert "education" in classify_user_domain(p)


def test_education_apprentice_classifies():
    p = _profile(titles=["Apprentice Electrician"])
    assert "education" in classify_user_domain(p)


def test_climate_sustainability_classifies_as_climate():
    p = _profile(titles=["Sustainability Consultant"])
    assert "climate" in classify_user_domain(p)


def test_climate_renewable_classifies():
    p = _profile(skills=["solar", "wind energy"])
    assert "climate" in classify_user_domain(p)


def test_multi_domain_user_returns_multiple_domains():
    """A user moving into climate tech from software engineering may span both."""
    p = _profile(
        titles=["Software Engineer"],
        skills=["Python"],
        industry="Renewable Energy",
    )
    domains = classify_user_domain(p)
    assert "tech" in domains
    assert "climate" in domains


def test_generic_manager_has_no_domain_signal():
    """A generic 'Project Manager' without tech/healthcare/climate keywords
    should classify as empty → _build_sources falls back to all sources."""
    p = _profile(titles=["Project Manager"], skills=["stakeholder management"])
    # No specific domain signals → general only (returned as empty set).
    assert classify_user_domain(p) == set()


def test_general_never_emitted_as_a_domain():
    """'general' is reserved as a source-side default, not a user classification."""
    p = _profile(titles=["Software Engineer", "Nurse"])
    domains = classify_user_domain(p)
    assert "general" not in domains


def test_linkedin_positions_contribute_to_classification():
    p = UserProfile(
        cv_data=CVData(
            raw_text="",
            linkedin_positions=[{"title": "Registered Nurse", "company": "NHS"}],
        ),
        preferences=UserPreferences(),
    )
    assert "healthcare" in classify_user_domain(p)


def test_word_boundary_avoids_false_match_on_short_keyword():
    """The 'ai' keyword for tech must not match 'maintain' or 'captain'."""
    p = _profile(titles=["Ship Captain"])   # contains 'ai' but not as a word
    assert "tech" not in classify_user_domain(p)


# ---------------------------------------------------------------------------
# source_matches_user_domains — gate logic
# ---------------------------------------------------------------------------


def test_empty_user_domains_matches_every_source():
    """Zero-profile users get every source (graceful fallback)."""
    assert source_matches_user_domains({"tech"}, set()) is True
    assert source_matches_user_domains({"healthcare"}, set()) is True
    assert source_matches_user_domains({"general"}, set()) is True


def test_general_source_always_matches():
    """Cross-domain boards (Reed, Indeed, LinkedIn) tagged 'general' always run."""
    assert source_matches_user_domains({"general"}, {"healthcare"}) is True
    assert source_matches_user_domains({"general"}, {"tech"}) is True


def test_tech_source_skipped_for_healthcare_user():
    assert source_matches_user_domains({"tech"}, {"healthcare"}) is False


def test_healthcare_source_included_for_healthcare_user():
    assert source_matches_user_domains({"healthcare"}, {"healthcare"}) is True


def test_multi_tag_source_with_any_overlap_matches():
    """A source tagged {"education", "general"} matches everyone (general)."""
    assert source_matches_user_domains({"education", "general"}, {"tech"}) is True


def test_multi_tag_source_without_general_needs_overlap():
    """A source tagged {"tech", "climate"} matches tech OR climate users only."""
    assert source_matches_user_domains({"tech", "climate"}, {"tech"}) is True
    assert source_matches_user_domains({"tech", "climate"}, {"climate"}) is True
    assert source_matches_user_domains({"tech", "climate"}, {"healthcare"}) is False


# ---------------------------------------------------------------------------
# Source DOMAINS class attribute invariants
# ---------------------------------------------------------------------------


def test_base_source_has_general_default():
    from src.sources.base import BaseJobSource
    assert BaseJobSource.DOMAINS == {"general"}


@pytest.mark.parametrize(("import_path", "class_name", "expected"), [
    ("src.sources.feeds.nhs_jobs", "NHSJobsSource", {"healthcare"}),
    ("src.sources.feeds.nhs_jobs_xml", "NHSJobsXMLSource", {"healthcare"}),
    ("src.sources.feeds.biospace", "BioSpaceSource", {"healthcare"}),
    ("src.sources.feeds.jobs_ac_uk", "JobsAcUkSource", {"academia"}),
    ("src.sources.feeds.uni_jobs", "UniJobsSource", {"academia"}),
    ("src.sources.apis_free.teaching_vacancies", "TeachingVacanciesSource", {"education"}),
    ("src.sources.scrapers.climatebase", "ClimatebaseSource", {"climate"}),
    ("src.sources.scrapers.bcs_jobs", "BCSJobsSource", {"tech"}),
    ("src.sources.scrapers.jobtensor", "JobTensorSource", {"tech"}),
    ("src.sources.apis_free.devitjobs", "DevITJobsSource", {"tech"}),
    ("src.sources.apis_free.landingjobs", "LandingJobsSource", {"tech"}),
    ("src.sources.apis_free.aijobs", "AIJobsSource", {"tech"}),
    ("src.sources.apis_free.hn_jobs", "HNJobsSource", {"tech"}),
    ("src.sources.other.hackernews", "HackerNewsSource", {"tech"}),
    ("src.sources.other.nofluffjobs", "NoFluffJobsSource", {"tech"}),
    ("src.sources.scrapers.aijobs_global", "AIJobsGlobalSource", {"tech"}),
    ("src.sources.scrapers.aijobs_ai", "AIJobsAISource", {"tech"}),
])
def test_domain_tagged_sources_have_correct_domains(import_path, class_name, expected):
    """The 17 single-domain sources in this parametrize list must advertise
    the right set. (The 18th overridden source, gov_apprenticeships with
    multi-tag {"education", "general"}, has its own dedicated test below —
    total count of `DOMAINS`-overridden sources is 18.)"""
    import importlib
    mod = importlib.import_module(import_path)
    cls = getattr(mod, class_name)
    assert cls.DOMAINS == expected


def test_apprenticeships_source_spans_education_and_general():
    """gov_apprenticeships deliberately spans {"education", "general"} — it's
    the one source where the apprenticeship nature wants education tagging
    BUT the content spans every trade (healthcare, engineering, finance)."""
    from src.sources.apis_free.gov_apprenticeships import GovApprenticeshipsSource
    assert GovApprenticeshipsSource.DOMAINS == {"education", "general"}


# ---------------------------------------------------------------------------
# End-to-end — _build_sources filters by classified domain
# ---------------------------------------------------------------------------


def _names(sources) -> set[str]:
    return {s.name for s in sources}


@pytest.fixture
def mock_session():
    """Mock aiohttp.ClientSession — _build_sources only stores the reference,
    it never calls anything on the session at construction time."""
    from unittest.mock import MagicMock
    return MagicMock(name="ClientSession")


def test_build_sources_with_healthcare_profile_skips_tech_only(mock_session):
    """A healthcare user should NOT get bcs_jobs / climatebase / aijobs_*."""
    from src.main import _build_sources
    profile = _profile(titles=["Registered Nurse"])
    sources = _build_sources(mock_session, user_profile=profile)
    names = _names(sources)
    assert "nhs_jobs" in names
    assert "nhs_jobs_xml" in names
    assert "reed" in names           # general always included
    assert "bcs_jobs" not in names   # tech-only, skipped
    assert "climatebase" not in names
    assert "aijobs" not in names


def test_build_sources_with_tech_profile_skips_healthcare_only(mock_session):
    from src.main import _build_sources
    profile = _profile(titles=["Senior Software Engineer"])
    sources = _build_sources(mock_session, user_profile=profile)
    names = _names(sources)
    assert "bcs_jobs" in names
    assert "reed" in names
    assert "nhs_jobs" not in names
    assert "nhs_jobs_xml" not in names
    assert "biospace" not in names


def test_build_sources_with_zero_profile_includes_everything(mock_session):
    """Graceful fallback — no profile means include every source."""
    from src.main import _build_sources, SOURCE_INSTANCE_COUNT
    sources = _build_sources(mock_session, user_profile=None)
    assert len(sources) == SOURCE_INSTANCE_COUNT


def test_build_sources_source_filter_still_works_post_batch_2_4(mock_session):
    """The --source CLI flag continues to short-circuit domain filtering."""
    from src.main import _build_sources
    # Even a healthcare user can force-fetch bcs_jobs via source_filter.
    profile = _profile(titles=["Registered Nurse"])
    sources = _build_sources(mock_session, source_filter="bcs_jobs", user_profile=profile)
    assert len(sources) == 1
    assert sources[0].name == "bcs_jobs"
