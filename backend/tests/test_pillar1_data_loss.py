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
              "linkedin_volunteer", "linkedin_courses")


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

        prefs = UserPreferences(needs_visa=True, preferred_workplace="remote",
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
            needs_visa=True, preferred_workplace="hybrid", salary_min=45000,
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


class TestWorkplaceBridge:
    """The WORKPLACE scoring dimension (weight 6) reads
    `UserPreferences.preferred_workplace`, but the preferences form only ever
    sent `work_arrangement`, and no control for `preferred_workplace` exists
    anywhere in the frontend. The model documents preferred_workplace as "the
    enum form of work_arrangement" — but nothing built that bridge, so the
    field the scorer reads stayed None for every user and the whole dimension
    was permanently dead. That was the workplace "dead pair" the shelf X-ray
    flagged; the cause was here, not users failing to fill anything.
    """

    def _apply(self, form: dict):
        import json

        from src.api.routes.profile import _apply_preferences
        from src.services.profile.models import CVData, UserPreferences, UserProfile

        p = UserProfile(cv_data=CVData(), preferences=UserPreferences())
        _apply_preferences(json.dumps(form), p)
        return p.preferences

    def test_work_arrangement_populates_preferred_workplace(self) -> None:
        for value in ("remote", "hybrid", "onsite"):
            prefs = self._apply({"work_arrangement": value})
            assert prefs.preferred_workplace == value, (
                f"{value}: the scorer's field was not bridged from the form"
            )

    def test_empty_stays_none_not_a_fake_preference(self) -> None:
        """Rule #29: an unfilled preference must be 'don't care' (None ->
        neutral score), never a manufactured value."""
        assert self._apply({"work_arrangement": ""}).preferred_workplace is None
        assert self._apply({}).preferred_workplace is None


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
