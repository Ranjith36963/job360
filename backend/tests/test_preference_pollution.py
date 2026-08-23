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


class TestDropsDuplicatesOfExtractedShelves:
    """THE live-bug fix. A skill already in an extracted shelf (CV/LinkedIn/
    GitHub) must NOT also sit in "Added by you" — it is a duplicate, and dropping
    it is zero-loss because it still shows under its source shelf."""

    def test_cv_skills_are_dropped_as_duplicates(self) -> None:
        # Python + PyTorch are in cv.skills -> must leave the "Added by you" box.
        prefs = UserPreferences(additional_skills=["Python", "PyTorch"])
        assert sanitize_preferences(prefs, _cv()).additional_skills == []

    def test_linkedin_and_github_skills_are_dropped_as_duplicates(self) -> None:
        cv = CVData(
            skills=["Python"],
            linkedin_skills=["Kubernetes"],
            github_llm_skills=["Terraform"],
            github_skills_inferred=["Ansible"],
        )
        prefs = UserPreferences(additional_skills=[
            "Kubernetes", "Terraform", "Ansible", "Redis",  # Redis is genuinely new
        ])
        out = sanitize_preferences(prefs, cv)
        assert out.additional_skills == ["Redis"]

    def test_case_and_whitespace_insensitive(self) -> None:
        prefs = UserPreferences(additional_skills=["  python ", "PYTORCH"])
        assert sanitize_preferences(prefs, _cv()).additional_skills == []

    def test_intra_box_duplicates_collapse(self) -> None:
        prefs = UserPreferences(additional_skills=["Redis", "redis", "Kafka"])
        assert sanitize_preferences(prefs, _cv()).additional_skills == ["Redis", "Kafka"]


class TestSanitizerIsZeroLoss:
    """It must drop the pollution and KEEP every GENUINE addition — a skill the
    user typed that is NOT already extracted, even if it appears inside a bullet."""

    def test_genuine_additions_survive(self) -> None:
        # None of these are in cv.skills/linkedin/github -> all kept.
        prefs = UserPreferences(additional_skills=[
            "Kubernetes", "Rust", "Terraform", "GraphQL",
        ])
        out = sanitize_preferences(prefs, _cv())
        assert out.additional_skills == ["Kubernetes", "Rust", "Terraform", "GraphQL"]

    def test_skill_named_in_a_bullet_but_not_extracted_survives(self) -> None:
        # TensorFlow appears in a cv_positions bullet but is NOT in cv.skills.
        # Substring matching would wrongly eat it; exact-bullet matching keeps it.
        prefs = UserPreferences(additional_skills=["TensorFlow"])
        out = sanitize_preferences(prefs, _cv())
        assert out.additional_skills == ["TensorFlow"]

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

    def test_target_titles_keep_user_choices_drop_only_junk(self) -> None:
        # A target equal to a PAST role is a legitimate choice (want the same
        # level again) — KEEP it. Only structural junk (a date range) is dropped.
        prefs = UserPreferences(
            target_job_titles=[
                "ML Engineer Intern",          # == a past role — still a valid target
                "Staff ML Engineer",           # a fresh target
                "October 2024 – January 2025", # a date range — junk
            ]
        )
        out = sanitize_preferences(prefs, _cv())
        assert out.target_job_titles == ["ML Engineer Intern", "Staff ML Engineer"]

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


class TestAnOfferedSuggestionSurvivesBeingAccepted:
    """The suggester and the sanitizer must agree on "skills the user has".

    They did not. Each hand-maintained its own list and they drifted:

        suggester (two_pass) : skills, linkedin_skills, github_llm_skills,
                               github_languages, additional_skills
        sanitizer (this file): skills, linkedin_skills, github_llm_skills,
                               github_skills_inferred, cv_skills_esco

    ``github_skills_inferred`` is languages PLUS repo topics, so a topic-derived
    skill was invisible to the suggester and visible to the sanitizer. That gap
    becomes a user-visible defect the moment suggestions are tappable: the chip
    is offered, accepted, written to ``additional_skills`` -- and stripped on the
    very next save. It disappears with nothing on screen to explain it, which
    reads as the app losing the user's input.

    Measured before the fix: accepting "rag" left additional_skills == [].
    Both sides now derive from ``skills_already_held``.
    """

    def _cv_with_a_topic_skill(self) -> CVData:
        return CVData(
            skills=["Python"],
            github_topics=["rag"],
            github_skills_inferred=["Python", "rag"],
        )

    def test_a_topic_derived_skill_is_never_offered(self) -> None:
        """The fix at its source: the suggester's "already have" list must
        include the topic-derived shelf, so "rag" is filtered out BEFORE it can
        ever be shown as a suggestion."""
        from src.services.profile.preferences import skills_already_held

        held = {s.casefold() for s in skills_already_held(self._cv_with_a_topic_skill())}
        assert "rag" in held, (
            "the suggester's view of what the user already has still omits "
            "github_skills_inferred, so it can offer a skill the sanitizer "
            "will immediately strip"
        )

    def test_the_two_lists_cannot_drift_again(self) -> None:
        """The STRUCTURAL guard, and the one that actually matters. Pinning the
        symptom would let a sixth shelf be added to one list and not the other
        tomorrow. This asserts the RELATIONSHIP instead: everything the
        sanitizer strips as "already extracted" must be something the suggester
        knows the user has."""
        from src.services.profile.preferences import (
            _extracted_skills_index,
            skills_already_held,
        )

        cv = CVData(
            skills=["Python"],
            linkedin_skills=["Kubernetes"],
            github_llm_skills=["Terraform"],
            github_skills_inferred=["rag", "Go"],
            cv_skills_esco=["data engineering"],
        )
        stripped = _extracted_skills_index(cv)
        offered_against = {s.casefold() for s in skills_already_held(cv)}
        missing = sorted(stripped - offered_against)
        assert not missing, (
            f"the sanitizer strips {missing} but the suggester does not know "
            "the user has them, so each is a chip that can be offered, "
            "accepted, and then silently deleted"
        )

    def test_an_accepted_suggestion_survives_the_next_save(self) -> None:
        """The outcome, end to end. A skill the suggester WOULD offer (because
        the user genuinely lacks it) must still be there after the save that
        follows accepting it."""
        from src.services.profile.preferences import skills_already_held

        cv = self._cv_with_a_topic_skill()
        held = {s.casefold() for s in skills_already_held(cv)}
        assert "kubernetes" not in held, "premise broken: pick a skill the user lacks"

        accepted = UserPreferences(additional_skills=["Kubernetes"])
        out = sanitize_preferences(accepted, cv)
        assert out.additional_skills == ["Kubernetes"], (
            "a legitimately-offered suggestion was stripped on save -- accepting "
            "a chip would appear to do nothing"
        )

    def test_the_prompt_keeps_the_original_spelling(self) -> None:
        """``skills_already_held`` feeds the suggestion PROMPT as well as the
        filter. Lower-casing it would quietly make the prompt worse
        ("pytorch, aws") without failing anything."""
        from src.services.profile.preferences import skills_already_held

        cv = CVData(skills=["PyTorch", "AWS"], linkedin_skills=["SQL"])
        assert skills_already_held(cv) == ["PyTorch", "AWS", "SQL"]
