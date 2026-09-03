"""The mypy ratchet must not mistake "mypy never ran" for "mypy found nothing".

Found 2026-09-03: CI's ratchet step finished in under a second and printed
``OK — 0 errors`` while the same source had 80+ real errors on a dev machine.
The ratchet only counted parsable ``file:line: error:`` lines, so any run in
which mypy crashed, was missing, or rejected its config parsed as zero — the
best possible answer to a question it never asked. ``check_mypy_ran`` is the
fix; these tests are its drill (scripts/drill_registry.py: "owed").
"""
from __future__ import annotations

from scripts.mypy_ratchet import check_mypy_ran, parse

ERR = "src/foo.py:12: error: Function is missing a return type annotation  [no-untyped-def]"


def test_clean_run_is_trusted() -> None:
    assert check_mypy_ran(0, 0) is None


def test_errors_with_exit_1_are_trusted() -> None:
    assert sum(parse(ERR).values()) == 1
    assert check_mypy_ran(1, 1) is None


def test_crash_exit_2_is_refused_even_with_nothing_parsed() -> None:
    reason = check_mypy_ran(2, 0)
    assert reason is not None
    assert "exited 2" in reason


def test_mypy_missing_is_refused() -> None:
    # `python -m mypy` with no mypy installed: exit 1, a stderr line the
    # parser cannot read, zero errors counted. Must NOT pass.
    reason = check_mypy_ran(1, 0)
    assert reason is not None
    assert "no error lines were parsed" in reason


def test_parser_drift_is_refused() -> None:
    # exit 0 means mypy found nothing; parsed errors then mean our regex
    # matched something that is not an error — fail loudly, never guess.
    reason = check_mypy_ran(0, 1)
    assert reason is not None
    assert "parser drift" in reason
