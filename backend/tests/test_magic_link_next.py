"""W-01 — the magic link must carry ``?next=`` and must never carry an open redirect.

Why this file exists (wiring.md W-01): the emailed sign-in link used to carry only a
token. So when our own digest email linked a user to /jobs/123 and his session had
expired, he was bounced to /login, signed in by magic link — the DEFAULT form — and
landed on /dashboard. The job was gone. Our own email broke at our own front door.

Why the validation lives in Python and not only in the browser: the link is BUILT
SERVER-SIDE (services/auth/magic_link.py). ``next`` is therefore a server input. A
frontend-only check is bypassed by POSTing the API directly, and an unvalidated value
here is an open redirect *inside an email we send* — the worst place to have one, because
the recipient trusts the sender.

Rule #21 (value-presence, not schema-presence): these assert the real emitted URL, not
that a parameter was accepted. A test that only asserted "the route returned 204" would
pass against a build that silently dropped ``next`` on the floor.
"""

from __future__ import annotations

import pytest

from src.services.auth.magic_link import _build_magic_link_email, safe_next_path

ORIGIN = "https://job360.uk"


# ── the validator, on its own ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "good",
    [
        "/jobs/123",
        "/pipeline",
        "/profile?tab=cv",
        "/jobs/123#skills",
        "/",
    ],
)
def test_safe_next_allows_same_site_paths(good: str) -> None:
    assert safe_next_path(good) == good


@pytest.mark.parametrize(
    ("hostile", "why"),
    [
        ("https://evil.com", "absolute URL"),
        ("http://evil.com", "absolute URL"),
        ("//evil.com", "protocol-relative — the browser supplies the scheme"),
        (r"/\evil.com", r"backslash: WHATWG says browsers read \ as /, so this becomes //evil.com"),
        (r"/\/evil.com", "mixed slash/backslash variant of the same trick"),
        ("javascript:alert(1)", "scheme injection"),
        ("data:text/html,<script>", "scheme injection"),
        ("jobs/123", "relative, no leading slash — resolves against whatever page it lands on"),
        ("", "empty"),
        (None, "absent"),
        ("/jobs\r\nSet-Cookie: x=y", "CRLF — header/URL injection into the email we send"),
        ("/jobs\nX", "bare LF"),
        ("/jobs\x00", "NUL byte"),
        ("/jobs\tX", "tab"),
        ("/" + "a" * 4096, "absurd length"),
    ],
)
def test_safe_next_rejects_everything_hostile(hostile, why: str) -> None:
    assert safe_next_path(hostile) is None, f"accepted a hostile next ({why})"


def test_safe_next_rejects_are_a_fallback_not_a_crash() -> None:
    """A bad ``next`` must degrade to 'no next', never raise — the sign-in must still work."""
    assert safe_next_path("https://evil.com") is None
    assert safe_next_path("/jobs/1") == "/jobs/1"


# ── the emitted link ─────────────────────────────────────────────────────────


def _link_from(text_body: str) -> str:
    """Pull the sign-in URL out of the plain-text email body."""
    for line in text_body.splitlines():
        if line.startswith(ORIGIN):
            return line
    raise AssertionError(f"no sign-in link found in body:\n{text_body}")


def test_link_carries_next_when_it_is_safe() -> None:
    _subject, text, html_body = _build_magic_link_email(
        to_email="a@example.com",
        raw_token="tok_abc123",
        frontend_origin=ORIGIN,
        next_path="/jobs/123",
    )
    link = _link_from(text)
    assert "token=tok_abc123" in link
    # URL-encoded so the path can never break out of the query string.
    assert "next=%2Fjobs%2F123" in link, f"next missing or unencoded in: {link}"
    # The HTML half must carry it too — some clients only render HTML.
    assert "next=%2Fjobs%2F123" in html_body


def test_link_omits_next_entirely_when_hostile() -> None:
    """A hostile next is dropped, and the link still works as a plain sign-in."""
    _subject, text, html_body = _build_magic_link_email(
        to_email="a@example.com",
        raw_token="tok_abc123",
        frontend_origin=ORIGIN,
        next_path="https://evil.com",
    )
    link = _link_from(text)
    assert "next=" not in link, f"hostile next survived into the email: {link}"
    assert "evil.com" not in text
    assert "evil.com" not in html_body
    assert "token=tok_abc123" in link


def test_link_is_unchanged_when_no_next_is_given() -> None:
    """The old behaviour is exactly preserved for every caller that passes nothing."""
    _subject, text, _html = _build_magic_link_email(
        to_email="a@example.com",
        raw_token="tok_abc123",
        frontend_origin=ORIGIN,
    )
    link = _link_from(text)
    assert link == f"{ORIGIN}/auth/magic?token=tok_abc123"


# ── the dev-only link echo ───────────────────────────────────────────────────


def test_dev_echo_is_silent_unless_explicitly_switched_on(monkeypatch, caplog) -> None:
    """The default must be OFF. A sign-in link in a log file IS a login.

    This is the test that matters: the failure mode is not "it does not work",
    it is "it works in production", so the guard is what gets tested.
    """
    from src.services.auth.magic_link import _dev_echo_link

    monkeypatch.delenv("MAGIC_LINK_DEV_ECHO", raising=False)
    with caplog.at_level("WARNING"):
        _dev_echo_link(f"Click this:\n\n{ORIGIN}/auth/magic?token=secret123\n")
    assert "secret123" not in caplog.text, "sign-in token leaked into the log by default"


@pytest.mark.parametrize("value", ["0", "true", "yes", "", "TRUE", "1 "])
def test_dev_echo_only_accepts_exactly_one(monkeypatch, caplog, value: str) -> None:
    """Anything other than exactly "1" leaves it off — no truthy-string surprises."""
    from src.services.auth.magic_link import _dev_echo_link

    monkeypatch.setenv("MAGIC_LINK_DEV_ECHO", value)
    with caplog.at_level("WARNING"):
        _dev_echo_link(f"Click this:\n\n{ORIGIN}/auth/magic?token=secret123\n")
    assert "secret123" not in caplog.text, f"echo fired for MAGIC_LINK_DEV_ECHO={value!r}"


def test_dev_echo_prints_the_link_when_switched_on(monkeypatch, caplog) -> None:
    from src.services.auth.magic_link import _dev_echo_link

    monkeypatch.setenv("MAGIC_LINK_DEV_ECHO", "1")
    with caplog.at_level("WARNING"):
        _dev_echo_link(f"Click this:\n\n{ORIGIN}/auth/magic?token=secret123&next=%2Fjobs%2F9\n")
    assert f"{ORIGIN}/auth/magic?token=secret123&next=%2Fjobs%2F9" in caplog.text


def test_crlf_in_next_never_reaches_the_email() -> None:
    """Guard on the specific injection this file exists to stop."""
    _subject, text, html_body = _build_magic_link_email(
        to_email="a@example.com",
        raw_token="tok_abc123",
        frontend_origin=ORIGIN,
        next_path="/jobs\r\nBcc: attacker@evil.com",
    )
    assert "attacker@evil.com" not in text
    assert "attacker@evil.com" not in html_body
    assert "Bcc" not in text
