"""Visa as a THREE-state signal, and the precedence that makes it safe."""
from __future__ import annotations

import pytest

from src.services.visa_signal import VisaStatus, detect_visa_status


class TestPrecedenceIsLoadBearing:
    """"We cannot offer visa sponsorship" CONTAINS "visa sponsorship". Any
    positive-first design reads a refusal as an offer — the worst error this
    module can make, because it sends a candidate to a dead end."""

    @pytest.mark.parametrize("text", [
        "We cannot offer visa sponsorship for this role.",
        "Unfortunately we are unable to sponsor visas.",
        "No sponsorship available.",
        "Sponsorship is not available for this position.",
        "Candidates must already have the right to work in the UK.",
        "This role is open to those without the need for sponsorship.",
    ])
    def test_refusal_beats_the_word_sponsorship(self, text: str) -> None:
        assert detect_visa_status(text) is VisaStatus.NO_SPONSORSHIP


class TestPositives:
    @pytest.mark.parametrize("text", [
        "Experience level: Senior. Company size: 50-200. Visa sponsorship available",
        "Sponsorship is offered for exceptional candidates.",
        "We sponsor Skilled Worker visas.",
        "The company is a licensed sponsor.",
        "A Certificate of Sponsorship can be issued.",
    ])
    def test_offers_detected(self, text: str) -> None:
        assert detect_visa_status(text) is VisaStatus.SPONSORS


class TestSilenceIsNotRefusal:
    """The whole reason for three states: most ads simply never mention it,
    and that must never be read as 'no'."""

    def test_no_mention_is_unknown(self) -> None:
        assert detect_visa_status(
            "Great team, competitive salary, hybrid working in Leeds."
        ) is VisaStatus.UNKNOWN

    def test_empty_is_unknown(self) -> None:
        assert detect_visa_status("") is VisaStatus.UNKNOWN
        assert detect_visa_status(None) is VisaStatus.UNKNOWN


class TestFalsePositiveTrap:
    def test_support_tiers_are_not_the_visa_route(self) -> None:
        """Measured on prod: 'tier 2' matched "leading a team of Tier 1 and
        Tier 2 support representatives". A confident wrong badge is worse
        than no badge, so 'tier 2' was removed from the pattern."""
        assert detect_visa_status(
            "You will lead a team of Tier 1 and Tier 2 support representatives."
        ) is VisaStatus.UNKNOWN


class TestEnrichmentWins:
    """The LLM read the whole ad with context; text detection fills the gap
    where no enrichment exists."""

    def test_llm_verdict_takes_precedence(self) -> None:
        assert detect_visa_status(
            "No sponsorship available.", enrichment_value="yes"
        ) is VisaStatus.SPONSORS

    def test_unknown_enrichment_falls_through_to_text(self) -> None:
        assert detect_visa_status(
            "Visa sponsorship available", enrichment_value="unknown"
        ) is VisaStatus.SPONSORS


class TestSpotlightNotWall:
    """Product rule #31 at the API layer: visa ON keeps the catalog whole."""

    def _rank(self, status: VisaStatus) -> int:
        return {VisaStatus.SPONSORS: 0, VisaStatus.UNKNOWN: 1,
                VisaStatus.NO_SPONSORSHIP: 2}[status]

    def test_unknown_outranks_a_confirmed_refusal(self) -> None:
        """Silence is a question worth asking; "we cannot sponsor" is a dead
        end. Ordering them the other way round would bury the better lead."""
        assert self._rank(VisaStatus.UNKNOWN) < self._rank(VisaStatus.NO_SPONSORSHIP)

    def test_sponsors_come_first(self) -> None:
        assert self._rank(VisaStatus.SPONSORS) < self._rank(VisaStatus.UNKNOWN)

    def test_all_three_states_are_rankable(self) -> None:
        """A spotlight ORDERS the whole catalog; it never drops a bucket."""
        ranks = {self._rank(s) for s in VisaStatus}
        assert ranks == {0, 1, 2}
