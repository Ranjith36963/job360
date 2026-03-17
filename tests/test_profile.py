"""Tests for the src/profile/ package — models, cv_parser, preferences, storage, keyword_generator."""

import json
import os
import tempfile
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from src.models import Job
from src.profile.models import CVData, UserPreferences, UserProfile, SearchConfig
from src.profile.preferences import validate_preferences, merge_cv_and_preferences
from src.profile.keyword_generator import generate_search_config
from src.profile.storage import save_profile, load_profile, profile_exists
from src.profile.cv_parser import (
    _extract_skills_from_text,
    _extract_titles_from_experience,
    _find_sections,
)
from src.filters.skill_matcher import JobScorer
from src.config.keywords import (
    JOB_TITLES,
    PRIMARY_SKILLS,
    SECONDARY_SKILLS,
    TERTIARY_SKILLS,
    RELEVANCE_KEYWORDS,
    NEGATIVE_TITLE_KEYWORDS,
    VISA_KEYWORDS,
)


# -----------------------------------------------------------------------
# SearchConfig.from_defaults() — must reproduce hard-coded AI/ML keywords
# -----------------------------------------------------------------------

class TestSearchConfigDefaults:
    def test_from_defaults_job_titles(self):
        cfg = SearchConfig.from_defaults()
        assert cfg.job_titles == list(JOB_TITLES)

    def test_from_defaults_primary_skills(self):
        cfg = SearchConfig.from_defaults()
        assert cfg.primary_skills == list(PRIMARY_SKILLS)

    def test_from_defaults_secondary_skills(self):
        cfg = SearchConfig.from_defaults()
        assert cfg.secondary_skills == list(SECONDARY_SKILLS)

    def test_from_defaults_tertiary_skills(self):
        cfg = SearchConfig.from_defaults()
        assert cfg.tertiary_skills == list(TERTIARY_SKILLS)

    def test_from_defaults_relevance_keywords(self):
        cfg = SearchConfig.from_defaults()
        assert cfg.relevance_keywords == list(RELEVANCE_KEYWORDS)

    def test_from_defaults_negative_keywords(self):
        cfg = SearchConfig.from_defaults()
        assert cfg.negative_title_keywords == list(NEGATIVE_TITLE_KEYWORDS)

    def test_from_defaults_visa_keywords(self):
        cfg = SearchConfig.from_defaults()
        assert cfg.visa_keywords == list(VISA_KEYWORDS)

    def test_from_defaults_has_core_domain_words(self):
        cfg = SearchConfig.from_defaults()
        assert "ai" in cfg.core_domain_words
        assert "ml" in cfg.core_domain_words
        assert "llm" in cfg.core_domain_words

    def test_from_defaults_has_supporting_role_words(self):
        cfg = SearchConfig.from_defaults()
        assert "engineer" in cfg.supporting_role_words
        assert "scientist" in cfg.supporting_role_words


# -----------------------------------------------------------------------
# UserProfile
# -----------------------------------------------------------------------

class TestUserProfile:
    def test_empty_profile_not_complete(self):
        profile = UserProfile()
        assert not profile.is_complete

    def test_profile_with_cv_text_is_complete(self):
        profile = UserProfile(cv_data=CVData(raw_text="Some CV text"))
        assert profile.is_complete

    def test_profile_with_titles_is_complete(self):
        prefs = UserPreferences(target_job_titles=["Software Engineer"])
        profile = UserProfile(preferences=prefs)
        assert profile.is_complete

    def test_profile_with_skills_is_complete(self):
        prefs = UserPreferences(additional_skills=["Python", "SQL"])
        profile = UserProfile(preferences=prefs)
        assert profile.is_complete

    def test_profile_with_empty_prefs_not_complete(self):
        profile = UserProfile(preferences=UserPreferences())
        assert not profile.is_complete


# -----------------------------------------------------------------------
# CV Parser helpers
# -----------------------------------------------------------------------

class TestCVParserHelpers:
    def test_extract_skills_comma_separated(self):
        text = "Python, SQL, JavaScript, React, Node.js"
        skills = _extract_skills_from_text(text)
        assert "Python" in skills
        assert "SQL" in skills
        assert "JavaScript" in skills

    def test_extract_skills_semicolon_separated(self):
        text = "Python; SQL; JavaScript"
        skills = _extract_skills_from_text(text)
        assert "Python" in skills
        assert "SQL" in skills

    def test_extract_skills_newline_separated(self):
        text = "Python\nSQL\nJavaScript"
        skills = _extract_skills_from_text(text)
        assert "Python" in skills

    def test_extract_skills_filters_short(self):
        """Single-char items should be filtered out."""
        text = "a, Python, b, SQL"
        skills = _extract_skills_from_text(text)
        assert "a" not in skills
        assert "b" not in skills
        assert "Python" in skills

    def test_extract_titles_from_experience(self):
        text = "Software Engineer at Google\nProduct Manager at Meta\n"
        titles = _extract_titles_from_experience(text)
        assert "Software Engineer" in titles
        assert "Product Manager" in titles

    def test_extract_titles_dash_separator(self):
        text = "Data Scientist - Amazon"
        titles = _extract_titles_from_experience(text)
        assert "Data Scientist" in titles

    def test_find_sections_skills(self):
        text = "Summary\nI am a developer\nSkills\nPython, Java\nExperience\nWorked at Google"
        sections = _find_sections(text)
        assert "skills" in sections
        assert "Python" in sections["skills"]

    def test_find_sections_no_headers(self):
        text = "Just some random text without section headers"
        sections = _find_sections(text)
        assert "full_text" in sections


# -----------------------------------------------------------------------
# Preferences validation
# -----------------------------------------------------------------------

class TestPreferences:
    def test_validate_from_dict(self):
        data = {
            "target_job_titles": "Software Engineer, Data Scientist",
            "additional_skills": "Python, SQL, React",
            "preferred_locations": ["London", "Remote"],
            "work_arrangement": "hybrid",
            "salary_min": 50000,
            "salary_max": 80000,
        }
        prefs = validate_preferences(data)
        assert prefs.target_job_titles == ["Software Engineer", "Data Scientist"]
        assert prefs.additional_skills == ["Python", "SQL", "React"]
        assert prefs.preferred_locations == ["London", "Remote"]
        assert prefs.work_arrangement == "hybrid"
        assert prefs.salary_min == 50000

    def test_validate_empty_strings(self):
        prefs = validate_preferences({"target_job_titles": "", "additional_skills": ""})
        assert prefs.target_job_titles == []
        assert prefs.additional_skills == []

    def test_validate_list_input(self):
        prefs = validate_preferences({"target_job_titles": ["Engineer", "Scientist"]})
        assert prefs.target_job_titles == ["Engineer", "Scientist"]

    def test_merge_deduplicates(self):
        cv_skills = ["Python", "SQL", "Java"]
        cv_titles = ["Software Engineer"]
        prefs = UserPreferences(
            target_job_titles=["Software Engineer", "Data Analyst"],
            additional_skills=["Python", "React"],
        )
        merged = merge_cv_and_preferences(cv_skills, cv_titles, prefs)
        # Titles: prefs first, CV deduped
        assert merged.target_job_titles == ["Software Engineer", "Data Analyst"]
        # Skills: prefs first, CV minus excluded, deduped
        assert "Python" in merged.additional_skills
        assert "React" in merged.additional_skills
        assert "SQL" in merged.additional_skills
        assert "Java" in merged.additional_skills
        # No duplicates
        assert len([s for s in merged.additional_skills if s == "Python"]) == 1

    def test_merge_excludes_skills(self):
        cv_skills = ["Python", "SQL", "Java"]
        prefs = UserPreferences(
            additional_skills=["React"],
            excluded_skills=["Java"],
        )
        merged = merge_cv_and_preferences(cv_skills, [], prefs)
        assert "Java" not in merged.additional_skills
        assert "Python" in merged.additional_skills
        assert "React" in merged.additional_skills

    def test_merge_preserves_github_username(self):
        """BUG-1 regression: github_username must survive merge."""
        prefs = UserPreferences(
            additional_skills=["Python"],
            github_username="testuser",
        )
        merged = merge_cv_and_preferences(["SQL"], [], prefs)
        assert merged.github_username == "testuser"


# -----------------------------------------------------------------------
# Profile storage (uses temp directory)
# -----------------------------------------------------------------------

class TestProfileStorage:
    def test_save_and_load_roundtrip(self, tmp_path):
        profile = UserProfile(
            cv_data=CVData(raw_text="My CV", skills=["Python", "SQL"]),
            preferences=UserPreferences(
                target_job_titles=["Engineer"],
                salary_min=50000,
            ),
        )
        with patch("src.profile.storage.PROFILE_PATH", tmp_path / "profile.json"):
            save_profile(profile)
            loaded = load_profile()
            assert loaded is not None
            assert loaded.cv_data.raw_text == "My CV"
            assert loaded.cv_data.skills == ["Python", "SQL"]
            assert loaded.preferences.target_job_titles == ["Engineer"]
            assert loaded.preferences.salary_min == 50000

    def test_load_returns_none_when_missing(self, tmp_path):
        with patch("src.profile.storage.PROFILE_PATH", tmp_path / "nonexistent.json"):
            assert load_profile() is None

    def test_profile_exists_false(self, tmp_path):
        with patch("src.profile.storage.PROFILE_PATH", tmp_path / "nonexistent.json"):
            assert not profile_exists()

    def test_profile_exists_true(self, tmp_path):
        path = tmp_path / "profile.json"
        path.write_text("{}")
        with patch("src.profile.storage.PROFILE_PATH", path):
            assert profile_exists()

    def test_load_handles_corrupt_json(self, tmp_path):
        path = tmp_path / "profile.json"
        path.write_text("not valid json {{{{")
        with patch("src.profile.storage.PROFILE_PATH", path):
            assert load_profile() is None


# -----------------------------------------------------------------------
# Keyword generator
# -----------------------------------------------------------------------

class TestKeywordGenerator:
    def _make_profile(self, titles=None, skills=None, locations=None,
                      arrangement="", negatives=None):
        return UserProfile(
            cv_data=CVData(
                raw_text="test cv",
                skills=skills or [],
                job_titles=[],
            ),
            preferences=UserPreferences(
                target_job_titles=titles or [],
                additional_skills=[],
                preferred_locations=locations or [],
                work_arrangement=arrangement,
                negative_keywords=negatives or [],
            ),
        )

    def test_generates_relevance_keywords_from_titles(self):
        profile = self._make_profile(titles=["Software Engineer", "Data Analyst"])
        config = generate_search_config(profile)
        assert "software" in config.relevance_keywords
        assert "engineer" in config.relevance_keywords
        assert "data" in config.relevance_keywords
        assert "analyst" in config.relevance_keywords

    def test_generates_relevance_keywords_from_skills(self):
        profile = self._make_profile(skills=["Python", "SQL"])
        config = generate_search_config(profile)
        assert "python" in config.relevance_keywords
        assert "sql" in config.relevance_keywords

    def test_auto_tiers_skills(self):
        skills = ["Python", "SQL", "Java", "React", "Node", "Docker",
                   "K8s", "AWS", "GCP"]
        profile = self._make_profile(skills=skills)
        config = generate_search_config(profile)
        assert len(config.primary_skills) > 0
        assert len(config.secondary_skills) > 0
        assert len(config.tertiary_skills) > 0
        total = len(config.primary_skills) + len(config.secondary_skills) + len(config.tertiary_skills)
        assert total == len(skills)

    def test_single_skill_becomes_primary(self):
        profile = self._make_profile(skills=["Python"])
        config = generate_search_config(profile)
        assert config.primary_skills == ["Python"]
        assert config.secondary_skills == []
        assert config.tertiary_skills == []

    def test_empty_skills_no_crash(self):
        profile = self._make_profile()
        config = generate_search_config(profile)
        assert config.primary_skills == []
        assert config.secondary_skills == []
        assert config.tertiary_skills == []

    def test_locations_include_uk_defaults(self):
        profile = self._make_profile(locations=["Berlin"])
        config = generate_search_config(profile)
        assert "London" in config.locations
        assert "UK" in config.locations
        assert "Berlin" in config.locations

    def test_work_arrangement_added_to_locations(self):
        profile = self._make_profile(arrangement="remote")
        config = generate_search_config(profile)
        assert "Remote" in config.locations

    def test_negative_keywords_passed_through(self):
        profile = self._make_profile(negatives=["intern", "junior"])
        config = generate_search_config(profile)
        assert "intern" in config.negative_title_keywords
        assert "junior" in config.negative_title_keywords

    def test_search_queries_generated(self):
        profile = self._make_profile(
            titles=["Software Engineer"],
            locations=["London"],
        )
        config = generate_search_config(profile)
        assert len(config.search_queries) > 0
        assert "Software Engineer London" in config.search_queries

    def test_search_queries_default_uk(self):
        profile = self._make_profile(titles=["Data Scientist"])
        config = generate_search_config(profile)
        assert any("UK" in q for q in config.search_queries)

    def test_core_domain_words_extracted(self):
        profile = self._make_profile(titles=["Machine Learning Engineer"])
        config = generate_search_config(profile)
        assert "machine" in config.core_domain_words
        assert "learning" in config.core_domain_words
        # "engineer" is a role word, goes to supporting
        assert "engineer" in config.supporting_role_words

    def test_visa_keywords_always_present(self):
        profile = self._make_profile()
        config = generate_search_config(profile)
        assert config.visa_keywords == list(VISA_KEYWORDS)


# -----------------------------------------------------------------------
# JobScorer — dynamic scoring with SearchConfig
# -----------------------------------------------------------------------

class TestJobScorer:
    @pytest.fixture
    def default_scorer(self):
        return JobScorer(SearchConfig.from_defaults())

    @pytest.fixture
    def custom_scorer(self):
        """A scorer for a sales/marketing domain."""
        return JobScorer(SearchConfig(
            job_titles=["Sales Manager", "Account Executive", "Business Developer"],
            primary_skills=["Salesforce", "CRM", "Negotiation"],
            secondary_skills=["Excel", "HubSpot", "Cold Calling"],
            tertiary_skills=["PowerPoint", "LinkedIn"],
            relevance_keywords=["sales", "crm", "b2b", "pipeline", "revenue"],
            negative_title_keywords=["intern", "warehouse"],
            locations=["London", "UK", "Remote"],
            visa_keywords=["visa sponsorship", "sponsorship"],
            core_domain_words={"sales", "business", "account", "revenue", "crm"},
            supporting_role_words={"manager", "executive", "developer", "lead"},
            search_queries=["Sales Manager UK", "Account Executive London"],
        ))

    def test_default_scorer_matches_score_job(self, default_scorer, sample_ai_job):
        """Default scorer should produce same result as score_job()."""
        from src.filters.skill_matcher import score_job
        dynamic = default_scorer.score(sample_ai_job)
        static = score_job(sample_ai_job)
        assert dynamic == static

    def test_default_scorer_visa_matches(self, default_scorer, sample_visa_job):
        from src.filters.skill_matcher import check_visa_flag
        assert default_scorer.check_visa_flag(sample_visa_job) == check_visa_flag(sample_visa_job)

    def test_custom_scorer_sales_job_scores_high(self, custom_scorer):
        job = Job(
            title="Sales Manager",
            company="Acme",
            location="London, UK",
            description="Looking for a Sales Manager with Salesforce and CRM experience. B2B pipeline management.",
            apply_url="https://example.com/job",
            source="test",
            date_found=datetime.now(timezone.utc).isoformat(),
        )
        score = custom_scorer.score(job)
        assert score >= 50  # Good title match + skill match + location

    def test_custom_scorer_ai_job_scores_low(self, custom_scorer):
        """An AI job should score low with a sales config."""
        job = Job(
            title="AI Engineer",
            company="DeepMind",
            location="London, UK",
            description="PyTorch, TensorFlow, LLM fine-tuning",
            apply_url="https://example.com/job",
            source="test",
            date_found=datetime.now(timezone.utc).isoformat(),
        )
        score = custom_scorer.score(job)
        assert score < 30  # No title or skill match

    def test_custom_scorer_negative_penalty(self, custom_scorer):
        job = Job(
            title="Warehouse Intern",
            company="BigCo",
            location="London",
            description="Warehouse work with some CRM tasks",
            apply_url="https://example.com/job",
            source="test",
            date_found=datetime.now(timezone.utc).isoformat(),
        )
        score = custom_scorer.score(job)
        assert score < 20  # Negative keywords should penalize

    def test_custom_scorer_visa_flag(self, custom_scorer):
        job = Job(
            title="Sales Manager",
            company="Acme",
            location="London",
            description="Visa sponsorship available for qualified candidates.",
            apply_url="https://example.com/job",
            source="test",
            date_found=datetime.now(timezone.utc).isoformat(),
        )
        assert custom_scorer.check_visa_flag(job) is True

    def test_scorer_score_range(self, default_scorer):
        """Score must always be 0-100."""
        job = Job(
            title="Random Title",
            company="Unknown",
            location="Mars",
            description="Nothing relevant here at all",
            apply_url="https://example.com",
            source="test",
            date_found="2020-01-01",
        )
        score = default_scorer.score(job)
        assert 0 <= score <= 100

    def test_scorer_empty_config_no_crash(self):
        """Scorer with empty config should not crash."""
        scorer = JobScorer(SearchConfig())
        job = Job(
            title="Anything",
            company="Corp",
            location="London",
            description="Some description",
            apply_url="https://example.com",
            source="test",
            date_found=datetime.now(timezone.utc).isoformat(),
        )
        score = scorer.score(job)
        assert 0 <= score <= 100


# -----------------------------------------------------------------------
# Edge cases — storage, cv_parser, single-char skills
# -----------------------------------------------------------------------

class TestStorageEdgeCases:
    def test_storage_roundtrip_empty_file(self, tmp_path):
        """Empty PROFILE_PATH should return None, not crash."""
        path = tmp_path / ""  # empty suffix
        with patch("src.profile.storage.PROFILE_PATH", path):
            assert load_profile() is None


class TestCVParserEdgeCases:
    def test_doc_format_rejected(self):
        """Legacy .doc files should return empty string with warning."""
        from src.profile.cv_parser import extract_text
        result = extract_text("resume.doc")
        assert result == ""

    def test_single_char_skills_r_and_c(self):
        """R and C should be preserved as valid single-char skills."""
        text = "R, Python, C, Java"
        skills = _extract_skills_from_text(text)
        assert "R" in skills
        assert "C" in skills
        assert "Python" in skills
        assert "Java" in skills
