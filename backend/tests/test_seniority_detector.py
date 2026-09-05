"""Seniority as a deterministic signal — precedence, traps, and the
missing-data-file degrade path.

Was `tests/test_job_signals.py`. Slice 5 (#483) deleted
`services/job_signals.py`: the workplace detector and the
`signal_backed_lookup` seam existed only for the job scorer, which is gone.
`detect_seniority` survived because PROFILE extraction reads it — a CV's
dated job titles are the one place a seniority word is a fact about the user
— so it moved to `services/profile/seniority.py` and its tests moved here
with it.
"""
from __future__ import annotations

import pytest

from src.services.profile.seniority import (
    SeniorityLevel,
    SenioritySignal,
    detect_seniority,
)

# ---------------------------------------------------------------------------
# The vocabulary file is missing -> UNKNOWN, never a crash
# ---------------------------------------------------------------------------


class TestMissingDataFileDegradesGracefully:
    @pytest.fixture(autouse=True)
    def _clear_the_vocabulary_cache(self):
        """The cache is a module global, so per-test cleanup belongs here, not
        in each test's finally: a test that repoints `_DATA` and leaves an
        empty vocabulary cached poisons every later test in the SESSION. That
        really happened (2026-08-13) and stayed invisible until
        `tests/test_shipped_data.py` started asserting the vocabulary is
        non-empty."""
        from src.services.profile import seniority

        seniority._seniority_terms.cache_clear()
        yield
        seniority._seniority_terms.cache_clear()

    def test_seniority_terms_file_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from src.services.profile import seniority

        monkeypatch.setattr(seniority, "_DATA", seniority._DATA / "does_not_exist")
        result = seniority.detect_seniority("Senior Backend Engineer")
        assert result.value is SeniorityLevel.UNKNOWN
        assert result.reason == "no_signal"


# ---------------------------------------------------------------------------
# The result type is the shared enum, not a parallel one
# ---------------------------------------------------------------------------


class TestResultTypeReusesTheSharedEnum:
    def test_seniority_signal_wraps_seniority_level(self) -> None:
        result = detect_seniority("Senior Engineer")
        assert isinstance(result, SenioritySignal)
        assert isinstance(result.value, SeniorityLevel)

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
# The data has to reach the CONTAINER, not just the developer's disk
# ---------------------------------------------------------------------------


class TestTheDataShipsWithTheInstalledPackage:
    """THIRD instance of one packaging bug (ESCO was the first, the UK
    gazetteer — issue #260, PR #312 — the second, this is the third).

    Production installs the app with `pip install .`, and
    `[tool.setuptools.packages.find] include = ["src*"]` copies ONLY the `src`
    packages. Anything the code reaches for outside `src/` resolves, inside the
    container, to `<site-packages>/<whatever>` — a path that does not exist.
    The loader's `_DATA` pointed at `backend/data/job_signals`, so
    `_load_terms()` returned `{}` for the vocabulary and
    `detect_seniority` answered UNKNOWN for every job
    in production while every test on this machine passed.

    These tests assert the three things that would have caught it: the RUNTIME
    path lives inside the package, the packaging config actually copies it, and
    an empty load is LOUD.
    """

    def test_data_sits_inside_the_installed_package_tree(self) -> None:
        from pathlib import Path

        from src.services.profile import seniority as job_signals

        package_root = Path(job_signals.__file__).resolve().parents[2]
        assert job_signals._DATA.is_relative_to(package_root), (
            f"the seniority detector reads {job_signals._DATA}, which is outside the "
            f"installed package ({package_root}). `pip install .` ships only "
            "`src*`, so in the container that path does not exist and every "
            "detector silently answers UNKNOWN."
        )

    @pytest.mark.parametrize("name", ["seniority_terms.txt"])
    def test_every_vocabulary_file_is_present_and_non_empty(self, name: str) -> None:
        from src.services.profile import seniority as job_signals

        path = job_signals._DATA / name
        assert path.exists(), f"{name} is missing from {job_signals._DATA}"
        assert path.stat().st_size > 100, f"{name} is present but effectively empty"

    def test_packaging_config_ships_the_data_with_the_wheel(self) -> None:
        """Living under `src/` is necessary but NOT sufficient — setuptools
        copies non-.py files only when they are declared as package data."""
        from pathlib import Path

        from src.services.profile import seniority as job_signals

        backend = Path(job_signals.__file__).resolve().parents[3]
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

        from src.services.profile import seniority as job_signals

        original = job_signals._DATA
        try:
            job_signals._DATA = tmp_path / "absent"
            with caplog.at_level(logging.ERROR):
                assert job_signals._load_terms("seniority_terms.txt") == {}
            messages = [r.getMessage() for r in caplog.records]
            assert any("seniority_terms.txt" in m for m in messages), (
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

        from src.services.profile import seniority as job_signals

        (tmp_path / "seniority_terms.txt").write_text(
            "# only a comment\n\n", encoding="utf-8"
        )
        original = job_signals._DATA
        try:
            job_signals._DATA = tmp_path
            with caplog.at_level(logging.ERROR):
                assert job_signals._load_terms("seniority_terms.txt") == {}
            assert any(
                "seniority_terms.txt" in r.getMessage() for r in caplog.records
            ), "an empty vocabulary must scream in the logs, not pass silently"
        finally:
            job_signals._DATA = original
