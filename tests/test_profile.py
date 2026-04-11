"""Tests for the src/profile/ package — models, cv_parser, preferences, storage, keyword_generator."""

import json
import os
import tempfile
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import patch, AsyncMock

import pytest

from src.models import Job
from src.profile.models import CVData, UserPreferences, UserProfile, SearchConfig
from src.profile.preferences import validate_preferences, merge_cv_and_preferences
from src.profile.keyword_generator import generate_search_config
from src.profile.storage import save_profile, load_profile, profile_exists
from src.filters.skill_matcher import JobScorer
from src.config.keywords import VISA_KEYWORDS


# -----------------------------------------------------------------------
# SearchConfig.from_defaults() — no domain assumptions; empty skill lists
# -----------------------------------------------------------------------

class TestSearchConfigDefaults:
    def test_from_defaults_job_titles_empty(self):
        cfg = SearchConfig.from_defaults()
        assert cfg.job_titles == []

    def test_from_defaults_primary_skills_empty(self):
        cfg = SearchConfig.from_defaults()
        assert cfg.primary_skills == []

    def test_from_defaults_secondary_skills_empty(self):
        cfg = SearchConfig.from_defaults()
        assert cfg.secondary_skills == []

    def test_from_defaults_tertiary_skills_empty(self):
        cfg = SearchConfig.from_defaults()
        assert cfg.tertiary_skills == []

    def test_from_defaults_relevance_keywords_empty(self):
        cfg = SearchConfig.from_defaults()
        assert cfg.relevance_keywords == []

    def test_from_defaults_negative_keywords_empty(self):
        cfg = SearchConfig.from_defaults()
        assert cfg.negative_title_keywords == []

    def test_from_defaults_visa_keywords_populated(self):
        cfg = SearchConfig.from_defaults()
        assert cfg.visa_keywords == list(VISA_KEYWORDS)

    def test_from_defaults_core_domain_words_empty(self):
        cfg = SearchConfig.from_defaults()
        assert cfg.core_domain_words == set()

    def test_from_defaults_supporting_role_words_empty(self):
        cfg = SearchConfig.from_defaults()
        assert cfg.supporting_role_words == set()


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

    def test_default_scorer_returns_valid_score(self, default_scorer, sample_ai_job):
        """Default scorer (empty config) returns a score in valid 0-100 range."""
        score = default_scorer.score(sample_ai_job)
        assert 0 <= score <= 100

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


# -----------------------------------------------------------------------
# LLM CV Parser
# -----------------------------------------------------------------------

class TestLLMCVParser:
    """Tests for the LLM-based CV parser."""

    def test_llm_result_to_cvdata_tech_cv(self):
        """LLM result for a tech CV populates CVData correctly."""
        from src.profile.cv_parser import _llm_result_to_cvdata

        result = {
            "name": "Ranjith Guruprakash",
            "headline": "AI/ML Engineer | Generative AI Specialist",
            "location": "United Kingdom",
            "summary": "AI/ML Engineer with 1.5 years of experience.",
            "skills": ["Python", "PyTorch", "TensorFlow", "AWS Bedrock", "Docker"],
            "experience": [
                {"company": "Calnex", "title": "AI Solutions Engineer", "dates": "June 2025",
                 "location": "UK", "bullets": ["Built RAG pipeline"]}
            ],
            "education": [
                {"degree": "MSc AI and Robotics", "institution": "Univ of Hertfordshire",
                 "dates": "2022-2024", "details": ["Neural Networks", "Machine Learning"]}
            ],
            "certifications": ["AWS Certified AI Practitioner (2025)"],
            "achievements": ["achieving 95% response accuracy"],
            "experience_level": "mid",
            "industries": ["AI/ML"],
            "languages": ["English"],
        }

        cv = _llm_result_to_cvdata("raw cv text here", result)
        # Scoring-semantic fields: ONLY clean skills (no name/achievement pollution)
        assert "Python" in cv.skills
        assert "AWS Bedrock" in cv.skills
        assert "Ranjith Guruprakash" not in cv.skills  # name is in cv.name, not skills
        assert "achieving 95% response accuracy" not in cv.skills  # in cv.achievements
        # Display-only fields
        assert cv.name == "Ranjith Guruprakash"
        assert "Generative AI" in cv.headline
        assert "Kingdom" in cv.location
        assert "achieving 95% response accuracy" in cv.achievements
        # Companies and titles are separate
        assert any("Calnex" in c for c in cv.companies)
        assert any("AI Solutions Engineer" in t for t in cv.job_titles)
        assert "Calnex" not in " ".join(cv.job_titles)  # company stays out of titles
        # Education and certifications
        assert any("MSc" in e for e in cv.education)
        assert any("AWS" in c for c in cv.certifications)
        assert "1.5 years" in cv.summary
        # highlights property merges everything for the CV viewer
        assert "Ranjith Guruprakash" in cv.highlights
        assert "Python" in cv.highlights
        assert "Calnex" in cv.highlights
        assert "achieving 95% response accuracy" in cv.highlights

    def test_llm_result_to_cvdata_medical_cv(self):
        """LLM result for a medical CV works just as well — domain-agnostic."""
        from src.profile.cv_parser import _llm_result_to_cvdata

        result = {
            "name": "Dr. Sarah Thompson",
            "headline": "Cardiology Consultant",
            "location": "London, UK",
            "summary": "Experienced cardiologist with 10 years of clinical practice.",
            "skills": ["Echocardiography", "Cardiac Catheterization", "HIPAA", "Patient Triage",
                       "EHR Systems", "Clinical Trials", "Medical Research"],
            "experience": [
                {"company": "NHS Royal Free", "title": "Cardiology Consultant",
                 "dates": "2018-Present", "location": "London",
                 "bullets": ["Led cardiac unit with 40% reduced wait times"]}
            ],
            "education": [
                {"degree": "MBBS Medicine", "institution": "University of Oxford",
                 "dates": "2004-2010", "details": ["Honours in Cardiology"]}
            ],
            "certifications": ["MRCP Cardiology — Royal College of Physicians (2012)"],
            "achievements": ["reduced patient wait times by 40%"],
            "experience_level": "senior",
            "industries": ["Healthcare", "Cardiology"],
            "languages": ["English", "French"],
        }

        cv = _llm_result_to_cvdata("raw medical cv text", result)
        assert "Echocardiography" in cv.skills
        assert "HIPAA" in cv.skills
        assert "Patient Triage" in cv.skills
        # Scoring-safe: name is NOT in skills
        assert "Dr. Sarah Thompson" not in cv.skills
        assert cv.name == "Dr. Sarah Thompson"
        assert cv.headline == "Cardiology Consultant"
        assert any("Cardiology Consultant" in t for t in cv.job_titles)
        assert any("NHS Royal Free" in c for c in cv.companies)
        assert any("Oxford" in e for e in cv.education)
        assert any("MRCP" in c for c in cv.certifications)
        # Highlights for CV viewer merges everything
        assert "Dr. Sarah Thompson" in cv.highlights
        assert "HIPAA" in cv.highlights
        assert "NHS Royal Free" in cv.highlights

    def test_llm_result_to_cvdata_empty(self):
        """Empty LLM result produces empty CVData without crashing."""
        from src.profile.cv_parser import _llm_result_to_cvdata

        cv = _llm_result_to_cvdata("some raw text", {})
        assert cv.raw_text == "some raw text"
        assert cv.skills == []
        assert cv.job_titles == []
        assert cv.education == []

    def test_llm_result_type_guard_string_skills(self):
        """Weaker LLMs may return 'skills' as a comma-separated string — handle it."""
        from src.profile.cv_parser import _llm_result_to_cvdata

        result = {"skills": "Python, Java, Docker, Kubernetes"}
        cv = _llm_result_to_cvdata("text", result)
        assert "Python" in cv.skills
        assert "Java" in cv.skills
        assert "Docker" in cv.skills
        assert "Kubernetes" in cv.skills

    def test_llm_result_type_guard_none_skills(self):
        """LLM returning None for skills should not crash."""
        from src.profile.cv_parser import _llm_result_to_cvdata

        cv = _llm_result_to_cvdata("text", {"skills": None, "achievements": None})
        assert cv.skills == []
        assert cv.achievements == []

    def test_llm_result_type_guard_dict_items(self):
        """LLM returning list of dicts instead of strings — extract name field."""
        from src.profile.cv_parser import _llm_result_to_cvdata

        result = {
            "skills": [{"name": "Python"}, {"name": "Docker"}, {"skill": "AWS"}]
        }
        cv = _llm_result_to_cvdata("text", result)
        assert "Python" in cv.skills
        assert "Docker" in cv.skills
        assert "AWS" in cv.skills

    def test_llm_result_type_guard_wrong_types(self):
        """Numbers, bools, nested dicts should be coerced or dropped, never crash."""
        from src.profile.cv_parser import _llm_result_to_cvdata

        result = {
            "name": 123,  # wrong type
            "skills": ["Python", None, 42, {"name": "Docker"}],  # mixed
            "headline": ["not", "a", "string"],  # wrong type
            "summary": None,
        }
        cv = _llm_result_to_cvdata("text", result)
        assert cv.name == "123"  # coerced
        assert cv.headline == ""  # wrong type → empty
        assert cv.summary == ""
        assert "Python" in cv.skills
        assert "Docker" in cv.skills
        # None and 42 dropped from list cleanly


class TestCVParserFailures:
    """Tests for C2 — parse_cv_async must raise, not silently return empty."""

    @pytest.mark.asyncio
    async def test_parse_cv_async_raises_on_empty_text(self):
        """If text extraction yields empty string, raise RuntimeError."""
        from unittest.mock import patch
        from src.profile.cv_parser import parse_cv_async

        with patch("src.profile.cv_parser.extract_text", return_value=""):
            with pytest.raises(RuntimeError, match="Failed to extract text"):
                await parse_cv_async("broken.pdf")


class TestCVParserEdgeCases:
    def test_doc_format_rejected(self):
        """Legacy .doc files should return empty string with warning."""
        from src.profile.cv_parser import extract_text
        result = extract_text("resume.doc")
        assert result == ""
