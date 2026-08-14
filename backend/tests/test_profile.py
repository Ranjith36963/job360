"""Tests for the src/profile/ package — models, cv_parser, preferences, storage, keyword_generator."""

from datetime import datetime, timezone

import pytest

from src.core.keywords import VISA_KEYWORDS
from src.models import Job
from src.services.profile.keyword_generator import generate_search_config
from src.services.profile.models import CVData, SearchConfig, UserPreferences, UserProfile
from src.services.profile.preferences import merge_cv_and_preferences, validate_preferences
from src.services.skill_matcher import JobScorer

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
        # BEHAVIOUR CHANGE (skill-quality fix): additional_skills is the USER's
        # extras only and no longer absorbs cv_skills — folding the CV in here
        # collapsed the skill tiers (everything scored user_declared) and stuffed
        # the preferences box. Titles still merge (prefs first, CV appended).
        cv_skills = ["Python", "SQL", "Java"]
        cv_titles = ["Software Engineer"]
        prefs = UserPreferences(
            target_job_titles=["Software Engineer", "Data Analyst"],
            additional_skills=["Python", "React"],
        )
        merged = merge_cv_and_preferences(cv_skills, cv_titles, prefs)
        # Titles: prefs first, CV deduped
        assert merged.target_job_titles == ["Software Engineer", "Data Analyst"]
        # Skills: the user's own extras, deduped — NOT the CV skills.
        assert merged.additional_skills == ["Python", "React"]
        assert "SQL" not in merged.additional_skills
        assert "Java" not in merged.additional_skills

    def test_merge_excludes_skills(self):
        # excluded_skills now filters the user's OWN additional_skills (cv skills
        # are not merged in at all).
        prefs = UserPreferences(
            additional_skills=["React", "Java"],
            excluded_skills=["Java"],
        )
        merged = merge_cv_and_preferences(["Python", "SQL"], [], prefs)
        assert "Java" not in merged.additional_skills   # excluded
        assert "React" in merged.additional_skills       # user extra kept
        assert "Python" not in merged.additional_skills  # cv skill not folded in

    def test_merge_preserves_github_username(self):
        """BUG-1 regression: github_username must survive merge."""
        prefs = UserPreferences(
            additional_skills=["Python"],
            github_username="testuser",
        )
        merged = merge_cv_and_preferences(["SQL"], [], prefs)
        assert merged.github_username == "testuser"

    def test_cv_job_titles_do_not_pollute_target_roles(self):
        """TRUST: past CV job titles ('AI Solutions Engineer – R&D Department',
        'AI/ML Engineer Intern') were dumped into 'Roles you're targeting' with
        near-duplicates. target_job_titles = what the USER wants, not past roles."""
        prefs = UserPreferences(target_job_titles=["AI Engineer", "ML Engineer"])
        merged = merge_cv_and_preferences(
            [], ["AI Solutions Engineer – R&D Department", "AI/ML Engineer Intern"], prefs
        )
        assert merged.target_job_titles == ["AI Engineer", "ML Engineer"]
        assert "AI/ML Engineer Intern" not in merged.target_job_titles

    def test_apply_preferences_preserves_fields_the_form_omits(self):
        """TRUST/data-loss BUG: saving the preferences form built a FRESH
        UserPreferences, wiping fields the form doesn't carry — github_username
        (set by the separate GitHub route), preferred_workplace, needs_visa. Those
        must be preserved when the form omits them."""
        import json

        from src.api.routes.profile import _apply_preferences
        from src.services.profile.models import CVData, UserProfile

        profile = UserProfile(
            cv_data=CVData(),
            preferences=UserPreferences(
                github_username="ranjith36963",
                work_arrangement="remote",
                needs_visa=True,
            ),
        )
        # a normal preferences save — no github_username / workplace / visa in it
        _apply_preferences(
            json.dumps({"target_job_titles": ["AI Engineer"], "additional_skills": ["Docker"]}),
            profile,
        )
        # form fields applied…
        assert profile.preferences.target_job_titles == ["AI Engineer"]
        assert profile.preferences.additional_skills == ["Docker"]
        # …and the omitted fields survived
        assert profile.preferences.github_username == "ranjith36963"
        assert profile.preferences.preferred_workplace == "remote"
        assert profile.preferences.needs_visa is True


# -----------------------------------------------------------------------
# Profile storage — see tests/test_profile_storage.py for per-user
# DB-backed tests (Batch 3.5.2). The old JSON-file TestProfileStorage
# class that lived here was replaced when storage moved from
# data/user_profile.json to the user_profiles table.
# -----------------------------------------------------------------------


# -----------------------------------------------------------------------
# Keyword generator
# -----------------------------------------------------------------------


class TestKeywordGenerator:
    def _make_profile(self, titles=None, skills=None, locations=None, arrangement="", negatives=None, declared=None):
        """Build a profile. ``skills`` land in ``cv.skills`` (cv_explicit source);
        ``declared`` lands in ``preferences.additional_skills`` (user_declared source).
        The distinction matters under Batch 1.3a's evidence-based tiering —
        user_declared has weight 3.0 (primary), cv_explicit 2.0 (secondary).
        """
        return UserProfile(
            cv_data=CVData(
                raw_text="test cv",
                skills=skills or [],
                job_titles=[],
            ),
            preferences=UserPreferences(
                target_job_titles=titles or [],
                additional_skills=declared or [],
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

    def test_evidence_tiers_across_source_mix(self):
        """Batch 1.3a — declared vs cv-only vs github_lang-only split by weight.

        Replaces the old ``test_auto_tiers_skills`` which asserted naive
        position-based thirds. Under evidence-based tiering:

          * ``user_declared`` (weight 3.0) → primary
          * ``cv_explicit``   (weight 2.0) → secondary
          * ``github_lang``   (weight 1.0) → tertiary
        """
        profile = UserProfile(
            cv_data=CVData(
                raw_text="t",
                skills=["Django", "Flask"],  # cv_explicit → secondary
                github_languages={"Haskell": 200_000},  # github_lang  → tertiary
            ),
            preferences=UserPreferences(
                additional_skills=["Product Strategy"],  # user_declared → primary
            ),
        )
        cfg = generate_search_config(profile)
        # Batch 2.3 — skill lists are now canonicalised (lower-cased,
        # alias-resolved) before they enter the SearchConfig.
        assert cfg.primary_skills == ["product strategy"]
        assert set(cfg.secondary_skills) == {"django", "flask"}
        assert cfg.tertiary_skills == ["haskell"]
        total = len(cfg.primary_skills) + len(cfg.secondary_skills) + len(cfg.tertiary_skills)
        assert total == 4

    def test_single_cv_skill_tiers_as_secondary(self):
        """Batch 1.3a — a single CV-extracted skill has only cv_explicit evidence
        (weight 2.0) and MUST land in secondary. Primary requires either
        user_declared OR multi-source evidence — never a single non-user signal.
        """
        profile = self._make_profile(skills=["Python"])
        config = generate_search_config(profile)
        # Batch 2.3 — canonicalisation lower-cases skills on SearchConfig exit.
        assert config.primary_skills == []
        assert config.secondary_skills == ["python"]
        assert config.tertiary_skills == []

    def test_single_user_declared_skill_becomes_primary(self):
        """Batch 1.3a — an explicit user-declared skill remains primary.

        Batch 2.3 — canonicalisation lower-cases skills on SearchConfig exit.
        """
        profile = self._make_profile(declared=["Python"])
        config = generate_search_config(profile)
        assert config.primary_skills == ["python"]
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
        return JobScorer(
            SearchConfig(
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
            )
        )

    def test_default_scorer_returns_valid_score(self, default_scorer, sample_ai_job):
        """Default scorer (empty config) returns a score in valid 0-100 range."""
        score = default_scorer.score(sample_ai_job).match_score
        assert 0 <= score <= 100

    def test_default_scorer_visa_matches(self, default_scorer, sample_visa_job):
        from src.services.skill_matcher import check_visa_flag

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
        score = custom_scorer.score(job).match_score
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
        score = custom_scorer.score(job).match_score
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
        score = custom_scorer.score(job).match_score
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
        score = default_scorer.score(job).match_score
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
        score = scorer.score(job).match_score
        assert 0 <= score <= 100


# -----------------------------------------------------------------------
# Edge cases — storage, cv_parser, single-char skills
# -----------------------------------------------------------------------

# -----------------------------------------------------------------------
# LLM CV Parser
# -----------------------------------------------------------------------


class TestLLMCVParser:
    """Tests for the LLM-based CV parser."""

    def test_llm_result_to_cvdata_tech_cv(self):
        """LLM result for a tech CV populates CVData correctly."""
        from src.services.profile.cv_parser import _llm_result_to_cvdata

        result = {
            "name": "Ranjith Guruprakash",
            "headline": "AI/ML Engineer | Generative AI Specialist",
            "location": "United Kingdom",
            "summary": "AI/ML Engineer with 1.5 years of experience.",
            "skills": ["Python", "PyTorch", "TensorFlow", "AWS Bedrock", "Docker"],
            "experience": [
                {
                    "company": "Calnex",
                    "title": "AI Solutions Engineer",
                    "dates": "June 2025",
                    "location": "UK",
                    "bullets": ["Built RAG pipeline"],
                }
            ],
            "education": [
                {
                    "degree": "MSc AI and Robotics",
                    "institution": "Univ of Hertfordshire",
                    "dates": "2022-2024",
                    "details": ["Neural Networks", "Machine Learning"],
                }
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
        from src.services.profile.cv_parser import _llm_result_to_cvdata

        result = {
            "name": "Dr. Sarah Thompson",
            "headline": "Cardiology Consultant",
            "location": "London, UK",
            "summary": "Experienced cardiologist with 10 years of clinical practice.",
            "skills": [
                "Echocardiography",
                "Cardiac Catheterization",
                "HIPAA",
                "Patient Triage",
                "EHR Systems",
                "Clinical Trials",
                "Medical Research",
            ],
            "experience": [
                {
                    "company": "NHS Royal Free",
                    "title": "Cardiology Consultant",
                    "dates": "2018-Present",
                    "location": "London",
                    "bullets": ["Led cardiac unit with 40% reduced wait times"],
                }
            ],
            "education": [
                {
                    "degree": "MBBS Medicine",
                    "institution": "University of Oxford",
                    "dates": "2004-2010",
                    "details": ["Honours in Cardiology"],
                }
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
        from src.services.profile.cv_parser import _llm_result_to_cvdata

        cv = _llm_result_to_cvdata("some raw text", {})
        assert cv.raw_text == "some raw text"
        assert cv.skills == []
        assert cv.job_titles == []
        assert cv.education == []

    def test_llm_result_type_guard_string_skills(self):
        """Weaker LLMs may return 'skills' as a comma-separated string — handle it."""
        from src.services.profile.cv_parser import _llm_result_to_cvdata

        result = {"skills": "Python, Java, Docker, Kubernetes"}
        cv = _llm_result_to_cvdata("text", result)
        assert "Python" in cv.skills
        assert "Java" in cv.skills
        assert "Docker" in cv.skills
        assert "Kubernetes" in cv.skills

    def test_llm_result_type_guard_none_skills(self):
        """LLM returning None for skills should not crash."""
        from src.services.profile.cv_parser import _llm_result_to_cvdata

        cv = _llm_result_to_cvdata("text", {"skills": None, "achievements": None})
        assert cv.skills == []
        assert cv.achievements == []

    def test_llm_result_type_guard_dict_items(self):
        """LLM returning list of dicts instead of strings — extract name field."""
        from src.services.profile.cv_parser import _llm_result_to_cvdata

        result = {"skills": [{"name": "Python"}, {"name": "Docker"}, {"skill": "AWS"}]}
        cv = _llm_result_to_cvdata("text", result)
        assert "Python" in cv.skills
        assert "Docker" in cv.skills
        assert "AWS" in cv.skills

    def test_llm_result_type_guard_wrong_types(self):
        """Numbers, bools, nested dicts should be coerced or dropped, never crash."""
        from src.services.profile.cv_parser import _llm_result_to_cvdata

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


class TestCvPromptRequestsCareerDomain:
    """BUG 1a (Pillar-1 audit 2026-08-07). `_CV_PROMPT`'s JSON schema never
    asked for `career_domain`, even though `CVSchema.career_domain` and the
    `CareerDomain` enum have existed since Batch 1.1 — so the LLM had no
    reason to ever return one. Asserts PROMPT CONTENT (matches the style of
    test_cv_prompt_steering.py), not LLM behaviour."""

    def test_prompt_asks_for_career_domain(self):
        from src.services.profile import cv_parser

        p = cv_parser._CV_PROMPT.lower()
        assert '"career_domain"' in p

    def test_prompt_lists_the_real_enum_members_not_invented_values(self):
        """The allowed values in the prompt must be the ACTUAL `CareerDomain`
        members — a mismatched or partial list would have the model return
        buckets `CVSchema` then rejects, wasting a retry."""
        from src.services.profile import cv_parser
        from src.services.profile.schemas import CareerDomain

        p = cv_parser._CV_PROMPT
        for member in CareerDomain:
            assert member.value in p, f"{member.value!r} missing from the CV prompt"

    def test_prompt_tells_the_model_to_return_null_when_unclear(self):
        """Steering against guessing — a wrong classification corrupts
        downstream archetype-aware scoring more than an absent one."""
        from src.services.profile import cv_parser

        p = cv_parser._CV_PROMPT.lower()
        assert "null" in p
        assert "guess" in p


class TestCvSchemaEscoNormalisation:
    """BUG 2 (Pillar-1 audit 2026-08-07). `_maybe_normalise_skills_via_esco`
    was only ever called from `cv_parser._llm_result_to_cvdata` — the untyped
    DEFENSIVE FALLBACK, reached only when strict `CVSchema` validation
    exhausts its retries. `cv_schema_to_cvdata` (the path every successful
    extraction actually returns through) built `CVData(...)` with no
    `cv_skills_esco=` argument at all, so prod (`SEMANTIC_ENABLED=true`)
    shipped `cv_skills_esco={}` on every profile regardless of the flag.
    """

    def _schema(self):
        from src.services.profile.schemas import CVSchema

        return CVSchema(skills=["Python", "Docker"])

    def test_populates_cv_skills_esco_when_esco_data_is_available(self):
        from unittest.mock import patch

        from src.services.profile.schemas import cv_schema_to_cvdata

        fake_map = {"Python": "http://esco/python"}
        with patch(
            "src.services.profile.schemas._maybe_normalise_skills_via_esco",
            return_value=(["Python", "Docker"], fake_map),
        ) as mock_norm:
            cv = cv_schema_to_cvdata(self._schema(), "raw")

        mock_norm.assert_called_once_with(["Python", "Docker"])
        assert cv.cv_skills_esco == fake_map

    def test_returns_empty_dict_without_raising_when_esco_unavailable(self):
        """Flag-off / no-index-on-disk is the DEFAULT runtime state
        (`SEMANTIC_ENABLED` defaults false, root rule #18) — this must
        degrade to `{}` silently, never raise. No mocking: the test
        environment genuinely has the flag off and no ESCO index on disk
        (conftest.py + no backend/data/esco/), so the real normaliser runs
        its own no-op path."""
        from src.services.profile.schemas import cv_schema_to_cvdata

        cv = cv_schema_to_cvdata(self._schema(), "raw")
        assert cv.cv_skills_esco == {}
        assert cv.skills == ["Python", "Docker"]


class TestCVParserFailures:
    """Tests for C2 — parse_cv_async must raise, not silently return empty."""

    @pytest.mark.asyncio
    async def test_parse_cv_async_raises_on_empty_text(self):
        """If text extraction yields empty string, raise RuntimeError."""
        from unittest.mock import patch

        import pytest

        from src.services.profile.cv_parser import parse_cv_async

        with patch("src.services.profile.cv_parser.extract_text", return_value=""):
            with pytest.raises(RuntimeError, match="Failed to extract text"):
                await parse_cv_async("broken.pdf")


class TestCVParserEdgeCases:
    def test_doc_format_rejected(self):
        """Legacy .doc files should return empty string with warning."""
        from src.services.profile.cv_parser import extract_text

        result = extract_text("resume.doc")
        assert result == ""


# ---------------------------------------------------------------------------
# has_linkedin reflects ANY merged LinkedIn signal (B: positions, not just skills)
# ---------------------------------------------------------------------------


def test_has_linkedin_true_from_positions_even_without_skills():
    """A LinkedIn PDF that yields positions but no detected skills still counts
    as 'has LinkedIn'. The flag used to check only ``linkedin_skills``, so a
    successful upload (route returns merged=True on skills OR positions) left
    has_linkedin=False — the dashboard never showed LinkedIn as connected."""
    from src.api.routes.profile import _build_profile_response

    profile = UserProfile(
        cv_data=CVData(
            raw_text="x", skills=["python"], job_titles=["ML Engineer"],
            linkedin_skills=[],  # none detected
            linkedin_positions=[{"title": "Senior ML Engineer", "company": "Acme"}],
        ),
        preferences=UserPreferences(target_job_titles=["ML Engineer"]),
    )
    # user_id is now required: the helper used to derive it via
    # getattr(profile, "user_id", None) against an object that has no such
    # field, so every caller silently fell back to the default tenant.
    resp = _build_profile_response(profile, "00000000-0000-0000-0000-000000000001")
    assert resp.summary.has_linkedin is True


# ═══ Dead-title-lever fix (P0-3, funnel redesign 2026-08-05) ══════════════════
# core_domain_words used to be built ONLY from title tokens, so a user with two
# hyper-specific CV titles got a 4-word vocabulary polluted by junk tokens
# ('department', 'solutions' from "AI Solutions Engineer – R&D Department").
# Measured on real prod data: the 40-pt title lever never scored above 8 for
# ANY job, capping all scores at ~69 and making location the de-facto sort key.
# The fix is evidence-based: a title token joins core only with corroboration
# (>=2 titles, the skill pool, or an acronym); high-frequency skill tokens join
# core so "Machine Learning Engineer" matches a deep-learning profile.


class TestCoreDomainWordEvidence:
    def _profile(self, titles, skills=None):
        from src.services.profile.models import CVData, UserPreferences, UserProfile
        return UserProfile(
            cv_data=CVData(job_titles=titles, skills=skills or []),
            preferences=UserPreferences(),
        )

    def test_junk_single_title_tokens_are_dropped(self):
        """'department'/'solutions' appear in ONE title and no skill — they are
        noise from a CV job-title string, not the user's domain."""
        profile = self._profile(
            titles=["AI Solutions Engineer - R&D Department", "AI/ML Engineer Intern"],
            skills=["Deep Learning", "Generative AI"],
        )
        cfg = generate_search_config(profile)
        assert "department" not in cfg.core_domain_words
        assert "solutions" not in cfg.core_domain_words

    def test_cross_title_and_acronym_tokens_survive(self):
        """'ai' appears in both titles (corroborated); 'ml' is a short acronym
        token — both are real domain signal and must stay."""
        profile = self._profile(
            titles=["AI Solutions Engineer - R&D Department", "AI/ML Engineer Intern"],
            skills=["Deep Learning"],
        )
        cfg = generate_search_config(profile)
        assert "ai" in cfg.core_domain_words
        assert "ml" in cfg.core_domain_words

    def test_frequent_skill_tokens_join_core(self):
        """Tokens appearing across >=2 skills describe the user's domain even
        when absent from titles — this is what lets 'Machine Learning Engineer'
        postings match a profile whose titles never contain 'learning'."""
        profile = self._profile(
            titles=["AI Engineer"],
            skills=["Deep Learning", "Supervised Learning", "Machine Learning Ops"],
        )
        cfg = generate_search_config(profile)
        assert "learning" in cfg.core_domain_words

    def test_single_title_no_skills_falls_back_to_all_tokens(self):
        """A thin profile (one title, no skills) must not end up with an EMPTY
        core vocabulary — fall back to the old all-title-tokens behaviour."""
        profile = self._profile(titles=["Underwriting Portfolio Manager"])
        cfg = generate_search_config(profile)
        assert "underwriting" in cfg.core_domain_words
        assert "portfolio" in cfg.core_domain_words

    def test_role_words_still_go_to_supporting(self):
        profile = self._profile(titles=["AI Engineer", "ML Engineer"])
        cfg = generate_search_config(profile)
        assert "engineer" in cfg.supporting_role_words
        assert "engineer" not in cfg.core_domain_words

    def test_near_duplicate_titles_count_as_one_evidence(self):
        """The same title with a hyphen vs em-dash (or arriving from both CV and
        LinkedIn) must not fake cross-title corroboration for its junk tokens."""
        profile = self._profile(
            titles=[
                "AI Solutions Engineer - R&D Department",
                "AI Solutions Engineer – R&D Department",  # em-dash variant
                "AI/ML Engineer Intern",
            ],
            skills=["Deep Learning"],
        )
        cfg = generate_search_config(profile)
        assert "department" not in cfg.core_domain_words
        assert "solutions" not in cfg.core_domain_words
        assert "ai" in cfg.core_domain_words


class TestCvSchemaCarriesDatedExperience:
    """PILLAR-1 AUDIT FINDING (2026-08-07, profile: Pavan).

    PR #241 added `cv_positions` to `cv_parser._llm_result_to_cvdata` — but
    `llm_cv_fields_from_text` returns through `schemas.cv_schema_to_cvdata`,
    so the fix reached no real extraction. Measured on a live CV: the LLM
    returned "Freelance AI Trainer & Subject Matter Expert, 2023 - 2024" and
    cv_positions still came out 0. Two adapters for one contract was the bug.
    """

    def _schema(self):
        from src.services.profile.schemas import CVSchema, ExperienceEntry

        return CVSchema(
            skills=["python"],
            experience=[
                ExperienceEntry(
                    company="Acme", title="ML Engineer", dates="2023 - 2024",
                    location="London", bullets=["Built pipelines"],
                ),
                ExperienceEntry(
                    company="", title="Freelance AI Trainer", dates="2022 - 2023",
                    location="Remote", bullets=[],
                ),
            ],
        )

    def test_dated_positions_survive_the_schema_adapter(self):
        from src.services.profile.schemas import cv_schema_to_cvdata

        cv = cv_schema_to_cvdata(self._schema(), "raw cv text")
        assert len(cv.cv_positions) == 2, "experience entries were dropped"
        first = cv.cv_positions[0]
        assert first["title"] == "ML Engineer"
        assert first["company"] == "Acme"
        assert first["dates"] == "2023 - 2024", "dates discarded again"
        assert first["location"] == "London"
        assert first["bullets"] == ["Built pipelines"]

    def test_title_and_company_stay_paired(self):
        """The flat lists cannot express WHICH title was held WHERE — a
        company-less entry silently shifts every later pairing."""
        from src.services.profile.schemas import cv_schema_to_cvdata

        cv = cv_schema_to_cvdata(self._schema(), "raw")
        assert cv.cv_positions[1]["title"] == "Freelance AI Trainer"
        assert cv.cv_positions[1]["company"] == ""
        # the legacy flat lists are exactly the trap: 2 titles, 1 company
        assert len(cv.job_titles) == 2 and len(cv.companies) == 1

    def test_entries_without_title_or_company_are_skipped(self):
        from src.services.profile.schemas import CVSchema, ExperienceEntry, cv_schema_to_cvdata

        schema = CVSchema(experience=[ExperienceEntry(bullets=["orphan bullet"])])
        assert cv_schema_to_cvdata(schema, "raw").cv_positions == []
