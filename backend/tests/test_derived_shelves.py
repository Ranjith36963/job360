"""Two shelves are DERIVED VIEWS of other shelves. Pin them so they stay that way.

The shelf audit found four duplicate pairs. Two were genuine defects and were
fixed by deleting a copy (``skill_entry`` duplicated ``skill_tiering``;
``industries`` was declared on both dataclasses). These two are different: they
are stored derivations with real consumers, and they cause no wrong behaviour.

    github_skills_inferred  == github_languages (by bytes) + github_topics
    preferred_workplace     == work_arrangement, lower-cased

Deleting either is the wrong trade. ``github_skills_inferred`` has genuine
ASSIGNMENT writers (``github_enricher`` and the clear route), so making it a
read-only property would break them — and ``two_pass`` mutates it in place, which
a property would silently swallow. ``preferred_workplace`` is what the scoring
dimension reads, with no frontend control of its own.

So the risk worth guarding is not the duplication. It is DIVERGENCE: a derived
field that quietly stops matching its source becomes a third, wrong truth that
nothing can detect — the exact failure mode behind every other bug in this batch.
These tests make divergence loud.
"""
from __future__ import annotations

import pytest

from src.services.profile.github_enricher import _infer_skills


class TestGithubSkillsInferredIsPurelyDerived:
    """It must hold NOTHING that ``github_languages`` + ``github_topics`` do not
    already hold. The moment it does, it is a third source of truth and the
    profile can disagree with itself."""

    def test_it_is_exactly_languages_then_topics(self) -> None:
        langs = {"Python": 900, "Rust": 100, "Shell": 5}
        topics = {"machine-learning", "rag"}
        got = _infer_skills(langs, topics)
        assert got == ["Python", "Rust", "Shell", "machine learning", "rag"], (
            "the derivation changed shape — anything it now adds is data that "
            "exists in no other shelf, which makes this an independent fact "
            "rather than a view"
        )

    def test_languages_are_ordered_by_bytes(self) -> None:
        assert _infer_skills({"A": 1, "B": 50, "C": 10}, set())[0] == "B"

    def test_it_invents_nothing_when_both_sources_are_empty(self) -> None:
        assert _infer_skills({}, set()) == []

    def test_every_entry_traces_back_to_a_source(self) -> None:
        """The general form of the rule, not a fixed expected list: a future
        change may reorder or reformat, but must never introduce a term that
        neither source contains."""
        langs = {"Python": 10, "Go": 5}
        topics = {"data-engineering"}
        derived = {s.lower() for s in _infer_skills(langs, topics)}
        allowed = {k.lower() for k in langs} | {t.replace("-", " ") for t in topics}
        assert derived <= allowed, f"invented: {sorted(derived - allowed)}"


class TestPreferredWorkplaceMirrorsWorkArrangement:
    """``preferred_workplace`` is the enum form of ``work_arrangement``, bridged
    in the profile route because the scoring dimension reads the former while the
    form writes the latter. Rule #29 makes the empty case the important one: an
    unstated arrangement must NOT become a stated workplace preference."""

    def _bridge(self, arrangement: str):
        from src.services.profile.models import CVData, UserPreferences
        from src.services.profile.preferences import sanitize_preferences

        prefs = UserPreferences(work_arrangement=arrangement)
        return sanitize_preferences(prefs, CVData())

    @pytest.mark.parametrize("value", ["remote", "hybrid", "onsite"])
    def test_a_stated_arrangement_agrees_with_the_workplace(self, value: str) -> None:
        out = self._bridge(value)
        if out.preferred_workplace is not None:
            assert out.preferred_workplace == out.work_arrangement.lower(), (
                "the two have diverged — the scorer and the prefilter would now "
                "act on different answers to the same question"
            )

    def test_an_unstated_arrangement_never_becomes_a_preference(self) -> None:
        """Rule #29 at its sharpest: silence must stay silence. A default written
        here is indistinguishable from a choice the user made."""
        out = self._bridge("")
        assert not out.work_arrangement
        assert out.preferred_workplace in (None, ""), (
            "an empty work_arrangement produced a workplace preference — that is "
            "a fake constraint the user never stated"
        )
