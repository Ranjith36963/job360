"""Clearing an input must leave NOTHING of it behind.

THE DEFECT, reproduced on shipped code 2026-08-15 (third occurrence).

Every "what this input owns" list in this project has been hand-maintained, and
every one of them has fallen behind the dataclass:

    2026-08-07  career_domain
    2026-08-10  cv_experience_level, cv_right_to_work, cv_projects
    2026-08-15  cv_positions, linkedin_summary, linkedin_headline

The 2026-08-10 round fixed only the SCALAR half by deriving it from
``dataclasses.fields``. Collections stayed hand-listed, so ``cv_positions`` — a
``list[dict]``, invisible to a scalar-only loop — survived a full profile clear
AND every CV re-upload. ``linkedin_summary`` and ``linkedin_headline`` were
added later, wired into the LLM judge and the semantic vector, and never added
to ``_clear_linkedin`` at all.

Measured before the fix: after clearing everything, exactly three fields still
held data, and ``profile_to_matcher_text`` emitted the previous person's dated
work history and About paragraph into the judge prompt for every job.

WHY THIS TEST IS SHAPED LIKE THIS. The old guard listed ten field names by hand
and asserted each was empty. A test that names fields one at a time cannot catch
a field nobody remembered — it has the identical blind spot as the code it
guards, which is exactly how this recurred three times. So these tests never
name a field to CLEAR. They fill every field on the dataclass programmatically,
run the real clear, and demand the leftovers match a short, individually
justified allowlist.
"""
from __future__ import annotations

import dataclasses
from typing import Any

from src.services.profile.models import CVData, UserPreferences


def _fill_every_field(cv: CVData) -> None:
    """Put a non-empty value in EVERY field, whatever its type.

    Derived from the dataclass, so a shelf added tomorrow is filled — and
    therefore checked — without anyone editing this file.
    """
    for f in dataclasses.fields(CVData):
        current = getattr(cv, f.name)
        value: Any
        if isinstance(current, str):
            value = f"filled-{f.name}"
        elif isinstance(current, bool):
            value = True
        elif isinstance(current, (int, float)):
            value = 1
        elif isinstance(current, dict):
            value = {"filled": f.name}
        elif isinstance(current, list):
            value = [{"filled": f.name}] if "positions" in f.name or "projects" in f.name else [f"filled-{f.name}"]
        elif current is None:
            value = f"filled-{f.name}"
        else:
            continue
        setattr(cv, f.name, value)


def _non_empty(cv: CVData) -> set[str]:
    return {f.name for f in dataclasses.fields(CVData) if getattr(cv, f.name)}


class TestClearingTheCV:
    """``reset_cv_owned_fields`` runs on the Clear button AND on every CV
    re-upload, so a survivor here becomes another person's data presented as
    yours."""

    # Each entry is a field that SHOULD survive, with the reason. Anything not
    # listed must be gone. Keep this list tiny and justified.
    ALLOWED = {
        "raw_text": "the caller overwrites it with the new CV immediately after",
        "cv_filename": "upload receipt — the route stamps the NEW one straight "
                       "after this call. The Clear button uses _clear_cv, which "
                       "does empty it; that path is covered below.",
        "cv_uploaded_at": "same upload receipt",
        "llm_input_hashes": "only the cv/linkedin keys are dropped; github and "
                            "about_me hashes must survive or we re-bill for "
                            "inputs the user never touched",
    }

    def test_nothing_cv_owned_survives(self) -> None:
        from src.services.profile.two_pass import reset_cv_owned_fields

        cv = CVData()
        _fill_every_field(cv)
        reset_cv_owned_fields(cv)

        survivors = _non_empty(cv)
        # Everything belonging to ANOTHER input must survive — a CV swap must
        # never silently delete the LinkedIn or GitHub work.
        other_inputs = {
            n for n in survivors
            if n.startswith(("linkedin_", "github_", "about_me_"))
        }
        unexpected = survivors - other_inputs - set(self.ALLOWED)
        assert not unexpected, (
            f"these CV-owned fields survived the reset: {sorted(unexpected)}. "
            "A new CV will now inherit them from the previous one — and "
            "cv_positions in particular reaches the LLM judge as the "
            "candidate's work history."
        )

    def test_the_other_inputs_are_not_collateral_damage(self) -> None:
        """The control. A reset that wiped everything would pass the test above
        while silently destroying the user's LinkedIn and GitHub data."""
        from src.services.profile.two_pass import reset_cv_owned_fields

        cv = CVData()
        _fill_every_field(cv)
        reset_cv_owned_fields(cv)

        for prefix in ("linkedin_", "github_", "about_me_"):
            kept = [
                f.name for f in dataclasses.fields(CVData)
                if f.name.startswith(prefix) and getattr(cv, f.name)
            ]
            assert kept, (
                f"a CV reset destroyed every {prefix}* field. Those come from a "
                "different upload the user did not touch; wiping them is silent "
                "data loss."
            )

    def test_the_github_and_about_me_hashes_survive(self) -> None:
        """Dropping them would force a paid re-read of inputs that did not
        change."""
        from src.services.profile.two_pass import reset_cv_owned_fields

        cv = CVData(llm_input_hashes={"cv": "a", "linkedin": "b", "github": "c", "about_me": "d"})
        reset_cv_owned_fields(cv)
        assert sorted(cv.llm_input_hashes) == ["about_me", "github"]


class TestClearingLinkedIn:
    def test_nothing_linkedin_survives(self) -> None:
        from src.api.routes.profile import _clear_linkedin

        cv = CVData()
        _fill_every_field(cv)
        _clear_linkedin(cv)

        survivors = [
            f.name for f in dataclasses.fields(CVData)
            if f.name.startswith("linkedin_") and getattr(cv, f.name)
        ]
        assert not survivors, (
            f"these LinkedIn fields survived Clear LinkedIn: {survivors}. "
            "linkedin_summary and linkedin_headline both reach the LLM judge, "
            "so the previous person's words keep scoring the new person's jobs."
        )

    def test_it_does_not_touch_the_cv(self) -> None:
        """The control — a sweep that was too greedy would delete the CV."""
        from src.api.routes.profile import _clear_linkedin

        cv = CVData()
        _fill_every_field(cv)
        # Realistic keys — the blanket filler puts junk in here, which would
        # make the assertion below pass or fail for the wrong reason.
        cv.llm_input_hashes = {"cv": "a", "linkedin": "b", "github": "c"}
        _clear_linkedin(cv)
        assert cv.skills and cv.name and cv.raw_text
        # A LinkedIn clear must not force the CV to be re-extracted.
        assert "cv" in cv.llm_input_hashes
        assert "linkedin" not in cv.llm_input_hashes


class TestClearingGitHub:
    def test_nothing_github_survives(self) -> None:
        from src.api.routes.profile import _clear_github

        cv = CVData()
        _fill_every_field(cv)
        prefs = UserPreferences(github_username="someone")
        _clear_github(cv, prefs)

        survivors = [
            f.name for f in dataclasses.fields(CVData)
            if f.name.startswith("github_") and getattr(cv, f.name)
        ]
        assert not survivors, f"these GitHub fields survived: {survivors}"
        assert prefs.github_username == "", (
            "the handle lives on UserPreferences, outside the prefix sweep, and "
            "must be cleared explicitly"
        )


class TestClearingEverything:
    """What the owner actually clicks: 'Clear profile'. It runs all three."""

    def test_a_full_clear_leaves_only_the_raw_inputs(self) -> None:
        # The REAL button: profile.py's section="all" calls these three.
        # Deliberately NOT reset_cv_owned_fields — that is the re-upload path,
        # and using it here would quietly exempt the upload receipts from a test
        # whose whole purpose is that nothing gets quietly exempted.
        from src.api.routes.profile import _clear_cv, _clear_github, _clear_linkedin

        cv = CVData()
        _fill_every_field(cv)
        prefs = UserPreferences(github_username="someone")

        _clear_cv(cv)
        _clear_linkedin(cv)
        _clear_github(cv, prefs)

        survivors = _non_empty(cv)
        allowed = {
            "llm_input_hashes",  # the about_me hash only, by this point
            "about_me_inferred_skills",  # owned by preferences, cleared with them
        }
        unexpected = survivors - allowed
        assert not unexpected, (
            f"'Clear profile' left these behind: {sorted(unexpected)}. The "
            "button exists so the owner can upload a CV and see exactly what "
            "came out; anything surviving makes that experiment a lie, and "
            "these fields are invisible on a cleared screen while still "
            "reaching the scorer."
        )
