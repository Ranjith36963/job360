"""The LLM de-duplication pass must MERGE, never DELETE.

`llm_merge_duplicates` rewrites three shelves the matching engines read —
`education`, `job_titles` and `certifications` — and it is the only step in the
whole extractor that can make a list shorter. Until 2026-08-12 its only guards
were "the model returned something" and "it is not LONGER than the input", so
ANY shorter answer was accepted and written to the profile.

That is a live failure mode, not a hypothetical: the provider chain falls back
to weaker models under rate limiting, and a lazy answer that returns one item
from seven collapsed six real certifications into one, permanently, with no
error and nothing comparing the result against what was stored.

Every existing test of `run_two_pass_extraction` patches this function out with
a passthrough, so the code path that rewrites three matching shelves had no
coverage at all. These tests exercise it directly.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from src.services.profile.llm_curate import _merge_covers_input, llm_merge_duplicates


def _reply(items):
    async def _fake(prompt, system=None):
        return {"items": list(items)}

    return _fake


class TestCoverageRule:
    """The predicate on its own — merging rewrites wording, so the rule has to
    be generous about phrasing and strict about disappearance."""

    def test_a_genuine_reword_is_covered(self) -> None:
        before = [
            "Master of Science - Artificial Intelligence and Robotics - University of Hertfordshire",
            "Master's degree, Artificial Intelligence and Robotics - University of Hertfordshire",
        ]
        after = ["MSc Artificial Intelligence and Robotics, University of Hertfordshire"]
        assert _merge_covers_input(before, after) is True

    def test_a_vanished_qualification_is_not_covered(self) -> None:
        # The BEng is a DIFFERENT degree at a DIFFERENT university. Nothing in
        # the output resembles it, so this is deletion wearing a merge's clothes.
        before = [
            "Master of Science - Artificial Intelligence - University of Hertfordshire",
            "Bachelor of Engineering - Computer Science - Visvesvaraya Technological University",
        ]
        after = ["MSc Artificial Intelligence, University of Hertfordshire"]
        assert _merge_covers_input(before, after) is False

    def test_the_collapse_to_one_is_rejected(self) -> None:
        before = [
            "AWS Certified AI Practitioner",
            "Accenture Data Analytics Job Simulation",
            "Cognizant Artificial Intelligence Job Simulation",
            "The Complete Python Bootcamp",
            "Kaggle Certifications: Python, Pandas",
            "Building with the Claude API",
            "AI Fluency: Framework and Foundations",
        ]
        assert _merge_covers_input(before, ["AWS Certified AI Practitioner"]) is False


class TestTheGuardActuallyFires:
    """End to end through the real function, with only the provider stubbed."""

    @pytest.mark.asyncio
    async def test_a_deleting_merge_is_refused_and_the_original_kept(self) -> None:
        items = [
            "Master of Science - Artificial Intelligence - University of Hertfordshire",
            "Bachelor of Engineering - Computer Science - Visvesvaraya Technological University",
        ]
        with patch(
            "src.services.profile.llm_provider.llm_extract",
            _reply(["MSc Artificial Intelligence, University of Hertfordshire"]),
        ):
            out = await llm_merge_duplicates(items, "education")
        assert out == items, (
            "a merge that dropped a degree nothing else mentions was accepted — "
            "this is the shape that silently deleted real qualifications"
        )

    @pytest.mark.asyncio
    async def test_a_real_duplicate_still_merges(self) -> None:
        """The guard must not turn the de-duplicator off: that would leave the
        profile showing the same degree twice, which is the problem this
        function exists to solve."""
        items = [
            "AI/ML Engineer Intern",
            "AI / ML intern",
        ]
        with patch(
            "src.services.profile.llm_provider.llm_extract",
            _reply(["AI/ML Engineer Intern"]),
        ):
            out = await llm_merge_duplicates(items, "roles")
        assert out == ["AI/ML Engineer Intern"]

    @pytest.mark.asyncio
    async def test_an_empty_answer_keeps_the_original(self) -> None:
        with patch("src.services.profile.llm_provider.llm_extract", _reply([])):
            out = await llm_merge_duplicates(["A degree", "B degree"], "education")
        assert out == ["A degree", "B degree"]

    @pytest.mark.asyncio
    async def test_a_longer_answer_keeps_the_original(self) -> None:
        with patch(
            "src.services.profile.llm_provider.llm_extract",
            _reply(["A degree", "B degree", "C invented"]),
        ):
            out = await llm_merge_duplicates(["A degree", "B degree"], "education")
        assert out == ["A degree", "B degree"]


class TestSharedListsSurviveACvSwap:
    """`education`, `certifications` and `job_titles` are written by BOTH the CV
    pass and the LinkedIn pass — LinkedIn has no shelf of its own for them.

    `reset_cv_owned_fields` clears all three. It used to drop only the "cv"
    cache key, so the LinkedIn pass was a cache hit on the re-run, contributed
    an empty list, and LinkedIn's degrees and certifications were gone for good
    — unrecoverable by re-uploading the same PDF, because its text is unchanged
    so the hash still matches.
    """

    def test_reset_invalidates_the_linkedin_hash_too(self) -> None:
        from src.services.profile.models import CVData
        from src.services.profile.two_pass import reset_cv_owned_fields

        cv = CVData(
            raw_text="old cv",
            education=["MSc from the CV", "BEng from LinkedIn"],
            llm_input_hashes={
                "cv": "aaa", "linkedin": "bbb", "github": "ccc", "about_me": "ddd",
            },
        )
        reset_cv_owned_fields(cv)

        assert "cv" not in cv.llm_input_hashes
        assert "linkedin" not in cv.llm_input_hashes, (
            "the LinkedIn hash survived a CV reset, so the LinkedIn pass will be "
            "skipped on the re-run and its half of education/certifications/"
            "job_titles is lost permanently"
        )
        # The passes that own their OWN shelves must still be spared — wiping
        # those would be the opposite failure.
        assert cv.llm_input_hashes.get("github") == "ccc"
        assert cv.llm_input_hashes.get("about_me") == "ddd"
