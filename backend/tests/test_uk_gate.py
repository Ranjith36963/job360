"""The UK gate — one door every job passes before storage.

Job360 is a UK-market product; a Warsaw job in a user's feed is a product
fault, not a ranking preference. These tests pin decisions derived from
measuring the LIVE catalog on 2026-08-07, including every false-drop and
false-allow trap found before this shipped.

The governing rule (owner, 2026-08-07): never hand-enumerate an UNBOUNDED set.
Foreign cities are unbounded, so they are never typed; UK places are finite and
published, so the gate matches against DATA in `src/data/uk_gazetteer/`. Countries
and admin divisions stay enumerated because those are CLOSED sets — a complete
closed set is not the same mistake as a sample of an open one.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.services.uk_gate import UK_NATIVE_SOURCES, check_uk


class TestObviousCases:
    def test_uk_location_allowed(self) -> None:
        assert check_uk("London", "greenhouse").allowed

    @pytest.mark.parametrize("loc", ["Indianapolis, IN, USA", "Berlin, Germany",
                                     "Sydney, Australia", "Bangalore, India"])
    def test_foreign_country_blocked_from_any_source(self, loc: str) -> None:
        v = check_uk(loc, "greenhouse")
        assert not v.allowed and v.reason == "foreign_location"

    def test_foreign_beats_even_a_uk_native_source(self) -> None:
        """A UK board can still syndicate a foreign ad. Country wins."""
        assert not check_uk("Berlin, Germany", "reed").allowed

    def test_plain_remote_allowed(self) -> None:
        assert check_uk("Remote", "weworkremotely").allowed


class TestRemoteIsNotAlwaysRemote:
    """'Remote' on a global board often means remote-in-another-country."""

    @pytest.mark.parametrize("loc", ["Remote (US only)", "Remote - US based",
                                     "Remote, EU only", "Remote (India)"])
    def test_region_fenced_remote_blocked(self, loc: str) -> None:
        assert not check_uk(loc, "remoteok").allowed

    def test_fencing_in_body_blocks_when_location_absent(self) -> None:
        v = check_uk("", "greenhouse", description="Fully remote. Must be US based.")
        assert not v.allowed


class TestBoundedVsUnbounded:
    """The heart of the design. The first version hand-typed ~120 foreign city
    names; the owner rejected it: "How many will you hard-code like that? What
    if there is a city out of this list? Then you missed that."
    """

    def test_no_foreign_city_list_exists_in_code(self) -> None:
        import src.services.uk_gate as gate

        src = Path(gate.__file__).read_text(encoding="utf-8").lower()
        # These appear in prose (the docstring explains the history), so the
        # check is that no *collection literal* of them survives.
        assert "_foreign_cities" not in src, "the hand-typed city set is back"

    def test_towns_the_old_hand_list_missed_are_all_known(self) -> None:
        """Every one of these was wrongly blocked by the 26-term version."""
        for town in ("Telford", "Fareham", "Northampton", "Wellingborough",
                     "Cardiff", "Newcastle upon Tyne", "Shieldfield"):
            assert check_uk(town, "greenhouse").allowed, town

    def test_northern_ireland_is_covered(self) -> None:
        """GB-only datasets silently drop NI. This one does not."""
        for town in ("Belfast", "Lisburn", "Armagh", "Newry"):
            assert check_uk(town, "greenhouse").allowed, town


class TestForeignOverrideSurvivesInversion:
    """Positive-only matching has a fatal hole, so the country check runs FIRST."""

    def test_cambridge_usa_blocked_despite_being_a_uk_town_name(self) -> None:
        v = check_uk("Cambridge (USA)", "greenhouse")
        assert not v.allowed and v.reason == "foreign_location"

    @pytest.mark.parametrize("loc", ["Indianapolis, IN, USA", "Darmstadt, DE",
                                     "Boston, MA", "Toronto, CA"])
    def test_iso_codes_and_names_both_block(self, loc: str) -> None:
        """Ads write 'USA' and 'DE' far more often than the full country name.
        Without the ISO codes, "Indianapolis, IN, USA" fell through to the
        judgement bucket instead of being blocked outright."""
        assert not check_uk(loc, "greenhouse").allowed, loc

    def test_dual_site_needs_an_unambiguous_uk_signal(self) -> None:
        """The UK has a hamlet called Sydney, so a bare gazetteer hit let
        "Sydney, Australia" through as a dual-site posting. London is not
        ambiguous, so "London, New York" still survives."""
        assert check_uk("London, New York", "greenhouse").allowed
        assert not check_uk("Sydney, Australia", "greenhouse").allowed


class TestComputedAmbiguity:
    """Boston/Cambridge/Perth collide with bigger foreign twins. That set is
    DERIVED from population data at build time — hand-listing it would repeat
    the exact mistake this design exists to remove."""

    def test_ambiguous_name_needs_corroboration_on_a_global_source(self) -> None:
        v = check_uk("Boston", "greenhouse")
        assert not v.allowed and v.reason == "ambiguous_place_name_unconfirmed"

    def test_uk_evidence_in_the_ad_resolves_it(self) -> None:
        v = check_uk("Boston", "greenhouse",
                     description="Salary £45,000. NHS pension.")
        assert v.allowed and v.reason == "ambiguous_name_with_uk_evidence"

    def test_uk_native_source_resolves_it_too(self) -> None:
        assert check_uk("Boston", "reed").allowed

    def test_london_is_not_ambiguous(self) -> None:
        """London, Ontario is ~4% the size, so the computation leaves it alone."""
        v = check_uk("London", "greenhouse")
        assert v.allowed and v.reason == "uk_gazetteer"


class TestWhoSaidItDecides:
    """Source trust survives inversion: 52k places still miss hamlets,
    business parks and new estates."""

    def test_real_uk_town_now_recognised_by_data_not_a_hand_list(self) -> None:
        """Wellingborough used to survive only because teaching_vacancies is
        trusted. The gazetteer knows the town itself, so it is allowed from
        ANY source — which is the whole point of inverting."""
        assert check_uk("Wellingborough", "teaching_vacancies").allowed
        v = check_uk("Wellingborough", "greenhouse")
        assert v.allowed and v.reason == "uk_gazetteer"

    def test_source_trust_still_carries_places_the_data_misses(self) -> None:
        v = check_uk("Some Unlisted Business Park", "teaching_vacancies")
        assert v.allowed and v.reason == "uk_native_source"

    def test_unrecognised_place_blocked_from_global_source(self) -> None:
        v = check_uk("Branchburg", "workday")
        assert not v.allowed and v.reason == "unverified_location_on_global_source"

    def test_devitjobs_is_uk_native(self) -> None:
        """devitjobs.UK — the endpoint is the UK site. Misclassifying it as
        global blocked 1,409 real UK jobs in the first dry run."""
        assert "devitjobs" in UK_NATIVE_SOURCES
        assert check_uk("Telford", "devitjobs").allowed

    def test_unknown_source_defaults_to_strict(self) -> None:
        """A newly added source must opt IN to trust, never inherit it."""
        assert not check_uk("Zzyzx Junction", "brand_new_source").allowed


class TestPostcode:
    def test_full_uk_postcode_is_decisive(self) -> None:
        assert check_uk("SW1A 1AA", "greenhouse").allowed

    @pytest.mark.parametrize("near_miss", ["M5V 2T6", "1234 AB"])
    def test_foreign_postcodes_do_not_match(self, near_miss: str) -> None:
        """Canadian and Dutch codes must not be read as UK ones."""
        from src.services.uk_gate import _POSTCODE

        assert not _POSTCODE.search(near_miss)


class TestEvidenceInTheAd:
    """Workday posts multi-site roles with a placeholder location ("4
    Locations"). 14 such jobs named a UK city in the body — real UK jobs the
    first version dropped."""

    def test_uk_city_in_body_rescues_placeholder_location(self) -> None:
        v = check_uk("4 Locations", "workday",
                     description="This role is based in our Manchester office.")
        assert v.allowed and v.reason == "uk_evidence_in_ad"

    def test_gbp_symbol_is_uk_evidence(self) -> None:
        assert check_uk("2 Locations", "greenhouse",
                        description="Salary £65,000 - £80,000").allowed

    def test_no_evidence_stays_blocked(self) -> None:
        assert not check_uk("4 Locations", "workday",
                            description="Great team, great benefits.").allowed


class TestMessyFreeText:
    def test_multi_location_string_splits(self) -> None:
        """"Barcelona; Madrid" is two foreign segments, not one odd token."""
        assert not check_uk("Barcelona; Madrid", "greenhouse").allowed

    def test_milwaukee_does_not_match_uk_substring(self) -> None:
        """Word-boundary matching: 'uk' must not match inside 'Milwaukee'."""
        assert not check_uk("Milwaukee", "greenhouse").allowed

    def test_accents_normalise(self) -> None:
        assert not check_uk("München, DE", "greenhouse").allowed

    def test_town_with_trailing_noise_still_resolves(self) -> None:
        """Longest-first n-gram matching inside a segment."""
        assert check_uk("Newcastle upon Tyne hybrid working", "greenhouse").allowed


class TestTheGazetteerActuallyShipsToProduction:
    """Issue #260. The gate logic was CORRECT and still let foreign jobs in.

    Measured in prod on 2026-08-11 (worker deployment e6c87061, the 04:06 UTC
    refresh_catalog run over 6,087 fetched jobs): "UK gate blocked 372 job(s)
    before storage: no_location_on_global_source=46,
    remote_restricted_to_other_region=1,
    unverified_location_on_global_source=325". Not ONE `foreign_location` and
    not ONE `uk_gazetteer` verdict in the whole run — impossible unless both
    data sets were EMPTY.

    They were. `pip install .` installs only the `src*` packages, so
    `backend/data/` never reached site-packages, and `_gazetteer()` degrades to
    empty frozensets when the directory is missing. The same run logged
    "Permission denied: '/usr/local/lib/python3.12/site-packages/data'" — that
    is the exact directory `_DATA` resolved to in the container.

    Every test above passes with the files sitting on a dev disk, so the whole
    suite stayed green while production ran with no gazetteer at all. These
    three tests are the ones that would have caught it: they assert the RUNTIME
    path, the packaging that fills it, and that an empty load is LOUD.
    """

    def test_data_sits_inside_the_installed_package_tree(self) -> None:
        import src.services.uk_gate as gate

        for name in ("uk_places.txt", "foreign_admin.txt", "ambiguous.txt"):
            path = gate._DATA / name
            assert path.exists(), (
                f"{name} is not under the package tree ({gate._DATA}). Anything "
                "outside `src/` is not installed by `pip install .`, so the gate "
                "ships blind — exactly issue #260."
            )
            assert path.stat().st_size > 1024, f"{name} is present but empty"

    def test_packaging_config_ships_the_data_with_the_wheel(self) -> None:
        """Living under `src/` is necessary but not sufficient — setuptools
        copies non-.py files only when they are declared as package data."""
        import src.services.uk_gate as gate

        pyproject = (gate._DATA.parent.parent.parent / "pyproject.toml").read_text(
            encoding="utf-8"
        )
        assert "[tool.setuptools.package-data]" in pyproject
        assert "data/uk_gazetteer" in pyproject

    def test_a_missing_gazetteer_is_loud(self, tmp_path, caplog) -> None:
        """Graceful degradation is right — a lost data file must not empty the
        catalog. SILENT degradation is what cost four days."""
        import logging

        import src.services.uk_gate as gate

        original = gate._DATA
        gate._gazetteer.cache_clear()
        try:
            gate._DATA = tmp_path / "absent"
            with caplog.at_level(logging.ERROR):
                places, foreign, ambiguous = gate._gazetteer()
            assert not places and not foreign and not ambiguous
            assert any(
                "gazetteer" in r.getMessage().lower() for r in caplog.records
            ), "an empty gazetteer must scream in the logs, not pass silently"
        finally:
            gate._DATA = original
            gate._gazetteer.cache_clear()


class TestProdRowsThatReachedTheCatalog:
    """Real rows found live in `jobs` on 2026-08-11 (ids 65415-65579, plus
    54746 from 2026-08-08) — every one stored AFTER the gate merged on
    2026-08-07. They are the acceptance test for the packaging fix."""

    @pytest.mark.parametrize("source,loc", [
        ("arbeitnow", "Germany (Remote)"),
        ("greenhouse", "Brazil (Remote)"),
        ("smartrecruiters", "São Paulo, br"),
        ("ashby", "India - Remote"),
        ("greenhouse", "Warsaw"),
        ("workday", "Cambridge (USA)"),
    ])
    def test_row_is_refused(self, source: str, loc: str) -> None:
        assert not check_uk(loc, source).allowed, loc


class TestRemoteGluedToACountryName:
    """The second hole, still open even with the data present: the country
    override matched a WHOLE segment, so "US-Remote" (one segment, "us remote"
    after normalisation) missed it and step 5's remote branch waved it through.

    Dry-run over the 4,647 live ACTIVE rows (2026-08-11, rule #30): stripping
    the remote words and re-testing the remainder flips 28 rows ALLOW->BLOCK —
    every one a US-only remote from greenhouse — and 0 UK rows. 11 more rows
    keep their allow through the dual-site escape.
    """

    @pytest.mark.parametrize("loc", [
        "US-Remote", "US Remote", "US remote", "Remote US", "Remote India",
        "Chicago, US-Remote", "US-Remote, Atlanta, Chicago",
        "US-Chicago; US-Atlanta; US-Remote; Canada-Toronto; Canada-Remote",
    ])
    def test_country_glued_to_remote_is_refused(self, loc: str) -> None:
        v = check_uk(loc, "greenhouse")
        assert not v.allowed and v.reason == "foreign_location", loc

    @pytest.mark.parametrize("loc", [
        "Remote", "REMOTE", "Flexible / Remote", "Anywhere in the World",
        "Work from home",
    ])
    def test_unqualified_remote_is_still_allowed(self, loc: str) -> None:
        """56 'Anywhere in the World' rows are legitimately reachable from the
        UK. Tightening must not cost them."""
        assert check_uk(loc, "weworkremotely").allowed, loc

    @pytest.mark.parametrize("loc", [
        "London; Remote", "Remote - Edinburgh", "Manchester, Remote",
        "London, US-Remote",
    ])
    def test_uk_place_beside_remote_is_still_allowed(self, loc: str) -> None:
        assert check_uk(loc, "greenhouse").allowed, loc

    def test_an_ordinary_place_is_decided_exactly_as_before(self) -> None:
        """The strip only fires on a segment that really contains a remote
        term, so nothing else changes verdict."""
        assert check_uk("Shoreham-by-Sea", "reed").allowed
        assert check_uk("Home Park", "reed").allowed
