"""Tests for the src/services/profile/ package — models, cv_parser,
preferences, storage.

Slice 5 (#483) removed the sections that tested the job SCORER's side of
this package: `keyword_generator` (the board-query builder), `SearchConfig`
and `JobScorer` are all deleted. What is left is profile extraction — the
half the mission keeps.
"""


import pytest

from src.services.profile.models import CVData, UserPreferences, UserProfile
from src.services.profile.preferences import merge_cv_and_preferences, validate_preferences

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
        # Finding 7 (Pillar-1 closeout audit) — the fallback adapter must
        # mirror the live adapter: education sub-bullets (coursework/thesis)
        # land on their OWN shelf, not mixed into the "degree — institution"
        # lines (a fix that left them mixed in would fail this — the two
        # adapters would disagree on what `cv.education` even contains).
        assert cv.cv_education_details == ["Neural Networks", "Machine Learning"]
        assert not any("Neural Networks" in e for e in cv.education)
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
    # agent_edits is passed IN (slice-4 review N4): the caller has already read
    # the overlay on the connection it loaded the profile with, so the helper
    # never opens a second one. Empty here — no agent has edited this profile.
    resp = _build_profile_response(profile, "00000000-0000-0000-0000-000000000001", [])
    assert resp.summary.has_linkedin is True


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
