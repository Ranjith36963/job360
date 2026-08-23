"""Two shelves are DERIVED VIEWS of other shelves. Pin them so they stay that way.

    github_skills_inferred  == github_languages (by bytes) + github_topics
    preferred_workplace     == work_arrangement, as a strict enum

The risk is not the duplication. It is DIVERGENCE: a derived field that quietly
stops matching its source becomes a third, wrong truth that nothing can detect —
the failure mode behind most of this batch.

The two are guarded DIFFERENTLY, on purpose, because only one of them could be
collapsed:

``github_skills_inferred`` stays a stored field. It has genuine ASSIGNMENT
writers (``github_enricher`` and the clear route) that a read-only property would
break, and ``two_pass`` mutates it in place, which a property would silently
swallow. So divergence here is possible and the tests below have to catch it.

``preferred_workplace`` was collapsed into a read-only property (2026-08-13).
It had no writer of its own — the profile route bridged it from
``work_arrangement`` on every save — so nothing was lost by deleting the storage,
and divergence became structurally impossible instead of merely tested. Verified
against production before shipping: 3 live profiles and 20 history versions, zero
rows holding an answer only under the old key. What its tests guard now is the
TRANSLATION, including the "any" sentinel that was scoring users a live penalty.
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


class TestPreferredWorkplaceIsDerivedNotStored:
    """``preferred_workplace`` is no longer a field. It is a read-only property
    computed from ``work_arrangement``, so the two CANNOT diverge — divergence is
    now a structural impossibility rather than something a test has to catch.

    What this class guards instead is the TRANSLATION, which is still a real
    decision with a real bug history:

      * only the three enum values the scorer understands may pass through;
      * everything else — including the form's own ``"any"`` default — must come
        out as None, i.e. as SILENCE, not as a stated preference.

    The old test asserted the mirror conditionally (``if x is not None``), which
    could pass while saying nothing. This one has no escape hatch.
    """

    def _prefs(self, arrangement: str):
        from src.services.profile.models import UserPreferences

        return UserPreferences(work_arrangement=arrangement)

    def test_it_is_a_property_not_a_stored_field(self) -> None:
        """The whole point of the collapse: there is exactly ONE place the
        answer lives. A field here would let a writer set it independently and
        re-open the divergence this class used to police."""
        from dataclasses import fields

        from src.services.profile.models import UserPreferences

        names = {f.name for f in fields(UserPreferences)}
        assert "preferred_workplace" not in names, (
            "preferred_workplace is a stored field again — it can now hold an "
            "answer that contradicts work_arrangement, with no way to tell which "
            "one the user actually chose"
        )
        assert isinstance(
            getattr(UserPreferences, "preferred_workplace", None), property
        )

    @pytest.mark.parametrize("value", ["remote", "hybrid", "onsite"])
    def test_a_stated_arrangement_passes_through(self, value: str) -> None:
        assert self._prefs(value).preferred_workplace == value

    @pytest.mark.parametrize(
        "raw,expected",
        [("Remote", "remote"), ("  ONSITE  ", "onsite"), ("Hybrid", "hybrid")],
    )
    def test_case_and_padding_are_normalised(self, raw: str, expected: str) -> None:
        """The scorer compares lower-case strings. A stored "Remote" used to
        score as a non-match against its own value."""
        assert self._prefs(raw).preferred_workplace == expected

    @pytest.mark.parametrize("value", ["", "   ", "any", "Any", "flexible", "n/a"])
    def test_anything_not_in_the_enum_is_silence(self, value: str) -> None:
        """THE BUG THIS FIXES, and it was live.

        The preferences form seeds the select at ``"any"``. That is the user
        saying "I don't mind" — but ``workplace_score`` only branches on the
        three enum values, so ``"any"`` fell past every branch and scored **0**,
        while an EMPTY preference returns the neutral **3**. Choosing "I don't
        mind" therefore scored worse, on every single job, than never answering.

        Rule #29: an unstated preference must be silence, never a penalty.
        """
        assert self._prefs(value).preferred_workplace is None

    def test_a_dont_mind_answer_scores_the_same_as_no_answer(self) -> None:
        """The rule #29 violation stated as the outcome the user FELT, not as
        the mechanism.

        A real ``JobEnrichment`` on purpose. The obvious way to write this — a
        ``{"workplace_type": "onsite"}`` dict — passes without proving anything,
        because both branches return the neutral score before ever touching the
        enrichment, so the wrong type is never noticed. That is the same
        vacuous-pass shape this file replaced in the old test; a green tick from
        a test that cannot fail is worse than no test.
        """
        from src.services.job_enrichment_schema import (
            JobCategory,
            JobEnrichment,
            WorkplaceType,
        )
        from src.services.scoring_dimensions import workplace_score

        onsite_job = JobEnrichment(
            title_canonical="X",
            category=JobCategory.SOFTWARE_ENGINEERING,
            workplace_type=WorkplaceType.ONSITE,
        )
        silent = workplace_score(onsite_job, self._prefs("").preferred_workplace)
        dont_mind = workplace_score(onsite_job, self._prefs("any").preferred_workplace)
        stated = workplace_score(onsite_job, self._prefs("remote").preferred_workplace)

        # The control: a STATED, opposite preference must still score lower.
        # Without this the test would also pass if the scorer stopped
        # discriminating at all.
        assert stated < silent, (
            "a remote preference no longer costs anything on an onsite job — "
            "the dimension has gone flat and the assertion below is meaningless"
        )
        assert dont_mind == silent, (
            f'"any" scored {dont_mind} but silence scored {silent} — saying '
            f'"I don\'t mind" is punished relative to saying nothing. Before the '
            f"collapse this was live: the raw \"any\" reached the scorer, matched "
            f"no branch, and fell through to 0 on every job."
        )
