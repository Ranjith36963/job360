"""Workplace + seniority as deterministic signals — precedence, traps, and
the missing-data-file degrade path (the same shape as test_visa_signal.py)."""
from __future__ import annotations

import pytest

from src.services.job_enrichment_schema import SeniorityLevel, WorkplaceType
from src.services.job_signals import (
    SenioritySignal,
    WorkplaceSignal,
    detect_seniority,
    detect_workplace,
)

# ---------------------------------------------------------------------------
# Workplace — each detected value
# ---------------------------------------------------------------------------


class TestWorkplaceDetectedValues:
    @pytest.mark.parametrize("text", [
        "This is a fully remote position.",
        "100% remote — work from anywhere in the UK.",
        "We are a remote-first company.",
        "Remote working, occasional team offsites.",
        "This is a remote role based in the UK.",
    ])
    def test_remote_detected(self, text: str) -> None:
        assert detect_workplace(text).value is WorkplaceType.REMOTE

    @pytest.mark.parametrize("text", [
        "This is a hybrid role.",
        "Hybrid working, 2 days a week in the office.",
        "3 days a week in the office, 2 days remote.",
        "2-3 days per week in office required.",
        "You'll be in the office 3 days a week.",
    ])
    def test_hybrid_detected(self, text: str) -> None:
        assert detect_workplace(text).value is WorkplaceType.HYBRID

    @pytest.mark.parametrize("text", [
        "This role is onsite, based in our Leeds office.",
        "On-site position, no remote working available.",
        "This is an office-based role, 5 days in the office.",
        "Office only — must be based in the office full time.",
    ])
    def test_onsite_detected(self, text: str) -> None:
        assert detect_workplace(text).value is WorkplaceType.ONSITE


class TestWorkplaceUnknownWhenSilent:
    def test_no_mention_is_unknown(self) -> None:
        assert detect_workplace(
            "Great team, competitive salary, based in Leeds."
        ).value is WorkplaceType.UNKNOWN

    def test_empty_and_none_are_unknown(self) -> None:
        assert detect_workplace("").value is WorkplaceType.UNKNOWN
        assert detect_workplace(None).value is WorkplaceType.UNKNOWN
        assert detect_workplace(None, None).value is WorkplaceType.UNKNOWN


class TestWorkplacePrecedenceOrderingTrap:
    """The spec's own example: hybrid signals must win over a co-occurring
    'remote' mention, and an explicit remote statement must win over a
    passing office mention."""

    def test_hybrid_beats_a_co_occurring_remote_mention(self) -> None:
        text = "Remote considered, but this hybrid role needs 2 days a week in the office."
        assert detect_workplace(text).value is WorkplaceType.HYBRID

    def test_day_cadence_beats_remote_even_without_the_word_hybrid(self) -> None:
        text = "This role is remote-friendly with 3 days a week in the office."
        # "remote-friendly" is NOT in the remote vocabulary (deliberately —
        # see workplace_terms.txt); the cadence pattern alone should still
        # win HYBRID here.
        assert detect_workplace(text).value is WorkplaceType.HYBRID

    def test_explicit_remote_beats_a_passing_office_mention(self) -> None:
        text = (
            "This is a fully remote position — we do have a small office "
            "in London if you'd ever like to visit."
        )
        assert detect_workplace(text).value is WorkplaceType.REMOTE


class TestWorkplaceOnsiteAmenityTrap:
    """'On-site parking' / 'on-site gym' is a perk mention, not a
    workplace-arrangement statement — a real UK ad-copy pattern."""

    def test_onsite_parking_alone_is_not_a_workplace_verdict(self) -> None:
        assert detect_workplace(
            "Great benefits including free on-site parking and a pension scheme."
        ).value is WorkplaceType.UNKNOWN

    def test_onsite_gym_alone_is_not_a_workplace_verdict(self) -> None:
        assert detect_workplace(
            "We offer an on-site gym and subsidised canteen."
        ).value is WorkplaceType.UNKNOWN

    def test_amenity_mention_does_not_block_a_real_onsite_signal_elsewhere(self) -> None:
        text = "Office-based role with free on-site parking."
        assert detect_workplace(text).value is WorkplaceType.ONSITE


class TestWorkplaceEnrichmentWins:
    def test_llm_verdict_takes_precedence(self) -> None:
        result = detect_workplace(
            "This is a fully remote position.", enrichment_value="onsite"
        )
        assert result.value is WorkplaceType.ONSITE
        assert result.reason == "enrichment"

    def test_unknown_enrichment_falls_through_to_text(self) -> None:
        result = detect_workplace(
            "This is a fully remote position.", enrichment_value="unknown"
        )
        assert result.value is WorkplaceType.REMOTE
        assert result.reason == "remote_term"

    def test_none_enrichment_falls_through_to_text(self) -> None:
        result = detect_workplace("Hybrid role.", enrichment_value=None)
        assert result.value is WorkplaceType.HYBRID


# ---------------------------------------------------------------------------
# Location field — a fallback source, consulted only when prose is silent.
# Real prod examples (manager audit, 2026-08-07): 165 jobs UNKNOWN while
# location stated the arrangement outright.
# ---------------------------------------------------------------------------


class TestLocationBareRemote:
    """A bare 'Remote' token is trusted in the LOCATION field even though
    the identical bare word is banned in prose — see the module docstring's
    'WHY LOCATION GETS ITS OWN RULES' for why that asymmetry is deliberate,
    not an oversight."""

    def test_bare_remote_location_no_description(self) -> None:
        result = detect_workplace("", "Ranger", location="Remote")
        assert result.value is WorkplaceType.REMOTE
        assert result.reason == "location_remote"

    def test_bare_remote_location_second_prod_example(self) -> None:
        result = detect_workplace("", "Insight", location="Remote")
        assert result.value is WorkplaceType.REMOTE

    def test_empty_description_and_title_still_reads_location(self) -> None:
        result = detect_workplace(None, None, location="Remote")
        assert result.value is WorkplaceType.REMOTE


class TestLocationMultiSite:
    """Named office cities ALONGSIDE 'Remote' resolve to HYBRID — the
    employer is offering a choice of site, not committing to fully remote.
    WorkplaceType has no 'flexible' member, so HYBRID is the closest true
    statement among remote/onsite/hybrid. See the module docstring for the
    full defence of this call."""

    def test_prod_example_lead_product_designer(self) -> None:
        result = detect_workplace(
            "", "Lead Product Designer", location="Cardiff, London or Remote (UK)"
        )
        assert result.value is WorkplaceType.HYBRID
        assert result.reason == "location_multi_site"

    def test_prod_example_ml_manager(self) -> None:
        result = detect_workplace(
            "",
            "Machine Learning Manager, Operations",
            location="Cardiff, London or Remote (UK)",
        )
        assert result.value is WorkplaceType.HYBRID

    def test_office_and_remote_without_country_qualifier(self) -> None:
        result = detect_workplace("", "Engineer", location="London or Remote")
        assert result.value is WorkplaceType.HYBRID

    def test_country_qualifier_alone_does_not_count_as_an_office(self) -> None:
        """'Remote (UK)' — 'UK' is a nation qualifier, not a named office,
        so this must stay pure REMOTE, not be dragged into HYBRID."""
        result = detect_workplace("", "Engineer", location="Remote (UK)")
        assert result.value is WorkplaceType.REMOTE
        assert result.reason == "location_remote"


class TestLocationVsDescriptionConflict:
    """Prose is the MORE SPECIFIC claim and must win over a stale or
    generic location field — requirement from the manager review."""

    def test_explicit_onsite_description_beats_remote_location(self) -> None:
        result = detect_workplace(
            "This role is fully onsite, 5 days a week in the office.",
            "Backend Engineer",
            location="Remote",
        )
        assert result.value is WorkplaceType.ONSITE
        assert result.reason == "onsite_term"

    def test_explicit_hybrid_description_beats_remote_location(self) -> None:
        result = detect_workplace(
            "This is a hybrid role.",
            "Backend Engineer",
            location="Remote",
        )
        assert result.value is WorkplaceType.HYBRID

    def test_explicit_remote_description_agrees_with_office_location(self) -> None:
        """Description wins regardless of which way the conflict points."""
        result = detect_workplace(
            "This is a fully remote position.",
            "Backend Engineer",
            location="London",
        )
        assert result.value is WorkplaceType.REMOTE
        assert result.reason == "remote_term"


class TestLocationEmptyBehavesAsBefore:
    def test_no_location_argument_is_unknown_when_text_is_silent(self) -> None:
        result = detect_workplace("Great team, competitive salary.", "Engineer")
        assert result.value is WorkplaceType.UNKNOWN

    def test_empty_string_location_is_unknown_when_text_is_silent(self) -> None:
        result = detect_workplace("Great team, competitive salary.", "Engineer", location="")
        assert result.value is WorkplaceType.UNKNOWN

    def test_none_location_is_unknown_when_text_is_silent(self) -> None:
        result = detect_workplace("Great team, competitive salary.", "Engineer", location=None)
        assert result.value is WorkplaceType.UNKNOWN

    def test_whitespace_only_location_is_unknown(self) -> None:
        result = detect_workplace("Great team, competitive salary.", "Engineer", location="   ")
        assert result.value is WorkplaceType.UNKNOWN

    def test_location_default_matches_omitted_location(self) -> None:
        """The default value behaves identically to not passing it at all."""
        omitted = detect_workplace("Great team, competitive salary.", "Engineer")
        defaulted = detect_workplace("Great team, competitive salary.", "Engineer", location="")
        assert omitted == defaulted


class TestExistingCallersUnaffectedByLocationParam:
    """Every existing (description, title) caller must be byte-for-byte
    unaffected by adding the keyword-only `location` parameter."""

    def test_positional_two_arg_call_still_works(self) -> None:
        assert detect_workplace("This is a hybrid role.", "Engineer").value \
            is WorkplaceType.HYBRID

    def test_single_positional_arg_call_still_works(self) -> None:
        assert detect_workplace("This is a fully remote position.").value \
            is WorkplaceType.REMOTE

    def test_enrichment_only_call_still_works(self) -> None:
        result = detect_workplace("", enrichment_value="onsite")
        assert result.value is WorkplaceType.ONSITE
        assert result.reason == "enrichment"


# ---------------------------------------------------------------------------
# Seniority — each detected value
# ---------------------------------------------------------------------------


class TestSeniorityDetectedValues:
    def test_intern(self) -> None:
        assert detect_seniority("Summer Intern, Data Team").value is SeniorityLevel.INTERN

    def test_junior(self) -> None:
        assert detect_seniority("Graduate Software Engineer").value is SeniorityLevel.JUNIOR

    def test_mid(self) -> None:
        assert detect_seniority("Mid-level Backend Developer").value is SeniorityLevel.MID

    def test_senior(self) -> None:
        assert detect_seniority("Senior Backend Engineer").value is SeniorityLevel.SENIOR

    def test_staff(self) -> None:
        assert detect_seniority("Staff Software Engineer").value is SeniorityLevel.STAFF

    def test_principal(self) -> None:
        assert detect_seniority("Principal Engineer").value is SeniorityLevel.PRINCIPAL

    def test_director(self) -> None:
        assert detect_seniority("Director of Engineering").value is SeniorityLevel.DIRECTOR


class TestSeniorityUnknownWhenSilent:
    def test_no_mention_is_unknown(self) -> None:
        assert detect_seniority("Backend Developer", "Great team, nice office.").value \
            is SeniorityLevel.UNKNOWN

    def test_empty_and_none_are_unknown(self) -> None:
        assert detect_seniority("").value is SeniorityLevel.UNKNOWN
        assert detect_seniority(None).value is SeniorityLevel.UNKNOWN
        assert detect_seniority(None, None).value is SeniorityLevel.UNKNOWN
        assert detect_seniority().value is SeniorityLevel.UNKNOWN


class TestSeniorityReadsTitleFirst:
    def test_title_signal_wins_over_description_signal(self) -> None:
        result = detect_seniority(
            "Senior Backend Engineer",
            "You'll mentor our graduate scheme intake.",
        )
        assert result.value is SeniorityLevel.SENIOR
        assert result.reason == "title"

    def test_description_fills_the_gap_when_title_is_silent(self) -> None:
        result = detect_seniority(
            "Backend Engineer",
            "This is a senior role requiring 8+ years' experience.",
        )
        assert result.value is SeniorityLevel.SENIOR
        assert result.reason == "description"

    def test_title_exists_when_description_is_empty(self) -> None:
        """The whole point: the 1,311 no-description jobs still have a title."""
        result = detect_seniority("Graduate Data Analyst", "")
        assert result.value is SeniorityLevel.JUNIOR
        assert result.reason == "title"


class TestSeniorityFalsePositiveTraps:
    def test_junior_school_teacher_is_not_junior(self) -> None:
        """'Junior School' is a UK institution (ages 7-11), not a claim
        about the role's level."""
        assert detect_seniority("Junior School Teacher").value is SeniorityLevel.UNKNOWN

    def test_junior_school_teacher_with_description_still_unknown(self) -> None:
        assert detect_seniority(
            "Junior School Teacher",
            "Join our friendly junior school in a supportive environment.",
        ).value is SeniorityLevel.UNKNOWN

    def test_senior_care_assistant_is_genuinely_senior(self) -> None:
        """Contrast case: 'Senior Care Assistant' is a real, genuinely
        senior title in the care sector — it must NOT be swept up by the
        junior-school-style guard."""
        assert detect_seniority("Senior Care Assistant").value is SeniorityLevel.SENIOR

    def test_head_of_year_is_not_director(self) -> None:
        """'Head of Year' is a school pastoral title, not the
        department-leadership sense 'head of' means elsewhere."""
        assert detect_seniority("Head of Year 5").value is SeniorityLevel.UNKNOWN

    def test_head_of_house_is_not_director(self) -> None:
        assert detect_seniority("Head of House Coordinator").value is SeniorityLevel.UNKNOWN

    def test_head_of_engineering_is_genuinely_director(self) -> None:
        """Contrast case: 'head of' outside the school-pastoral phrases is a
        real leadership title and must still fire."""
        assert detect_seniority("Head of Engineering").value is SeniorityLevel.DIRECTOR

    def test_staff_nurse_is_not_staff_tier(self) -> None:
        """'Staff Nurse' is an NHS entry grade (Band 5) — bare 'staff' is
        deliberately absent from the vocabulary for this reason."""
        assert detect_seniority("Staff Nurse").value is SeniorityLevel.UNKNOWN

    def test_account_executive_is_not_director(self) -> None:
        """'Account Executive' is a common UK entry-level sales title
        despite containing 'executive' — bare 'executive' is deliberately
        absent from the vocabulary."""
        assert detect_seniority("Account Executive").value is SeniorityLevel.UNKNOWN

    def test_lead_generation_is_not_a_seniority_claim(self) -> None:
        """'Lead Generation Executive' — 'lead' here names a sales
        prospect, not the role's seniority."""
        assert detect_seniority("Lead Generation Executive").value is SeniorityLevel.UNKNOWN

    def test_graduate_degree_requirement_reads_as_the_experience_level(self) -> None:
        """'Graduate' can describe an educational requirement rather than
        the role's level; when a genuine senior signal is also present, the
        senior-first rank order resolves it correctly."""
        result = detect_seniority(
            "Backend Engineer",
            "Senior role, must hold a graduate degree and have 5+ years' experience.",
        )
        assert result.value is SeniorityLevel.SENIOR


class TestSeniorityEnrichmentWins:
    def test_llm_verdict_takes_precedence(self) -> None:
        result = detect_seniority("Graduate Software Engineer", enrichment_value="senior")
        assert result.value is SeniorityLevel.SENIOR
        assert result.reason == "enrichment"

    def test_unknown_enrichment_falls_through_to_text(self) -> None:
        result = detect_seniority("Graduate Software Engineer", enrichment_value="unknown")
        assert result.value is SeniorityLevel.JUNIOR
        assert result.reason == "title"

    def test_none_enrichment_falls_through_to_text(self) -> None:
        result = detect_seniority("Principal Engineer", enrichment_value=None)
        assert result.value is SeniorityLevel.PRINCIPAL


# ---------------------------------------------------------------------------
# Missing data file degrades to UNKNOWN, never raises
# ---------------------------------------------------------------------------


class TestMissingDataFileDegradesGracefully:
    @pytest.fixture(autouse=True)
    def _clear_every_vocabulary_cache(self):
        """A REAL leak these tests were causing, found 2026-08-13.

        Each test below cleared only the ONE cache it was named after, but
        `detect_workplace` touches two: `test_location_terms_file_missing`
        calls it with a title, so `_detect_prose_signal` populated
        `_workplace_terms` as {} while `_DATA` was still monkeypatched — and
        nothing cleared it. Every later test in the SESSION then saw an empty
        workplace vocabulary. It stayed invisible only because no other test
        asserted the vocabulary was non-empty; `tests/test_shipped_data.py`
        now does, and caught it. Under `pytest-randomly` this could have
        surfaced anywhere.

        Clearing all three on both sides is the fix — the caches are module
        globals, so per-test cleanup belongs here, not in each test's finally.
        """
        from src.services import job_signals

        def _clear() -> None:
            job_signals._workplace_terms.cache_clear()
            job_signals._seniority_terms.cache_clear()
            job_signals._location_terms.cache_clear()

        _clear()
        yield
        _clear()

    def test_workplace_terms_file_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from src.services import job_signals

        job_signals._workplace_terms.cache_clear()
        monkeypatch.setattr(job_signals, "_DATA", job_signals._DATA / "does_not_exist")
        try:
            result = job_signals.detect_workplace("This is a fully remote position.")
            assert result.value is WorkplaceType.UNKNOWN
            assert result.reason == "no_signal"
        finally:
            job_signals._workplace_terms.cache_clear()

    def test_seniority_terms_file_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from src.services import job_signals

        job_signals._seniority_terms.cache_clear()
        monkeypatch.setattr(job_signals, "_DATA", job_signals._DATA / "does_not_exist")
        try:
            result = job_signals.detect_seniority("Senior Backend Engineer")
            assert result.value is SeniorityLevel.UNKNOWN
            assert result.reason == "no_signal"
        finally:
            job_signals._seniority_terms.cache_clear()

    def test_location_terms_file_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from src.services import job_signals

        job_signals._location_terms.cache_clear()
        monkeypatch.setattr(job_signals, "_DATA", job_signals._DATA / "does_not_exist")
        try:
            result = job_signals.detect_workplace("", "Ranger", location="Remote")
            assert result.value is WorkplaceType.UNKNOWN
            assert result.reason == "no_signal"
        finally:
            job_signals._location_terms.cache_clear()


# ---------------------------------------------------------------------------
# Result types are the reused enums, not parallel ones
# ---------------------------------------------------------------------------


class TestResultTypesReuseSharedEnums:
    def test_workplace_signal_wraps_workplace_type(self) -> None:
        result = detect_workplace("Hybrid role.")
        assert isinstance(result, WorkplaceSignal)
        assert isinstance(result.value, WorkplaceType)

    def test_seniority_signal_wraps_seniority_level(self) -> None:
        result = detect_seniority("Senior Engineer")
        assert isinstance(result, SenioritySignal)
        assert isinstance(result.value, SeniorityLevel)


class TestSignalBackedLookupWiring:
    """The detectors are only worth building if something CALLS them.

    `JobScorer(enrichment_lookup=...)` takes a callable OF A JOB — the one
    place where the enrichment record and the job's own title/location are
    both in scope. The bulk loader is keyed by job_id and never sees the job;
    the dim scorers receive only the enrichment. So this wrapper is the single
    seam, and wiring it once reaches the API read path, rescore and the worker.
    """

    class _Job:
        def __init__(self, title="", description="", location=""):
            self.id = 1
            self.title = title
            self.description = description
            self.location = location

    class _Enrichment:
        def __init__(self, workplace_type="unknown", seniority="unknown"):
            self.workplace_type = workplace_type
            self.seniority = seniority

    def test_fills_unknown_from_the_title(self):
        from src.services.job_signals import signal_backed_lookup

        enr = self._Enrichment()
        job = self._Job(title="Senior Data Engineer")
        out = signal_backed_lookup(lambda j: enr)(job)
        assert str(getattr(out.seniority, "value", out.seniority)) == "senior"

    def test_fills_workplace_from_the_location_field(self):
        from src.services.job_signals import signal_backed_lookup

        enr = self._Enrichment()
        job = self._Job(title="Ranger", location="Remote")
        out = signal_backed_lookup(lambda j: enr)(job)
        assert str(getattr(out.workplace_type, "value", out.workplace_type)) == "remote"

    def test_never_overwrites_an_llm_verdict(self):
        """Precedence is one-way: the LLM read the whole ad, the detector read
        a title. Only a literal 'unknown' may be filled."""
        from src.services.job_signals import signal_backed_lookup

        enr = self._Enrichment(seniority="junior")
        job = self._Job(title="Senior Data Engineer")
        out = signal_backed_lookup(lambda j: enr)(job)
        assert str(getattr(out.seniority, "value", out.seniority)) == "junior"

    def test_missing_enrichment_stays_none(self):
        from src.services.job_signals import signal_backed_lookup

        assert signal_backed_lookup(lambda j: None)(self._Job()) is None

    def test_a_detector_failure_can_never_break_scoring(self):
        """A frozen/validated model may refuse assignment. The dim scorer must
        then see 'unknown' and return its neutral constant — never raise."""
        from src.services.job_signals import signal_backed_lookup

        class Frozen:
            workplace_type = "unknown"
            seniority = "unknown"

            def __setattr__(self, *a):
                raise AttributeError("frozen")

        out = signal_backed_lookup(lambda j: Frozen())(
            self._Job(title="Senior Engineer")
        )
        assert out is not None


# ---------------------------------------------------------------------------
# The data has to reach the CONTAINER, not just the developer's disk
# ---------------------------------------------------------------------------


class TestTheDataShipsWithTheInstalledPackage:
    """THIRD instance of one packaging bug (ESCO was the first, the UK
    gazetteer — issue #260, PR #312 — the second, this is the third).

    Production installs the app with `pip install .`, and
    `[tool.setuptools.packages.find] include = ["src*"]` copies ONLY the `src`
    packages. Anything the code reaches for outside `src/` resolves, inside the
    container, to `<site-packages>/<whatever>` — a path that does not exist.
    `job_signals._DATA` pointed at `backend/data/job_signals`, so
    `_load_terms()` returned `{}` for all three vocabularies and
    `signal_backed_lookup` / `detect_seniority` answered UNKNOWN for every job
    in production while every test on this machine passed.

    These tests assert the three things that would have caught it: the RUNTIME
    path lives inside the package, the packaging config actually copies it, and
    an empty load is LOUD.
    """

    def test_data_sits_inside_the_installed_package_tree(self) -> None:
        from pathlib import Path

        from src.services import job_signals

        package_root = Path(job_signals.__file__).resolve().parent.parent
        assert job_signals._DATA.is_relative_to(package_root), (
            f"job_signals reads {job_signals._DATA}, which is outside the "
            f"installed package ({package_root}). `pip install .` ships only "
            "`src*`, so in the container that path does not exist and every "
            "detector silently answers UNKNOWN."
        )

    @pytest.mark.parametrize("name", [
        "workplace_terms.txt",
        "workplace_location_terms.txt",
        "seniority_terms.txt",
    ])
    def test_every_vocabulary_file_is_present_and_non_empty(self, name: str) -> None:
        from src.services import job_signals

        path = job_signals._DATA / name
        assert path.exists(), f"{name} is missing from {job_signals._DATA}"
        assert path.stat().st_size > 100, f"{name} is present but effectively empty"

    def test_packaging_config_ships_the_data_with_the_wheel(self) -> None:
        """Living under `src/` is necessary but NOT sufficient — setuptools
        copies non-.py files only when they are declared as package data."""
        from pathlib import Path

        from src.services import job_signals

        backend = Path(job_signals.__file__).resolve().parent.parent.parent
        pyproject = (backend / "pyproject.toml").read_text(encoding="utf-8")
        assert "[tool.setuptools.package-data]" in pyproject
        assert "data/job_signals" in pyproject, (
            "pyproject does not declare the job_signals vocabularies as package "
            "data, so the wheel that production installs does not contain them."
        )


class TestAMissingVocabularyIsLoud:
    """Graceful degradation is right — a lost data file must not crash the
    pipeline. SILENT degradation is what let this survive: every instrument
    stayed green while the detectors decided nothing."""

    def test_missing_file_logs_an_error_naming_the_path(
        self, tmp_path, caplog: pytest.LogCaptureFixture
    ) -> None:
        import logging

        from src.services import job_signals

        original = job_signals._DATA
        try:
            job_signals._DATA = tmp_path / "absent"
            with caplog.at_level(logging.ERROR):
                assert job_signals._load_terms("workplace_terms.txt") == {}
            messages = [r.getMessage() for r in caplog.records]
            assert any("workplace_terms.txt" in m for m in messages), (
                "a missing vocabulary must name the path it looked at, loudly; "
                f"got {messages!r}"
            )
        finally:
            job_signals._DATA = original

    def test_a_file_that_parses_to_nothing_logs_an_error(
        self, tmp_path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Present-but-useless is the nastier case: `path.exists()` is True, so
        an existence check passes while the vocabulary is still empty."""
        import logging

        from src.services import job_signals

        (tmp_path / "workplace_terms.txt").write_text(
            "# only a comment\n\n", encoding="utf-8"
        )
        original = job_signals._DATA
        try:
            job_signals._DATA = tmp_path
            with caplog.at_level(logging.ERROR):
                assert job_signals._load_terms("workplace_terms.txt") == {}
            assert any(
                "workplace_terms.txt" in r.getMessage() for r in caplog.records
            ), "an empty vocabulary must scream in the logs, not pass silently"
        finally:
            job_signals._DATA = original
