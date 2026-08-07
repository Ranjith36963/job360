

class TestVisaSponsorshipIsParsedNotCoerced:
    """`hasVisaSponsorship` arrives as the STRING "Yes"/"No", and bool("No")
    is True in Python. That one-word bug marked EVERY devitjobs row as
    sponsoring and wrote "Visa sponsorship available" into the description we
    compose ourselves.

    Measured in prod 2026-08-07: 1,977 active rows claimed sponsorship while
    the API says exactly 2 of 2,377 offer it. A user who needs sponsorship was
    shown - and ranked UP on - jobs that explicitly refuse it, and the visa
    text detector read our own fabricated sentence back as evidence.
    """

    @staticmethod
    def _parse(raw: object) -> bool:
        return str(raw or "").strip().lower() in ("yes", "true", "1")

    def test_the_string_no_is_falsey(self) -> None:
        assert self._parse("No") is False
        assert bool("No") is True, "the trap itself: a non-empty string is truthy"

    def test_the_string_yes_is_truthy(self) -> None:
        assert self._parse("Yes") is True

    def test_absent_or_empty_is_false(self) -> None:
        for raw in (None, "", "  "):
            assert self._parse(raw) is False

    def test_real_booleans_still_work(self) -> None:
        assert self._parse(True) is True
        assert self._parse(False) is False
