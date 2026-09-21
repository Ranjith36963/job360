"""Three data-loss bugs found by an adversarial hunt on 2026-08-08.

All three were LIVE, affected every user, produced no error and no log, and
passed the entire existing suite. They share one shape: a SUCCESS path that
quietly destroys user data. Nothing crashed; the data simply stopped existing.

DECISION 28 (2026-09-21) rewrite. Two of the three original bugs lived
inside an LLM cost cache: the cache correctly skipped re-billing an
unchanged LinkedIn input, but the merge that followed still ran and
overwrote LinkedIn's LLM-only sections with the empty lists a
deterministic-only merge produces. That whole mechanism --
``llm_linkedin_fields``, the ``llm_ran`` flag, ``EXTRACTOR_VERSION``,
``_input_hash`` -- is gone. Job360 has no LLM pass left to cache the cost
of, so there is nothing left to skip.

The FEAR those tests protected against is not gone -- it is sharper. The
LinkedIn prose sections are now written by the USER'S OWN AGENT through
``update_profile``, and a re-extraction (triggered by ANY other profile
edit) must never silently wipe what the agent put there. The tests below
assert that promise directly against the real orchestrator
(``two_pass.run_two_pass_extraction``), which is deterministic end to end
now, so there are no LLM edges left to stub out.
"""
from __future__ import annotations

import asyncio
import copy
import dataclasses
from typing import Any

import pytest

from src.services.profile import two_pass
from src.services.profile.models import CVData, UserPreferences, UserProfile

# The six LinkedIn shelves the original 2026-08-08 hunt found being wiped on
# a cache-hit re-run. Kept as the historical anchor for that specific bug --
# skills sat one line OUTSIDE the fix the first round shipped, which is why
# a real profile's 13 LinkedIn skills once collapsed to the 3 in the
# deterministic sidebar on any unrelated edit.
# TestEveryLinkedInShelfSurvivesReExtraction below covers every shelf,
# hand-listed or not, which is the structural fix for THIS list also going
# stale one day.
_LI_FIELDS = ("linkedin_positions", "linkedin_languages", "linkedin_projects",
              "linkedin_volunteer", "linkedin_courses", "linkedin_skills")


class TestPreferencesSurviveTheMerge:
    """`merge_cv_and_preferences` rebuilt UserPreferences with a hand-listed
    constructor, so every field added to the dataclass afterwards was silently
    dropped — on EVERY save once a user had any extracted data.

    Measured: needs_visa True -> False, preferred_workplace 'remote' -> None.
    A user who ticked "I need visa sponsorship" had it reset before it reached
    the database, so the visa scoring gate went dark for exactly the person who
    asked for it.
    """

    def test_visa_and_workplace_survive(self) -> None:
        from src.services.profile.preferences import merge_cv_and_preferences

        prefs = UserPreferences(needs_visa=True, work_arrangement="remote",
                                target_job_titles=["ML Engineer"])
        out = merge_cv_and_preferences(["Python"], ["ML Engineer"], prefs)
        assert out.needs_visa is True
        assert out.preferred_workplace == "remote"

    def test_every_field_survives(self) -> None:
        """The structural guarantee, not just the two known casualties: a field
        added to UserPreferences tomorrow must pass through untouched. This is
        what `dataclasses.replace` buys over a hand-listed constructor."""
        from src.services.profile.preferences import merge_cv_and_preferences

        prefs = UserPreferences(
            needs_visa=True, work_arrangement="hybrid", salary_min=45000,
            salary_max=90000, experience_level="mid", about_me="hello",
            github_username="someone", preferred_locations=["London"],
            negative_keywords=["sales"], excluded_skills=["php"],
        )
        out = merge_cv_and_preferences(["Python"], ["ML Engineer"], prefs)
        derived = {"target_job_titles", "additional_skills"}
        for f in dataclasses.fields(UserPreferences):
            if f.name in derived:
                continue
            assert getattr(out, f.name) == getattr(prefs, f.name), (
                f"{f.name} was dropped by the merge"
            )


class TestLinkedInSectionsSurviveReExtraction:
    """Historical bug (2026-08-08): the LinkedIn cost cache correctly SKIPPED
    re-billing the LLM pass when the raw text was unchanged — but the merge
    that followed still ran and overwrote all six shelves above with the
    empty lists a deterministic-only pass produces. Upload LinkedIn, then
    touch anything else on the profile, and they were gone permanently:
    nothing short of uploading a DIFFERENT PDF brought them back.

    DECISION 28: there is no cache and no LLM pass to skip any more. The
    promise is now kept by ``enrich_cv_from_linkedin``'s fill-if-present rule
    (see its docstring in linkedin_parser.py) — it only ever ADDS a value it
    actually parsed structurally, and never assigns an empty one over
    something already there. This proves that promise against the real
    orchestrator: shelves the user's agent already filled must survive
    however many times extraction re-runs.
    """

    def test_populated_shelves_survive_a_re_extraction(self) -> None:
        cv = CVData(linkedin_raw_text="Some LinkedIn text")
        for f in _LI_FIELDS:
            setattr(cv, f, ["ProbeSkill"] if f == "linkedin_skills"
                    else [{"probe": f}])
        # Capture the EXACT value, not just "is it truthy" — a truthiness
        # check still passes when a re-extraction overwrites an agent-written
        # value with different non-empty data (CodeRabbit, PR #608). deepcopy
        # because these are mutable lists/dicts the orchestrator mutates in
        # place.
        original = {f: copy.deepcopy(getattr(cv, f)) for f in _LI_FIELDS}
        profile = UserProfile(cv_data=cv, preferences=UserPreferences())

        after1 = asyncio.run(two_pass.run_two_pass_extraction(profile))
        for f in _LI_FIELDS:
            assert getattr(after1.cv_data, f) == original[f], (
                f"{f} was changed (wiped OR overwritten) on the first re-extraction"
            )

        # A second run (e.g. the user edits an unrelated preference and the
        # whole profile is re-read again) must not wipe it either — this is
        # the exact repeat-run shape the original cache-hit bug had.
        after2 = asyncio.run(two_pass.run_two_pass_extraction(after1))
        for f in _LI_FIELDS:
            assert getattr(after2.cv_data, f) == original[f], (
                f"{f} was changed (wiped OR overwritten) on the second re-extraction"
            )


class TestEveryLinkedInShelfSurvivesReExtraction:
    """Same data-loss shape as the class above, one shelf-generation later.

    ``merge_linkedin_fields`` used to hand-list the keys it forwarded. On
    2026-08-09 eight new LinkedIn shelves shipped — honors, publications,
    patents, organizations, test_scores, recommendations, interests and
    contact. The merger was not updated, so it forwarded twelve keys and
    dropped those eight. ``enrich_cv_from_linkedin`` then did
    ``cv.linkedin_honors = linkedin_data.get("honors", [])`` against a dict
    that never had the key, so the shelves were not merely unfilled — they
    were ASSIGNED EMPTY on every extraction.

    DECISION 28: every one of those assignment lines is now fill-if-present
    (linkedin_parser.enrich_cv_from_linkedin), and the merge is built from
    ``_empty_linkedin_data()``, the ONE canonical shape, instead of a literal
    key list. This test is still deliberately NOT a hand-listed tuple — that
    is the construct that failed twice already. It reads the shelves off the
    dataclass, so a shelf added tomorrow is covered the moment it is
    declared.
    """

    def _linkedin_shelves(self) -> list[Any]:
        return [f for f in dataclasses.fields(CVData) if f.name.startswith("linkedin_")]

    @staticmethod
    def _probe_for(field_name: str, default: Any) -> Any:
        if isinstance(default, dict):
            return {"probe": field_name}
        if isinstance(default, list):
            # linkedin_skills / linkedin_interests are list[str]; every other
            # linkedin_ list shelf is list[dict]. This is the shape of the
            # dataclass, not a keyword vocabulary (rule #28) — just the two
            # known str-list fields among the LinkedIn shelves.
            if field_name in ("linkedin_skills", "linkedin_interests"):
                return [f"probe-{field_name}"]
            return [{"probe": field_name}]
        return f"probe-{field_name}"

    def test_every_linkedin_shelf_survives_a_re_extraction(self) -> None:
        shelves = self._linkedin_shelves()
        assert len(shelves) >= 12, "sanity: the LinkedIn shelves should not vanish"

        pristine = CVData()
        cv = CVData()
        for f in shelves:
            setattr(cv, f.name, self._probe_for(f.name, getattr(pristine, f.name)))
        # EXACT value, not truthiness — see the class above for why a
        # truthiness check misses an overwrite with different non-empty data.
        original = {f.name: copy.deepcopy(getattr(cv, f.name)) for f in shelves}

        profile = UserProfile(cv_data=cv, preferences=UserPreferences())
        after = asyncio.run(two_pass.run_two_pass_extraction(profile))

        changed = [
            f.name for f in shelves
            if getattr(after.cv_data, f.name) != original[f.name]
        ]
        assert not changed, (
            "These LinkedIn shelves were pre-populated (as if the user's own "
            f"agent had written them) and then changed by a re-extraction: {changed}. "
            "enrich_cv_from_linkedin must only ever ADD what it actually parsed "
            "structurally — never assign an empty value, or a different "
            "non-empty one, over something already there."
        )


class TestCertificationsAcceptBothShapes:
    """An LLM returns this section as EITHER objects or bare strings — both are
    reasonable readings of "certifications". The object-only assumption raised
    AttributeError and aborted the WHOLE LinkedIn merge, so one loosely-shaped
    section could cost a user every LinkedIn field. Found while verifying the
    fix above, not by the hunt that found the other two.

    Still relevant post decision-28: an agent writing this section back
    through ``update_profile`` can hand either shape too, and the merge must
    stay tolerant of both.
    """

    @pytest.mark.parametrize("certs", [
        ["AWS Solutions Architect"],
        [{"name": "AWS Solutions Architect"}],
        [{"title": "AWS Solutions Architect"}],
    ])
    def test_shape_tolerant(self, certs: list) -> None:
        from src.services.profile.linkedin_parser import enrich_cv_from_linkedin

        cv = enrich_cv_from_linkedin(CVData(), {"certifications": certs})
        assert cv.certifications == ["AWS Solutions Architect"]

    def test_junk_entries_are_skipped_not_fatal(self) -> None:
        from src.services.profile.linkedin_parser import enrich_cv_from_linkedin

        cv = enrich_cv_from_linkedin(CVData(), {"certifications": [None, 42, "Real"]})
        assert cv.certifications == ["Real"]




class TestNeedsVisaRoundTripsThroughTheRoute:
    """needs_visa gates the VISA scoring dimension (weight 6) but had NO UI
    control until 2026-08-08. The frontend form now sends it; this pins the
    BACKEND half — that _apply_preferences stores what the form sends, and that
    a save which omits it (an older client) does not silently wipe a stored
    True (the preferences-wipe class this file's first test covers).
    """

    def _apply(self, form: dict, existing=None):
        import json

        from src.api.routes.profile import _apply_preferences
        from src.services.profile.models import CVData, UserPreferences, UserProfile

        p = UserProfile(
            cv_data=CVData(),
            preferences=existing or UserPreferences(),
        )
        _apply_preferences(json.dumps(form), p)
        return p.preferences

    def test_form_sending_true_is_stored(self) -> None:
        assert self._apply({"needs_visa": True}).needs_visa is True

    def test_form_sending_false_is_stored(self) -> None:
        from src.services.profile.models import UserPreferences

        out = self._apply({"needs_visa": False},
                          existing=UserPreferences(needs_visa=True))
        assert out.needs_visa is False

    def test_a_save_that_omits_it_preserves_the_stored_value(self) -> None:
        """An older client that never sends the key must not wipe a stored True."""
        from src.services.profile.models import UserPreferences

        out = self._apply({"target_job_titles": ["ML"]},
                          existing=UserPreferences(needs_visa=True))
        assert out.needs_visa is True


class TestExperienceLevelIsNotWipedOrPolluted:
    """`experience_level` reached `UserPreferences` via a bare
    `pref_dict.get("experience_level", "")` -- no normalisation, and an
    OMITTED key silently defaulted to "" exactly like an explicit clear.

    Two separate bugs share that one line:

      1. No validation. `resolve_experience_level` says "typed always wins" --
         a typed value skips the CV-inferred fallback and drives
         `seniority_score` at full weight (up to 8 points, either direction) on
         every job. An unrecognised string reaching that seam the same way
         "any" reached the workplace one is exactly the class of bug already
         fixed one field over (`_normalize_work_arrangement`).
      2. No partial-save protection. Because the fallback default was "" and
         not the STORED value, saving anything else on the preferences form
         (salary, locations, about_me...) silently wiped a previously-chosen
         experience level -- the identical shape as the workplace regression
         `TestWorkplaceReachesTheScorer` guards, one field over.

    NOTE on "mid": the live defect proven for THIS field (a brand-new account
    posting "mid" it never chose) is a FRONTEND bug -- `prefsFromRaw` in
    PreferencesForm.tsx substitutes "mid" for a missing `experience_level`,
    fixed in `PreferencesForm.experience.test.tsx`. "mid" is a real,
    selectable option in the dropdown (same list as "entry"/"senior"/"lead"/
    "executive"), so the backend must NOT silence it -- doing so would drop a
    genuine choice for every user who actually picks "Mid", which is a worse
    bug than the one being fixed. This class guards the backend's own half:
    validating genuinely unrecognised strings and protecting partial saves.
    """

    def _apply(self, form: dict, existing=None):
        import json

        from src.api.routes.profile import _apply_preferences
        from src.services.profile.models import CVData, UserPreferences, UserProfile

        p = UserProfile(
            cv_data=CVData(), preferences=existing or UserPreferences()
        )
        _apply_preferences(json.dumps(form), p)
        return p.preferences.experience_level

    def test_each_real_level_round_trips(self) -> None:
        # The control: without it, "normalise everything to empty" would also
        # pass every other test in this class.
        for level in ("entry", "mid", "senior", "lead", "executive"):
            assert self._apply({"experience_level": level}) == level, (
                f"{level} is a real dropdown option and must survive a save"
            )

    def test_an_unrecognised_value_is_silenced(self) -> None:
        # Not a real dropdown option. Before normalisation this reached the
        # DB, the LLM judge prompt and the semantic vector as though the user
        # had stated it -- the same failure `_normalize_work_arrangement`
        # already fixed for "any".
        assert self._apply({"experience_level": "ninja-wizard"}) == ""

    def test_an_omitted_key_keeps_the_stored_answer(self) -> None:
        """The partial-save bug: the old `.get(key, "")` treated an absent
        key the same as an explicit clear, so saving any OTHER field on the
        form silently wiped a previously-chosen experience level."""
        from src.services.profile.models import UserPreferences

        stored = UserPreferences(experience_level="senior")
        assert self._apply({"salary_min": 40000}, stored) == "senior"

    def test_an_explicit_empty_string_still_clears_it(self) -> None:
        """Rule #29 cuts both ways -- a preference the user cleared must go
        back to silence, not keep the old stored value forever."""
        from src.services.profile.models import UserPreferences

        stored = UserPreferences(experience_level="senior")
        assert self._apply({"experience_level": ""}, stored) == ""


class TestGithubUsernameFallbackIsNormalized:
    """github_username reaching the _apply_preferences fallback path was stored
    raw — a real user ended up with 'https:' (2026-08-08), which silently broke
    GitHub enrichment (0 languages/skills, no GitHub bucket). The dedicated
    GitHub route already normalized; this pins the fallback path too."""

    def _apply(self, form: dict, existing_username: str = ""):
        import json

        from src.api.routes.profile import _apply_preferences
        from src.services.profile.models import CVData, UserPreferences, UserProfile

        p = UserProfile(
            cv_data=CVData(),
            preferences=UserPreferences(github_username=existing_username),
        )
        _apply_preferences(json.dumps(form), p)
        return p.preferences.github_username

    def test_a_url_is_reduced_to_a_handle(self) -> None:
        assert self._apply({"github_username": "https://github.com/Ranjith36963"}) == "Ranjith36963"

    def test_a_stored_bad_value_is_scrubbed_on_next_save(self) -> None:
        # 'https:' can never be a valid handle -> normalizes to "" rather than
        # persisting the poison.
        assert self._apply({}, existing_username="https:") == ""

    def test_a_clean_handle_survives(self) -> None:
        assert self._apply({"github_username": "torvalds"}) == "torvalds"
