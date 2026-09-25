"""Tests for the src/services/profile/ package — models, cv_parser,
preferences.

Slice 5 (#483) removed the sections that tested the job SCORER's side of
this package: `keyword_generator` (the board-query builder), `SearchConfig`
and `JobScorer` are all deleted.

Decision 28 (2026-09-21) then removed every LLM pass the profile pipeline
had: the CV prompt and its provider chain, the two dict/schema -> CVData
adapters (`_llm_result_to_cvdata`, `cv_schema_to_cvdata`), `schemas.py`
(`CVSchema`, `CareerDomain`, `ExperienceEntry`, `EducationEntry`) and the
about-me / LinkedIn / GitHub LLM inference passes. Job360 no longer turns a
parsed CV into structured fields itself — the user's own agent reads
`raw_text` off `get_profile` and writes the fields back with
`update_profile`. Every test whose subject was that LLM behaviour (prompt
content, schema validation, the adapters, "the model returns X") is gone
with it; nothing here re-homes those assertions onto another function,
because the flattening they tested no longer happens anywhere.

What is left, and what this file tests: text extraction (PDF/DOCX), the
deterministic structural CV pass (`cv_parser.deterministic_cv_fields` /
`cv_data_from_text`) including ESCO skill normalisation, preference
validation/sanitising/merging, and CVData/UserProfile model behaviour.
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

    def test_apply_preferences_carries_daily_check_forward(self):
        """Owner decision 2026-09-25 — the preferences FORM never sends
        daily_check (only the connected assistant, via update_profile, sets
        it). A routine web preferences save must not reset it, the same bug
        class (PR #630) that already bit needs_visa/work_arrangement."""
        import json

        from src.api.routes.profile import _apply_preferences
        from src.services.profile.models import CVData, UserProfile

        profile = UserProfile(
            cv_data=CVData(),
            preferences=UserPreferences(daily_check="scheduled"),
        )
        _apply_preferences(json.dumps({"target_job_titles": ["AI Engineer"]}), profile)
        assert profile.preferences.daily_check == "scheduled"


# -----------------------------------------------------------------------
# Profile storage — see tests/test_profile_storage.py for per-user
# DB-backed tests (Batch 3.5.2). The old JSON-file TestProfileStorage
# class that lived here was replaced when storage moved from
# data/user_profile.json to the user_profiles table.
# -----------------------------------------------------------------------


# -----------------------------------------------------------------------
# ESCO skill normalisation — Step-1.5 S1.5-D
# -----------------------------------------------------------------------
#
# Decision 28 deleted both the LLM adapters that used to call
# `_maybe_normalise_skills_via_esco` (`_llm_result_to_cvdata` and
# `schemas.cv_schema_to_cvdata`). The normaliser itself survives — it is now
# called once, from `cv_parser.cv_data_from_text`, the single deterministic
# pass every CV goes through. Same behaviour, one call site instead of two
# that could (and did) disagree.


class TestCvDataFromTextEscoNormalisation:
    def test_populates_cv_skills_esco_when_esco_data_is_available(self):
        from unittest.mock import patch

        from src.services.profile.cv_parser import cv_data_from_text

        fake_map = {"Python": "http://esco/python"}
        with patch(
            "src.services.profile.cv_parser._maybe_normalise_skills_via_esco",
            return_value=(["Python", "Docker"], fake_map),
        ) as mock_norm:
            cv = cv_data_from_text("Skills\nPython, Docker")

        mock_norm.assert_called_once_with(["Python", "Docker"])
        assert cv.cv_skills_esco == fake_map

    def test_returns_empty_dict_without_raising_when_esco_unavailable(self):
        """Flag-off / no-index-on-disk is the DEFAULT runtime state
        (`ESCO_SKILL_NORMALISATION_ENABLED` defaults false, root rule #18) —
        this must degrade to `{}` silently, never raise. No mocking: the test
        environment genuinely has the flag off and no ESCO index on disk
        (conftest.py + no backend/data/esco/), so the real normaliser runs
        its own no-op path."""
        from src.services.profile.cv_parser import cv_data_from_text

        cv = cv_data_from_text("Skills\nPython, Docker")
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


# NOTE (decision 28, 2026-09-21): `TestCvSchemaCarriesDatedExperience` lived
# here — it asserted that `schemas.cv_schema_to_cvdata` turned LLM-returned
# experience entries into `cv.cv_positions`. That whole adapter is deleted:
# Job360 does not extract dated positions from a CV any more, the user's
# agent reads `raw_text` and writes `cv_positions` back via `update_profile`.
# Nothing to re-home the assertions onto — the flattening they tested no
# longer happens anywhere.
