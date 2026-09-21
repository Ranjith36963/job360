"""Right to work — the UK fact a CV states and Job360 never reads for itself.

Job360 is a UK-market product. Rule #30 refused jobs the user could not take
because of WHERE they are; rule #31 treated sponsorship as a spotlight rather
than a wall, because the sourcing era's ``jobs.visa_flag`` boolean conflated
"says no" with "never mentioned". Both were about the JOB side — and both are
history: that column was dropped in migration 0043 (#571), and the job-side
signal now lives on ``applications.visa_signal`` (slice 7, migration 0042),
where the seeker's own agent records it.

The USER side had nothing at all. ``needs_visa`` is a single boolean the user
ticks, and 13 of 15 preferences on the owner's live profile are untouched — so
in practice the engine knows nothing about eligibility for almost everyone.
Meanwhile UK CVs routinely say it outright: "British citizen", "Indefinite Leave
to Remain", "Graduate Route visa valid until 2027", "requires sponsorship".

So this is a stated FACT the shelf must hold, not a missing preference.

Decision 28 (2026-09-21) removed every LLM pass from the profile pipeline —
Job360 has no brain of its own; the user's agent reads, judges and writes.
There is no CV prompt left that asks for ``right_to_work`` and nothing to
extract it from raw text: ``cv_right_to_work`` is now filled the same way any
other agent-supplied fact is, by the seeker's own agent reading the CV text
off MCP ``get_profile`` and writing the answer back with ``update_profile``
(``cv_data.cv_right_to_work`` is a declared path in
``core.settings.PROFILE_EDITABLE_PATHS``). What survives here is the shelf
itself and the invariant that guards it — not the prompt that used to fill it.

TRI-STATE, deliberately, for exactly the reason rule #31 gives: "" means
nobody has said yet, which is NOT the same as "needs sponsorship". Guessing
here — inferring it from a name, a university, anything — would put a wall in
front of the one candidate who most needs the door open. Job360 writes this
field never; only the agent does.
"""
from __future__ import annotations

from src.core.settings import PROFILE_EDITABLE_PATHS
from src.services.profile.models import CVData, UserPreferences


class TestTheShelfHoldsWhatItIsGiven:
    def test_a_stated_status_reaches_the_shelf(self) -> None:
        cv = CVData(cv_right_to_work="British citizen")
        assert cv.cv_right_to_work == "British citizen"

    def test_silence_stays_silent(self) -> None:
        """Rule #31: unknown is a THIRD state. An empty string must never be
        read as "needs sponsorship" — that is the opposite fact. Nobody
        infers this any more; a blank shelf just means nobody has answered
        yet, agent included."""
        cv = CVData()
        assert cv.cv_right_to_work == ""

    def test_it_is_not_the_needs_visa_preference(self) -> None:
        cv = CVData(cv_right_to_work="requires sponsorship")
        prefs = UserPreferences()
        # A fact the agent wrote onto the CV shelf must not silently flip a
        # preference the user owns on a different shelf.
        assert prefs.needs_visa is False
        assert cv.cv_right_to_work == "requires sponsorship"


class TestOnlyTheAgentMayWriteIt:
    """Decision 28: Job360 stores and versions this fact, it never guesses
    it. The one door that may change it is the agent's own
    ``update_profile`` call — the closed, dataclass-validated edit path in
    ``core.settings.PROFILE_EDITABLE_PATHS`` (enforced at import by
    ``services.profile.edits``). No CV-parsing code path may set this field;
    there isn't one left to do it."""

    def test_the_edit_path_is_declared(self) -> None:
        assert "cv_data.cv_right_to_work" in PROFILE_EDITABLE_PATHS, (
            "the agent has no way to correct or supply this fact if its "
            "path is missing from the editable set"
        )
