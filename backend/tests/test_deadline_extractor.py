"""Tests for the application-deadline extractor.

All tests inject `today_iso` so results are deterministic and never depend on
the real system clock.  The reference today is 2026-06-14.
"""

import pytest

from src.services.deadline import extract_deadline

TODAY = "2026-06-14"
# A date clearly in the future relative to TODAY
FUTURE = "2026-09-30"
# A date in the past relative to TODAY
PAST = "2025-12-31"
# Far future (more than 2 years out)
FAR_FUTURE = "2030-01-01"


# ---------------------------------------------------------------------------
# Positive cases — each keyword + each date format
# ---------------------------------------------------------------------------


class TestKeywordVariants:
    """Each supported keyword should trigger extraction."""

    def test_closing_date(self):
        text = "Closing date: 30 September 2026"
        result = extract_deadline(text, today_iso=TODAY)
        assert result == ("2026-09-30", "description")

    def test_apply_by(self):
        text = "Apply by 30 September 2026 to be considered."
        result = extract_deadline(text, today_iso=TODAY)
        assert result == ("2026-09-30", "description")

    def test_application_deadline(self):
        text = "Application deadline: 30 Sep 2026"
        result = extract_deadline(text, today_iso=TODAY)
        assert result == ("2026-09-30", "description")

    def test_deadline_colon(self):
        text = "Deadline: 30/09/2026"
        result = extract_deadline(text, today_iso=TODAY)
        assert result == ("2026-09-30", "description")

    def test_applications_close_on(self):
        text = "Applications close on 30 September 2026."
        result = extract_deadline(text, today_iso=TODAY)
        assert result == ("2026-09-30", "description")

    def test_applications_close(self):
        text = "Applications close 30 September 2026."
        result = extract_deadline(text, today_iso=TODAY)
        assert result == ("2026-09-30", "description")

    def test_close_date(self):
        text = "Close date: 30 Sep 2026"
        result = extract_deadline(text, today_iso=TODAY)
        assert result == ("2026-09-30", "description")

    def test_last_date_to_apply(self):
        text = "Last date to apply: 30 September 2026"
        result = extract_deadline(text, today_iso=TODAY)
        assert result == ("2026-09-30", "description")

    def test_apply_before(self):
        text = "Apply before 30 September 2026."
        result = extract_deadline(text, today_iso=TODAY)
        assert result == ("2026-09-30", "description")

    def test_closes_on(self):
        text = "This role closes on 2026-09-30."
        result = extract_deadline(text, today_iso=TODAY)
        assert result == ("2026-09-30", "description")


class TestDateFormats:
    """Each supported date format should parse to the same ISO output."""

    def test_full_month_day_first(self):
        # "30 September 2026"
        result = extract_deadline("Deadline: 30 September 2026", today_iso=TODAY)
        assert result == ("2026-09-30", "description")

    def test_abbreviated_month_day_first(self):
        # "30 Sep 2026"
        result = extract_deadline("Deadline: 30 Sep 2026", today_iso=TODAY)
        assert result == ("2026-09-30", "description")

    def test_slash_numeric_uk(self):
        # "30/09/2026" — UK day-first
        result = extract_deadline("Deadline: 30/09/2026", today_iso=TODAY)
        assert result == ("2026-09-30", "description")

    def test_dash_numeric_uk(self):
        # "30-09-2026" — UK day-first with dashes
        result = extract_deadline("Deadline: 30-09-2026", today_iso=TODAY)
        assert result == ("2026-09-30", "description")

    def test_iso_date(self):
        # "2026-09-30" — ISO 8601
        result = extract_deadline("Deadline: 2026-09-30", today_iso=TODAY)
        assert result == ("2026-09-30", "description")

    def test_us_style_month_day_year(self):
        # "September 30, 2026"
        result = extract_deadline("Deadline: September 30, 2026", today_iso=TODAY)
        assert result == ("2026-09-30", "description")

    def test_abbreviated_month_day_year(self):
        # "Sep 30, 2026"
        result = extract_deadline("Deadline: Sep 30, 2026", today_iso=TODAY)
        assert result == ("2026-09-30", "description")

    def test_case_insensitive_keyword(self):
        result = extract_deadline("CLOSING DATE: 30 September 2026", today_iso=TODAY)
        assert result == ("2026-09-30", "description")

    def test_case_insensitive_month(self):
        result = extract_deadline("Deadline: 30 SEPTEMBER 2026", today_iso=TODAY)
        assert result == ("2026-09-30", "description")


class TestFirstMatchWins:
    def test_first_keyword_returned(self):
        text = (
            "Closing date: 30 September 2026. "
            "Also apply by 31 October 2026 for the late round."
        )
        result = extract_deadline(text, today_iso=TODAY)
        assert result == ("2026-09-30", "description")


# ---------------------------------------------------------------------------
# Negative cases — must all return None
# ---------------------------------------------------------------------------


class TestNegativeCases:
    def test_no_keyword_no_date(self):
        assert extract_deadline("Great opportunity to join our team.", today_iso=TODAY) is None

    def test_date_without_keyword(self):
        # A stray date in the text without a keyword anchor should be ignored.
        assert extract_deadline("We were founded on 01/01/2010.", today_iso=TODAY) is None

    def test_keyword_but_no_date(self):
        assert extract_deadline("Please check our website for the deadline.", today_iso=TODAY) is None

    def test_keyword_with_garbage_date(self):
        assert extract_deadline("Deadline: not yet decided", today_iso=TODAY) is None

    def test_past_date_rejected(self):
        # 2025-12-31 is before today 2026-06-14 → None
        assert extract_deadline("Deadline: 31 December 2025", today_iso=TODAY) is None

    def test_today_date_rejected(self):
        # Deadline ON today counts as past (already closed).
        assert extract_deadline("Deadline: 14 June 2026", today_iso=TODAY) is None

    def test_far_future_rejected(self):
        # 2030-01-01 is more than 2 years after 2026-06-14 → None
        assert extract_deadline("Deadline: 01 January 2030", today_iso=TODAY) is None

    def test_none_input(self):
        assert extract_deadline(None, today_iso=TODAY) is None  # type: ignore[arg-type]

    def test_empty_string(self):
        assert extract_deadline("", today_iso=TODAY) is None

    def test_invalid_calendar_date_rejected(self):
        # Feb 30 does not exist
        assert extract_deadline("Deadline: 30/02/2026", today_iso=TODAY) is None

    def test_ambiguous_numeric_both_small_accepted_as_uk(self):
        # 05/06/2026 — both ≤ 12, accepted as UK (day=5, month=6 → June 5)
        result = extract_deadline("Deadline: 05/06/2026", today_iso=TODAY)
        # June 5 2026 is in the future relative to June 14 — actually June 5 < June 14, so PAST!
        # Should be None (past).
        assert result is None

    def test_keyword_far_from_date(self):
        # Gap > _LOOKAHEAD chars between keyword and date → no match.
        long_gap = "x" * 100
        text = f"Deadline:{long_gap}30 September 2026"
        assert extract_deadline(text, today_iso=TODAY) is None

    def test_whitespace_only(self):
        assert extract_deadline("   ", today_iso=TODAY) is None


class TestEdgeCases:
    def test_year_boundary_exactly_two_years_allowed(self):
        # Exactly 2 years from 2026-06-14 → 2028-06-14 (NOT > max_future).
        result = extract_deadline("Deadline: 14 June 2028", today_iso=TODAY)
        assert result == ("2028-06-14", "description")

    def test_year_boundary_one_day_over_rejected(self):
        # 2028-06-15 is one day beyond max_future → None.
        result = extract_deadline("Deadline: 15 June 2028", today_iso=TODAY)
        assert result is None

    def test_tomorrow_accepted(self):
        # One day in the future — the minimum valid deadline.
        result = extract_deadline("Closing date: 15 June 2026", today_iso=TODAY)
        assert result == ("2026-06-15", "description")

    def test_dash_separator_keyword(self):
        result = extract_deadline("Closing date - 30 September 2026", today_iso=TODAY)
        assert result == ("2026-09-30", "description")

    def test_no_separator_keyword(self):
        result = extract_deadline("Deadline 30 September 2026", today_iso=TODAY)
        assert result == ("2026-09-30", "description")

    def test_real_clock_used_when_no_today_iso(self):
        # When today_iso is omitted, extract_deadline uses date.today().
        # We can't predict the result, but it must not crash and must return
        # None or a string tuple.
        result = extract_deadline("Deadline: 30 September 2026")
        assert result is None or (isinstance(result, tuple) and len(result) == 2)
