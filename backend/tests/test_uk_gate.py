"""The UK gate — one door every job passes before storage.

Job360 is a UK-market product; a Warsaw job in a user's feed is a product
fault, not a ranking preference. These tests pin the decisions that were
derived from measuring the LIVE catalog on 2026-08-07, including the two
false-drop traps the dry run caught before this shipped.
"""
from __future__ import annotations

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
        v = check_uk("", "greenhouse",
                     description="Fully remote. Must be US based.")
        assert not v.allowed


class TestWhoSaidItDecides:
    """The measured core: UK_TERMS holds 26 cities, so hundreds of real UK
    towns are unrecognised. Blanket-rejecting them would have deleted 171
    teaching_vacancies rows (UK schools) and 42 reed rows."""

    def test_unknown_town_kept_from_uk_native_source(self) -> None:
        v = check_uk("Wellingborough", "teaching_vacancies")
        assert v.allowed and v.reason == "uk_native_source"

    def test_unknown_town_blocked_from_global_source(self) -> None:
        v = check_uk("Branchburg", "workday")
        assert not v.allowed and v.reason == "unverified_location_on_global_source"

    def test_devitjobs_is_uk_native(self) -> None:
        """devitjobs.UK — the endpoint is the UK site. Misclassifying it as
        global blocked 1,409 real UK jobs in the first dry run."""
        assert "devitjobs" in UK_NATIVE_SOURCES
        assert check_uk("Telford", "devitjobs").allowed

    def test_unknown_source_defaults_to_strict(self) -> None:
        """A newly added source must opt IN to trust, never inherit it."""
        assert not check_uk("Wellingborough", "brand_new_source").allowed


class TestEvidenceInTheAd:
    """Workday posts multi-site roles with a placeholder location ('4
    Locations'). 14 such jobs named a UK city in the body — real UK jobs the
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


class TestAmbiguityFavoursTheUser:
    def test_dual_site_posting_including_uk_is_kept(self) -> None:
        """'London / New York' — the user can take the UK half."""
        v = check_uk("London, New York", "greenhouse")
        assert v.allowed and v.reason == "dual_site_includes_uk"

    def test_milwaukee_does_not_match_uk_substring(self) -> None:
        """Word-boundary matching: 'uk' must not match inside 'Milwaukee'."""
        assert not check_uk("Milwaukee", "greenhouse").allowed
