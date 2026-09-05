"""Right to work — the UK fact a CV states and we never read.

Job360 is a UK-market product. Rule #30 refuses jobs the user cannot take
because of WHERE they are; rule #31 treats sponsorship as a spotlight rather
than a wall, because ``jobs.visa_flag`` conflates "says no" with "never
mentioned". Both are about the JOB side.

The USER side had nothing at all. ``needs_visa`` is a single boolean the user
ticks, and 13 of 15 preferences on the owner's live profile are untouched — so
in practice the engine knows nothing about eligibility for almost everyone.
Meanwhile UK CVs routinely say it outright: "British citizen", "Indefinite Leave
to Remain", "Graduate Route visa valid until 2027", "requires sponsorship".

So this is a stated FACT being discarded, not a missing preference — the same
shape as the seniority level the prompt already asked for and the adapter binned.

TRI-STATE, deliberately, for exactly the reason rule #31 gives: "" means the CV
never said, which is NOT the same as "needs sponsorship". Guessing here would
put a wall in front of the one candidate who most needs the door open.
"""
from __future__ import annotations

from src.services.profile.models import UserPreferences
from src.services.profile.schemas import CVSchema, cv_schema_to_cvdata


class TestTheCvsStatementSurvives:
    def test_a_stated_status_reaches_the_shelf(self) -> None:
        cv = cv_schema_to_cvdata(
            CVSchema(name="Ada", right_to_work="British citizen"), raw_text="x"
        )
        assert cv.cv_right_to_work == "British citizen"

    def test_silence_stays_silent(self) -> None:
        """Rule #31: unknown is a THIRD state. An empty string must never be
        read as "needs sponsorship" — that is the opposite fact."""
        cv = cv_schema_to_cvdata(CVSchema(name="Ada"), raw_text="x")
        assert cv.cv_right_to_work == ""

    def test_it_is_not_the_needs_visa_preference(self) -> None:
        cv = cv_schema_to_cvdata(
            CVSchema(name="Ada", right_to_work="requires sponsorship"), raw_text="x"
        )
        prefs = UserPreferences()
        # The extracted fact must not silently flip a preference the user owns.
        assert prefs.needs_visa is False
        assert cv.cv_right_to_work == "requires sponsorship"




class TestThePromptAsksForIt:
    """An unasked-for field is an empty field. The extraction prompt and the
    schema have to move together — this is the pairing that failed for
    experience_level, where the prompt asked and the adapter dropped it."""

    def test_the_cv_prompt_requests_it(self) -> None:
        from src.services.profile import cv_parser

        blob = " ".join(
            str(getattr(cv_parser, n, "")) for n in dir(cv_parser)
            if n.isupper() and isinstance(getattr(cv_parser, n, None), str)
        )
        assert "right_to_work" in blob, (
            "the CV prompt never asks for right_to_work, so the shelf can only "
            "ever be empty"
        )

    def test_the_extractor_version_moved(self) -> None:
        """A new prompt field reaches nobody unless the cost cache is
        invalidated — the exact miss that kept seven LinkedIn sections empty."""
        from src.services.profile.two_pass import EXTRACTOR_VERSION

        assert EXTRACTOR_VERSION not in ("1", "2", "3"), (
            "EXTRACTOR_VERSION still predates the right_to_work prompt; every "
            "existing user will hit the cache and never be asked"
        )
