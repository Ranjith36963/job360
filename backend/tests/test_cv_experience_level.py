"""The CV's own statement of seniority, which we asked for and then binned.

``cv_parser``'s prompt has always instructed the LLM to return
``experience_level`` ("One of: intern, junior, mid, senior, lead, principal,
director — infer from experience duration and roles"), and ``CVSchema`` has
always declared the field. The adapter that turns a ``CVSchema`` into a
``CVData`` simply never passed it on, so the answer was paid for on every CV
parse and dropped on the floor.

That mattered more than one missing field. It left ``experience_level_inferred``
dependent solely on ``seniority.infer_experience_level``, which reads dated job
TITLES — so a CV whose titles carry no seniority word produced no level at all,
and the seniority dimension sat at its neutral fallback for that user.

The LLM read is a FACT about the document, exactly like ``skills``. It is NOT a
preference, so it never gates anything (rule #29, and see
``tests/test_prefilter_wiring.py`` for the gate side of that line).
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

from src.services.profile.models import CVData, UserPreferences, UserProfile
from src.services.profile.schemas import CVSchema, cv_schema_to_cvdata


class TestTheAdapterCarriesTheLevel:
    def test_a_stated_level_reaches_the_shelf(self) -> None:
        schema = CVSchema(name="Ada", experience_level="senior")
        cv = cv_schema_to_cvdata(schema, raw_text="x")
        assert cv.cv_experience_level == "senior", (
            "the CV LLM's seniority read is still being dropped by the adapter"
        )

    def test_an_absent_level_stays_empty_not_guessed(self) -> None:
        cv = cv_schema_to_cvdata(CVSchema(name="Ada"), raw_text="x")
        assert cv.cv_experience_level == ""

    def test_it_is_not_a_preference(self) -> None:
        """A fact read off the document must never land on the preference the
        user types — that is the field a gate is allowed to act on."""
        schema = CVSchema(name="Ada", experience_level="principal")
        cv = cv_schema_to_cvdata(schema, raw_text="x")
        prefs = UserPreferences()
        assert prefs.experience_level == ""
        assert cv.cv_experience_level == "principal"


class TestItFillsTheScoringSeamOnlyWhenTitlesCannot:
    """``infer_experience_level`` reads dated job TITLES. When those carry no
    seniority word it returns "", and before this the seniority dimension went
    dark. The LLM read of the whole document is the better fallback — but it
    must never displace a value the titles DID yield, which is the stronger
    structural signal."""

    def _run(self, cv: CVData, titles_yield: str) -> UserProfile:
        from src.services.profile import llm_curate, two_pass

        async def _noop(*a, **kw):
            return []

        async def _none(*a, **kw):
            return None

        async def _passthrough(items, *a, **kw):
            return items

        profile = UserProfile(cv_data=cv, preferences=UserPreferences())
        with patch.object(two_pass, "llm_cv_fields_from_text", _none), \
             patch.object(two_pass, "llm_linkedin_fields", _none), \
             patch.object(two_pass, "llm_infer_github_skills", _noop), \
             patch.object(two_pass, "llm_infer_from_about_me", _noop), \
             patch.object(two_pass, "infer_experience_level",
                          lambda *a, **kw: titles_yield), \
             patch.object(llm_curate, "llm_suggest_adjacent_skills", _noop), \
             patch.object(llm_curate, "llm_merge_duplicates", _passthrough):
            return asyncio.run(two_pass.run_two_pass_extraction(profile))

    def test_the_llm_level_fills_a_blank_from_the_titles(self) -> None:
        cv = CVData(raw_text="x", skills=["python"], cv_experience_level="senior")
        after = self._run(cv, titles_yield="")
        assert after.preferences.experience_level_inferred == "senior", (
            "titles yielded nothing and the CV's own stated level was ignored — "
            "the seniority dimension stays dark for this user"
        )

    def test_the_titles_win_when_they_have_an_answer(self) -> None:
        cv = CVData(raw_text="x", skills=["python"], cv_experience_level="junior")
        after = self._run(cv, titles_yield="principal")
        assert after.preferences.experience_level_inferred == "principal", (
            "the structural title signal must outrank the LLM's reading"
        )

    def test_neither_source_leaves_it_empty(self) -> None:
        cv = CVData(raw_text="x", skills=["python"])
        after = self._run(cv, titles_yield="")
        assert after.preferences.experience_level_inferred == ""


class TestEveryCvScalarSurvivesTheMerge:
    """The bug the tests above MISSED, caught by production.

    ``_merge_cv_llm_into`` named every scalar individually. Adding
    ``cv_experience_level`` and ``cv_right_to_work`` to the prompt, the schema
    and the adapter was not enough — the merge dropped both one layer above the
    fix, and a live re-extraction produced two empty shelves while every unit
    test passed. The tests passed because they SET the field on the CVData
    directly instead of letting the CV pass produce it, so the merge was never
    exercised.

    This is the third time this function has lost a field that way
    (``career_domain`` was the first). So the assertion is structural: whatever
    the CV pass produces on a CV-owned scalar must reach the profile — no list
    of names to keep in step.
    """

    def _merged(self, produced: CVData) -> CVData:
        from src.services.profile.two_pass import _merge_cv_llm_into

        cv = CVData(raw_text="x")
        _merge_cv_llm_into(cv, produced)
        return cv

    def test_every_cv_owned_scalar_the_pass_produces_is_carried(self) -> None:
        from src.services.profile.two_pass import _cv_owned_scalars

        names = _cv_owned_scalars()
        assert names, "no CV-owned scalars detected — the derivation is broken"

        produced = CVData(**{n: f"value-for-{n}" for n in names})
        merged = self._merged(produced)

        dropped = [n for n in names if not getattr(merged, n)]
        assert not dropped, (
            f"the CV merge dropped these scalars the pass produced: {dropped}. "
            "They will be empty on every live profile no matter how correct the "
            "prompt, schema and adapter are."
        )

    def test_the_two_shelves_production_caught_are_covered(self) -> None:
        from src.services.profile.two_pass import _cv_owned_scalars

        assert "cv_experience_level" in _cv_owned_scalars()
        assert "cv_right_to_work" in _cv_owned_scalars()

    def test_another_passes_scalar_is_never_clobbered_by_a_cv_reparse(self) -> None:
        """The exclusions are load-bearing: a CV re-parse must not overwrite the
        LinkedIn About or a GitHub bio, which the CV pass knows nothing about."""
        from src.services.profile.two_pass import _cv_owned_scalars

        for name in ("linkedin_summary", "github_bio", "github_profile_readme",
                     "raw_text", "cv_filename"):
            assert name not in _cv_owned_scalars(), (
                f"{name} is not the CV pass's to write"
            )

    def test_a_populated_scalar_is_not_overwritten(self) -> None:
        from src.services.profile.two_pass import _merge_cv_llm_into

        cv = CVData(raw_text="x", cv_experience_level="senior")
        _merge_cv_llm_into(cv, CVData(cv_experience_level="junior"))
        assert cv.cv_experience_level == "senior", (
            "fill-if-empty, never overwrite — a re-run that reads differently "
            "must not clobber what the profile already carries"
        )


