"""W-23 — one-click unsubscribe.

The digest had no unsubscribe link at all; the only exit was to log in and delete the
channel. That is a deliverability problem before it is a legal one: a recipient who
cannot find the link presses "spam" instead, and that is the worst signal a sending
domain can collect.

The tests that matter are the forgery ones. A token that turns notifications off for
whoever holds it must not be guessable, and must not be accepted in a mangled form —
email clients wrap, truncate and re-case URLs, and "close enough" must fail closed.
"""

from __future__ import annotations

import pytest

from src.services.notifications.unsubscribe import (
    make_token,
    unsubscribe_line,
    verify_token,
)

USER = "11111111-2222-3333-4444-555555555555"


def test_a_token_round_trips_to_its_own_user() -> None:
    assert verify_token(make_token(USER)) == USER


def test_each_user_gets_a_different_token() -> None:
    other = "99999999-8888-7777-6666-555555555555"
    assert make_token(USER) != make_token(other)
    assert verify_token(make_token(other)) == other


@pytest.mark.parametrize(
    ("bad", "why"),
    [
        ("", "empty"),
        (None, "absent"),
        ("no-dot-at-all", "not a token shape"),
        (f"{USER}.", "signature stripped"),
        (f".{'a' * 32}", "user id stripped"),
        (f"{USER}.{'a' * 32}", "forged signature"),
        (f"{USER}.{'A' * 32}", "forged, upper case"),
        ("other-user." + make_token(USER).split(".")[1], "someone else's signature reused"),
    ],
)
def test_a_token_that_is_not_ours_is_refused(bad, why: str) -> None:
    assert verify_token(bad) is None, f"accepted a bad token ({why})"


def test_swapping_the_user_id_invalidates_the_signature() -> None:
    """The signature must be OVER the user id, not merely next to it.

    A token that authorises "turn notifications off" would otherwise let anyone
    silence any account by editing the id in the URL.
    """
    victim = "victim-user-id"
    forged = f"{victim}.{make_token(USER).split('.')[1]}"
    assert verify_token(forged) is None


def test_a_truncated_token_is_refused() -> None:
    """Email clients wrap and truncate long URLs. Close enough must fail closed."""
    token = make_token(USER)
    assert verify_token(token[:-1]) is None
    assert verify_token(token[:-8]) is None


def test_the_line_says_plainly_what_it_does() -> None:
    """"Manage preferences" hides the action; a recipient who cannot find the exit
    presses spam instead."""
    line = unsubscribe_line("https://job360.uk", USER)
    assert "Stop these emails" in line
    assert "https://job360.uk/unsubscribe?token=" in line
    assert verify_token(line.rsplit("token=", 1)[1]) == USER


def test_the_line_survives_a_base_url_with_a_trailing_slash() -> None:
    line = unsubscribe_line("https://job360.uk/", USER)
    assert "job360.uk//unsubscribe" not in line
