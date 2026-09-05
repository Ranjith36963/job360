"""Three data-loss bugs found by an adversarial hunt on 2026-08-08.

All three were LIVE, affected every user, produced no error and no log, and
passed the entire existing suite. They share one shape: a SUCCESS path that
quietly destroys user data. Nothing crashed; the data simply stopped existing.
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from src.services.profile.models import CVData, UserPreferences, UserProfile

_LI_FIELDS = ("linkedin_positions", "linkedin_languages", "linkedin_projects",
              "linkedin_volunteer", "linkedin_courses",
              # Added 2026-08-12. Skills have the same two-pass shape as the
              # five above and the same cache-hit failure, but the assignment
              # sat one line OUTSIDE the llm_ran gate the original fix added —
              # so a real profile's 13 LinkedIn skills collapsed to the 3 in the
              # deterministic sidebar on any unrelated profile edit. The tuple
              # not covering it is why the original fix looked complete.
              "linkedin_skills")


async def _noop(*a, **kw):
    return []


async def _passthrough(items, *a, **kw):
    return items


async def _no_cv(*a, **kw):
    return None


async def _drive(profile, li_fn):
    """Run the REAL orchestrator with only the network/LLM edges stubbed.

    Deliberately not a hand-rolled replica of two_pass's calls: the original
    proof script WAS such a replica, and once the fix landed inside two_pass
    the replica kept reporting the old result. A stale replica is the same
    trap as a fixture describing an API that no longer exists.
    """
    from src.services.profile import llm_curate, two_pass

    with patch.object(two_pass, "llm_linkedin_fields", li_fn), \
         patch.object(two_pass, "llm_cv_fields_from_text", _no_cv), \
         patch.object(two_pass, "llm_infer_github_skills", _noop), \
         patch.object(two_pass, "llm_infer_from_about_me", _noop), \
         patch.object(llm_curate, "llm_suggest_adjacent_skills", _noop), \
         patch.object(llm_curate, "llm_merge_duplicates", _passthrough):
        return await two_pass.run_two_pass_extraction(profile)


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
        import dataclasses

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


class TestLinkedInSectionsSurviveACacheHit:
    """The five LinkedIn sections are LLM-ONLY output. The cost cache correctly
    SKIPS the LinkedIn LLM pass when the raw text is unchanged — but the merge
    still ran and overwrote all five with the empty lists a deterministic-only
    merge produces. Upload LinkedIn, then touch anything else on the profile,
    and they were gone permanently: nothing short of uploading a DIFFERENT PDF
    brought them back.
    """

    def test_cache_hit_does_not_wipe(self) -> None:
        payload = {
            "positions": [{"title": "Engineer", "company": "Acme"}],
            "languages": [{"language": "English"}],
            "projects": [{"title": "Thing"}],
            "volunteer": [{"role": "Mentor"}],
            "courses": [{"title": "K8s"}],
            # Skills come from BOTH LinkedIn passes, so they belong in this
            # cache-hit test exactly like the five sections above. They were
            # absent from this payload, which is why the assignment sitting
            # outside the llm_ran gate went unnoticed for four days.
            "skills": ["LangGraph", "Multi-agent Systems"],
        }

        async def ran(*a, **kw):
            return dict(payload)

        async def skipped(*a, **kw):
            return None

        profile = UserProfile(
            cv_data=CVData(linkedin_raw_text="Some LinkedIn text"),
            preferences=UserPreferences(),
        )
        after1 = asyncio.run(_drive(profile, ran))
        assert all(getattr(after1.cv_data, f) for f in _LI_FIELDS), "setup failed"

        after2 = asyncio.run(_drive(after1, skipped))
        for f in _LI_FIELDS:
            assert getattr(after2.cv_data, f), f"{f} was wiped on the cache-hit re-run"

    def test_a_real_reparse_still_replaces(self) -> None:
        """The original overwrite existed for a reason: LinkedIn is the
        canonical source, so removing a section there should remove it here.
        The fix must preserve that — only the SKIPPED case is protected."""
        async def ran_full(*a, **kw):
            return {"positions": [{"title": "Engineer", "company": "Acme"}]}

        async def ran_empty(*a, **kw):
            return {"positions": []}

        profile = UserProfile(
            cv_data=CVData(linkedin_raw_text="text v1"),
            preferences=UserPreferences(),
        )
        p1 = asyncio.run(_drive(profile, ran_full))
        assert p1.cv_data.linkedin_positions

        p1.cv_data.linkedin_raw_text = "text v2 — section removed"
        p2 = asyncio.run(_drive(p1, ran_empty))
        assert p2.cv_data.linkedin_positions == [], (
            "a genuine re-parse must still be able to clear a removed section"
        )


class TestEveryLinkedInShelfSurvivesTheMerge:
    """The same data-loss shape as the class above, one shelf-generation later.

    ``merge_linkedin_fields`` hand-lists the keys it forwards. On 2026-08-09
    eight new LinkedIn shelves shipped — honors, publications, patents,
    organizations, test_scores, recommendations, interests and contact — with
    extractors, prompts, storage, an API field and a rendered UI section each.
    The merger was not updated, so it forwarded twelve keys and dropped those
    eight. ``enrich_cv_from_linkedin`` then does
    ``cv.linkedin_honors = linkedin_data.get("honors", [])`` against a dict that
    never had the key, so the shelves were not merely unfilled — they were
    ASSIGNED EMPTY on every extraction.

    Every layer was verified in isolation and the feature was reported working;
    a live profile even showed a populated contact block, because it had been
    backfilled by a one-off script that bypassed the merge. The pipeline was
    never once exercised end to end.

    So this test is deliberately NOT another hand-listed tuple — that is the
    construct that failed. It reads the shelves off the dataclass, so a shelf
    added tomorrow is covered the moment it is declared.
    """

    # Shelves whose value does not travel as its own key through this merge.
    # Each needs a reason; "hard to test" is not one.
    _NOT_A_MERGE_KEY = {
        "linkedin_raw_text": "the raw document, carried as raw_text",
        "linkedin_industry": "deterministic header field, not an LLM section",
        "linkedin_skills": "unioned from both passes, covered by its own tests",
        "linkedin_filename": "upload receipt, stamped by the API route",
        "linkedin_uploaded_at": "upload receipt, stamped by the API route",
    }

    def _section_shelves(self) -> list[str]:
        import dataclasses

        return [
            f.name
            for f in dataclasses.fields(CVData)
            if f.name.startswith("linkedin_") and f.name not in self._NOT_A_MERGE_KEY
        ]

    def test_every_linkedin_section_reaches_the_shelf(self) -> None:
        shelves = self._section_shelves()
        assert len(shelves) >= 12, "sanity: the LinkedIn shelves should not vanish"

        # Payload keys are the shelf names minus the prefix — the naming
        # contract the merger is supposed to honour.
        payload: dict = {}
        for shelf in shelves:
            key = shelf[len("linkedin_"):]
            payload[key] = (
                {"email": "probe@example.com"} if key == "contact" else [{"probe": key}]
            )

        async def ran(*a, **kw):
            return dict(payload)

        profile = UserProfile(
            cv_data=CVData(linkedin_raw_text="Some LinkedIn text"),
            preferences=UserPreferences(),
        )
        after = asyncio.run(_drive(profile, ran))

        dropped = [s for s in shelves if not getattr(after.cv_data, s)]
        assert not dropped, (
            "These LinkedIn shelves were produced by the pass and then dropped "
            f"before reaching CVData: {dropped}. merge_linkedin_fields forwards a "
            "hand-listed set of keys; anything missing from that list is silently "
            "discarded and then overwritten with an empty value."
        )


class TestTheExtractorVersionTracksThePrompts:
    """A cost cache keyed on the INPUT hides a change to the EXTRACTOR.

    ``_input_hash`` folds ``EXTRACTOR_VERSION`` into the hash precisely so that
    improving a prompt re-reads inputs that have not changed. Shipping seven new
    LinkedIn section prompts WITHOUT bumping it meant every existing user's
    LinkedIn hash still matched, the LLM pass was skipped as a cache hit, and the
    new sections could never populate for anyone who had already uploaded —
    which is every current user.
    """

    def test_bumping_the_version_invalidates_a_stored_hash(self) -> None:
        from src.services.profile import two_pass

        raw = "same unchanged LinkedIn text"
        before = two_pass._input_hash(raw)

        original = two_pass.EXTRACTOR_VERSION
        try:
            two_pass.EXTRACTOR_VERSION = f"{original}-next"
            after = two_pass._input_hash(raw)
        finally:
            two_pass.EXTRACTOR_VERSION = original

        assert before != after, (
            "EXTRACTOR_VERSION is not reaching the hash, so a prompt change can "
            "never re-read an unchanged input."
        )

    def test_the_version_is_ahead_of_the_linkedin_section_prompts(self) -> None:
        """Pins the specific miss: the version at the time the seven section
        prompts landed was "2". Any later prompt change must move it again."""
        from src.services.profile.two_pass import EXTRACTOR_VERSION

        assert EXTRACTOR_VERSION != "2", (
            "EXTRACTOR_VERSION is still '2', the value in force before the "
            "LinkedIn section prompts shipped — existing users will keep hitting "
            "the cache and never receive them."
        )


class TestCertificationsAcceptBothShapes:
    """An LLM returns this section as EITHER objects or bare strings — both are
    reasonable readings of "certifications". The object-only assumption raised
    AttributeError and aborted the WHOLE LinkedIn merge, so one loosely-shaped
    section could cost a user every LinkedIn field. Found while verifying the
    fix above, not by the hunt that found the other two.
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
