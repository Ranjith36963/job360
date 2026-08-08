"""Preferences must hold ONLY what the user typed — never extraction leakage.

Found on a live smoke test 2026-08-08: a real user's additional_skills held 131
entries, 21+ of them CV job titles, company names, locations, date ranges, and
whole experience-bullet sentences; target_job_titles held his PAST roles, not
roles he was targeting. An older extraction merge dumped CV content into the
preference boxes, and the frontend autosaves it back on every touch, so it never
self-heals. These pin the fix: extraction never writes a preference, and the
sanitizer removes the pollution WITHOUT eating a real skill.
"""
from __future__ import annotations

from src.services.profile.models import CVData, UserPreferences
from src.services.profile.preferences import sanitize_preferences


def _cv() -> CVData:
    return CVData(
        skills=["Python", "PyTorch"],
        job_titles=["ML Engineer Intern", "AI Solutions Engineer – R&D Department"],
        companies=["Nethermind", "Calnex Solutions"],
        location="Stevenage, United Kingdom",
        cv_positions=[{
            "company": "Nethermind", "title": "ML Engineer Intern",
            "dates": "October 2024 – January 2025", "location": "Remote",
            "bullets": [
                "Designed and built scalable LLM systems using TensorFlow and PyTorch",
                "improving AI response relevance by 40% and reducing query latency by 35%.",
            ],
        }],
    )


class TestSanitizerIsZeroLoss:
    """It must drop the pollution and KEEP every real skill — even a skill that
    also appears inside an experience bullet (PyTorch, TensorFlow)."""

    def test_real_skills_survive_even_when_named_in_a_bullet(self) -> None:
        prefs = UserPreferences(additional_skills=[
            "Python", "PyTorch", "TensorFlow", "Generative AI", "AWS Bedrock",
            "Docker", "RAG",
        ])
        out = sanitize_preferences(prefs, _cv())
        for skill in ["Python", "PyTorch", "TensorFlow", "Generative AI",
                      "AWS Bedrock", "Docker", "RAG"]:
            assert skill in out.additional_skills, f"{skill} was wrongly dropped"

    def test_cv_content_is_dropped(self) -> None:
        prefs = UserPreferences(additional_skills=[
            "Nethermind", "Calnex Solutions", "United Kingdom",
            "AI Solutions Engineer – R&D Department",
        ])
        assert sanitize_preferences(prefs, _cv()).additional_skills == []

    def test_structural_junk_is_dropped(self) -> None:
        prefs = UserPreferences(additional_skills=[
            "October 2024 – January 2025",                      # date range
            "achieving 95% response accuracy and enabling it.",  # period + metric
            "with 92% user satisfaction.",                       # metric + period
            "Architected containerised multimodal Generative AI assistant using",  # >60
        ])
        assert sanitize_preferences(prefs, _cv()).additional_skills == []

    def test_verb_led_bullet_fragments_are_dropped(self) -> None:
        prefs = UserPreferences(additional_skills=[
            "improving AI response", "delivering context-aware solutions",
            "enhancing performance", "accelerating feature",
        ])
        assert sanitize_preferences(prefs, _cv()).additional_skills == []

    def test_target_titles_drop_past_roles(self) -> None:
        prefs = UserPreferences(
            target_job_titles=["ML Engineer Intern", "Staff ML Engineer"]
        )
        out = sanitize_preferences(prefs, _cv())
        assert "ML Engineer Intern" not in out.target_job_titles  # a past role
        assert "Staff ML Engineer" in out.target_job_titles       # a real target

    def test_clean_input_is_a_noop(self) -> None:
        prefs = UserPreferences(
            additional_skills=["Kubernetes", "Rust"],
            target_job_titles=["Staff Engineer"],
        )
        out = sanitize_preferences(prefs, _cv())
        assert out.additional_skills == ["Kubernetes", "Rust"]
        assert out.target_job_titles == ["Staff Engineer"]


class TestExtractionNeverWritesAPreference:
    """The permanent guard: running the full two-pass extraction with EMPTY
    preferences must leave every user-typed preference field empty. If a future
    change re-introduces CV->preference seeding, this fails."""

    def test_empty_prefs_stay_empty_after_extraction(self) -> None:
        import asyncio
        from unittest.mock import patch

        from src.services.profile import llm_curate, two_pass
        from src.services.profile.models import UserProfile

        async def _noop(*a, **kw):
            return []

        async def _pass(items, *a, **kw):
            return items

        async def _no_cv(*a, **kw):
            return None

        profile = UserProfile(
            cv_data=CVData(raw_text="CV text", skills=["Python"],
                           job_titles=["ML Engineer"]),
            preferences=UserPreferences(),  # user typed NOTHING
        )
        with patch.object(two_pass, "llm_cv_fields_from_text", _no_cv), \
             patch.object(two_pass, "llm_linkedin_fields", _no_cv), \
             patch.object(two_pass, "llm_infer_github_skills", _noop), \
             patch.object(two_pass, "llm_infer_from_about_me", _noop), \
             patch.object(llm_curate, "llm_suggest_adjacent_skills", _noop), \
             patch.object(llm_curate, "llm_merge_duplicates", _pass):
            out = asyncio.run(two_pass.run_two_pass_extraction(profile))

        p = out.preferences
        # user-typed preference boxes must remain empty; extraction fills the
        # PROFILE (cv_data), never these.
        assert p.additional_skills == [], p.additional_skills
        assert p.target_job_titles == [], p.target_job_titles
        assert p.preferred_locations == []
        assert p.industries == []
