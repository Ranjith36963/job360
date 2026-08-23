"""The funnel's first gate, finally fed — and deliberately fed only part of the profile.

``passes_prefilter`` has been called in production since it was written, always
with a bare ``FilterProfile()``. Every field sat at its default, so all three
stages took their "nothing declared, pass everything" branch: the cheap gate that
exists to spare the expensive stages filtered nothing, for every user, ever.

Feeding it is easy. Feeding it CORRECTLY is the part with evidence behind it,
and these tests pin the two things a future change is most likely to get wrong.

Measured against 2,000 live jobs and the owner's real profile (2026-08-09):
    stage                     kept
    location_ok               100.0%   (no locations stated)
    experience_ok              75.5%   using the INFERRED level -> 24.5% deleted
    skill_overlap_ok           40.0%   using his 80 real skills
    all three, skills wired    28.8%   -> seven jobs in ten deleted before scoring
"""
from __future__ import annotations

import pytest

from src.models import Job
from src.services.prefilter import location_ok, passes_prefilter
from src.services.profile.models import CVData, UserPreferences, UserProfile
from src.workers.tasks import _filter_profile_for


def _job_at(location: str) -> Job:
    """A job in a specific place - the location stage is what is under test."""
    return Job(
        title="Machine Learning Engineer",
        company="Acme",
        apply_url="https://example.com/1",
        source="test",
        date_found="2026-08-12",
        location=location,
        description="We use Rust.",
    )


def _job(title: str = "Machine Learning Engineer", desc: str = "We use Rust.") -> Job:
    return Job(
        title=title,
        company="Acme",
        apply_url="https://example.com/1",
        source="test",
        date_found="2026-08-09",
        location="London",
        description=desc,
    )


class TestTheGateIsActuallyFed:
    def test_a_stated_location_reaches_the_filter(self, monkeypatch) -> None:
        profile = UserProfile(
            cv_data=CVData(raw_text="x", skills=["python"]),
            preferences=UserPreferences(preferred_locations=["Manchester"]),
        )
        monkeypatch.setattr(
            "src.workers.tasks._user_profile_for", lambda _uid: profile
        )
        fp = _filter_profile_for("u1")
        assert fp.preferred_locations == ["Manchester"], (
            "the gate is still being handed an empty profile"
        )
        # London job, Manchester preference -> the gate now has something to say.
        assert passes_prefilter(fp, _job()) is False

    def test_no_profile_means_no_filtering(self, monkeypatch) -> None:
        monkeypatch.setattr("src.workers.tasks._user_profile_for", lambda _uid: None)
        assert passes_prefilter(_filter_profile_for("u1"), _job()) is True


class TestAnUnstatedPreferenceNeverDeletesAJob:
    """Rule #29, at the sharpest place it applies.

    A soft score may lean on an inferred value — it only moves a job's position.
    A gate REMOVES the job, so it must run on what the user actually said.
    """

    def test_the_inferred_level_does_not_gate(self, monkeypatch) -> None:
        # Typed level empty; only the CV-inferred one is set. Wiring the
        # resolver here deleted 24.5% of the live catalogue — every senior+
        # role — for a user who never called himself junior.
        profile = UserProfile(
            cv_data=CVData(raw_text="x", skills=["python"]),
            preferences=UserPreferences(
                experience_level="", experience_level_inferred="junior"
            ),
        )
        monkeypatch.setattr(
            "src.workers.tasks._user_profile_for", lambda _uid: profile
        )
        fp = _filter_profile_for("u1")
        assert fp.experience_level == "", (
            "the INFERRED level reached the gate — an unstated preference must "
            "never delete a job (rule #29)"
        )
        assert passes_prefilter(fp, _job(title="Principal ML Engineer")) is True

    def test_a_typed_level_does_gate(self, monkeypatch) -> None:
        """The other half: what the user DID state must still work, or this
        would just be the old do-nothing gate with extra steps."""
        profile = UserProfile(
            cv_data=CVData(raw_text="x", skills=["python"]),
            preferences=UserPreferences(experience_level="junior"),
        )
        monkeypatch.setattr(
            "src.workers.tasks._user_profile_for", lambda _uid: profile
        )
        fp = _filter_profile_for("u1")
        assert fp.experience_level == "junior"
        assert passes_prefilter(fp, _job(title="Principal ML Engineer")) is False


class TestACountryLevelPreferenceIsNotAConstraint:
    """The hazard created by wiring this gate at all.

    ``location_ok`` compares by SUBSTRING. Before the prefilter was fed, that
    never ran on real data. Once it did, the single most natural thing a UK user
    can type — "United Kingdom" — matched neither direction of "London", so the
    gate would have deleted almost the entire feed the moment anyone set it.

    The catalogue is UK-only by construction (rule #30 refuses the rest at
    ingestion), so a country-level preference carries no information this gate
    can act on. It passes.
    """

    def _fp(self, monkeypatch, locations):
        profile = UserProfile(
            cv_data=CVData(raw_text="x", skills=["python"]),
            preferences=UserPreferences(preferred_locations=locations),
        )
        monkeypatch.setattr(
            "src.workers.tasks._user_profile_for", lambda _uid: profile
        )
        return _filter_profile_for("u1")

    def test_united_kingdom_does_not_delete_a_london_job(self, monkeypatch) -> None:
        fp = self._fp(monkeypatch, ["United Kingdom"])
        assert passes_prefilter(fp, _job()) is True, (
            "a country-level location preference deleted a UK job — the whole "
            "catalogue is UK-only, so this preference constrains nothing"
        )

    def test_every_way_of_naming_the_uk_behaves_the_same(self, monkeypatch) -> None:
        for name in ("UK", "uk", "United Kingdom", "Great Britain", "England",
                     "Scotland", "Wales", "Northern Ireland"):
            fp = self._fp(monkeypatch, [name])
            assert passes_prefilter(fp, _job()) is True, f"{name!r} filtered a UK job"

    def test_a_real_city_preference_still_filters(self, monkeypatch) -> None:
        """The fix must not turn the gate back into a no-op: a CITY the user
        named is a genuine constraint and must still apply."""
        fp = self._fp(monkeypatch, ["Manchester"])
        assert passes_prefilter(fp, _job()) is False  # the job is in London

    def test_a_country_preference_alongside_a_city_still_passes_both(
        self, monkeypatch
    ) -> None:
        fp = self._fp(monkeypatch, ["United Kingdom", "Manchester"])
        # London matches the country entry, so it survives — the user asking for
        # "UK or Manchester" wants UK-wide results.
        assert passes_prefilter(fp, _job()) is True


class TestAnUnresolvablePlaceNeverDeletesTheFeed:
    """PR #297 fixed the COUNTRY case and stopped one level too high.

    Measured against the real predicate after #297 shipped:

        "United Kingdom"  keeps everything   (the #297 fix)
        "Greater London"  keeps London only  (and only because one string is a
                                              substring of the other)
        "West Midlands"   keeps NOTHING      (Birmingham is IN it)
        "Yorkshire"       keeps NOTHING      (Leeds is IN it)
        "South East"      keeps NOTHING

    A county or region is the second most natural thing a UK user types after a
    city, and it emptied the whole feed. The gazetteer decides this from DATA,
    not a typed list of regions: it holds ~52k settlements and no administrative
    divisions, so "can this gate compare the preference to a job location?" is
    "is it in the gazetteer?".
    """

    def _fp(self, monkeypatch, locations):
        profile = UserProfile(
            cv_data=CVData(raw_text="x", skills=["python"]),
            preferences=UserPreferences(preferred_locations=locations),
        )
        monkeypatch.setattr(
            "src.workers.tasks._user_profile_for", lambda _uid: profile
        )
        return _filter_profile_for("u1")

    @pytest.mark.parametrize(
        "region", ["Greater London", "West Midlands", "Yorkshire", "South East"]
    )
    def test_a_region_or_county_keeps_the_feed(self, monkeypatch, region) -> None:
        fp = self._fp(monkeypatch, [region])
        for city in ("London", "Manchester", "Birmingham", "Leeds"):
            assert location_ok(fp, _job_at(city)) is True, (
                f"{region!r} deleted a job in {city!r} — a preference this gate "
                "cannot resolve must never empty the feed"
            )

    def test_a_real_city_still_filters(self, monkeypatch) -> None:
        """The fix must not turn the stage back into a no-op: a settlement the
        user named is resolvable, so it genuinely constrains."""
        fp = self._fp(monkeypatch, ["Manchester"])
        assert location_ok(fp, _job_at("Manchester")) is True
        assert location_ok(fp, _job_at("London")) is False

    def test_a_missing_gazetteer_fails_open(self, monkeypatch) -> None:
        """The image shipped without the gazetteer for weeks. If that recurs,
        an unresolvable preference must pass, never delete."""
        from src.services import prefilter as pf

        monkeypatch.setattr(
            pf, "_no_resolvable_place", lambda _prefs: True
        )
        fp = self._fp(monkeypatch, ["Manchester"])
        assert location_ok(fp, _job_at("London")) is True


class TestSkillsStayOutUntilMeasured:
    """Extracted skills are NOT a stated preference, and the one measurement we
    have says wiring them here deletes 71% of the catalogue. If a future change
    wants them, it owes a fresh dry run — not an assumption."""

    def test_skills_are_not_wired_into_the_gate(self, monkeypatch) -> None:
        profile = UserProfile(
            cv_data=CVData(raw_text="x", skills=["python", "pytorch", "kubernetes"]),
            preferences=UserPreferences(),
        )
        monkeypatch.setattr(
            "src.workers.tasks._user_profile_for", lambda _uid: profile
        )
        fp = _filter_profile_for("u1")
        assert fp.skills == set(), (
            "skills reached the prefilter — measured at 71% of the live "
            "catalogue deleted. Re-run scripts against prod before changing this."
        )
        # A job sharing none of those skills must still survive to be scored.
        assert passes_prefilter(fp, _job(desc="We write COBOL on mainframes.")) is True
