"""What counts as a GitHub handle — decided by a pure function, tested fast.

WHY THIS FILE EXISTS. These cases started life as a parametrized ROUTE test in
test_profile_upload.py. That file's ``api`` fixture builds a fresh Postgres
schema and runs every migration PER TEST (~50s), so four cases cost ~200s to
assert what a regex decides in microseconds — and it quadrupled the targeted
gate (measured: a 40-minute run). Testing a pure function through an HTTP route
plus a database is the expensive way to be no more correct.

WHAT IT GUARDS (found on a live profile 2026-08-08). A real user's GitHub enrich
silently did nothing: ``github_connected_at`` stamped, ``github_username`` empty,
0 repos. The likely input was his PORTFOLIO url — a CV lists
``<user>.github.io`` under "Portfolio", and that is NOT a username. The route now
rejects an unusable handle with a 400 instead of pretending it worked; this file
pins exactly which strings are usable.
"""
from __future__ import annotations

import pytest

from src.services.profile.github_enricher import normalize_github_username


class TestAcceptsWhatPeopleActuallyPaste:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Ranjith36963", "Ranjith36963"),
            ("ranjith36963", "ranjith36963"),
            ("@Ranjith36963", "Ranjith36963"),
            ("  Ranjith36963  ", "Ranjith36963"),
            ("https://github.com/Ranjith36963", "Ranjith36963"),
            ("http://github.com/Ranjith36963", "Ranjith36963"),
            ("github.com/Ranjith36963", "Ranjith36963"),
            ("www.github.com/Ranjith36963", "Ranjith36963"),
            ("github.com/Ranjith36963/job360", "Ranjith36963"),
            ("www.github.com/Ranjith36963?tab=repositories", "Ranjith36963"),
            ("Ranjith36963/", "Ranjith36963"),
        ],
    )
    def test_reduces_to_the_bare_handle(self, raw: str, expected: str) -> None:
        assert normalize_github_username(raw) == expected


class TestRejectsWhatIsNotAHandle:
    @pytest.mark.parametrize(
        "raw",
        [
            "ranjith36963.github.io",          # a PORTFOLIO url — the real trap
            "https://ranjith36963.github.io",  # ditto, with scheme
            "https:",                          # junk that once got STORED verbatim
            "   ",
            "",
            "-leading-dash",                   # GitHub handles cannot start with -
            "has space",
            "a" * 40,                          # over GitHub's 39-char limit
        ],
    )
    def test_returns_empty_so_no_caller_can_persist_poison(self, raw: str) -> None:
        assert normalize_github_username(raw) == ""

    def test_a_non_string_is_not_a_handle(self) -> None:
        assert normalize_github_username(None) == ""  # type: ignore[arg-type]
        assert normalize_github_username(123) == ""  # type: ignore[arg-type]
