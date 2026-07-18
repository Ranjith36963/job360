"""ReDoS regression guard for the email regexes (CodeQL py/polynomial-redos).

Before the fix, ``channels._EMAIL_RE`` was ``^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$``:
``.`` matched inside BOTH domain classes, so a non-matching tail gave the engine
O(n) ways to split the domain — quadratic backtracking. Measured on this repo:
2 000 chars = 0.18 s, 4 000 = 0.65 s, 8 000 = 3.38 s (doubling input quadrupled
time). An authenticated user could pin an API worker with one POST /channels.

``linkedin_parser._EMAIL_RE`` had the same shape and a WORSE input surface — it
runs via ``finditer`` over uploaded CV / LinkedIn text.

These tests assert (a) the regexes still accept/reject correctly, and (b) a
pathological payload completes fast. The timing bound is deliberately loose
(1 s vs the 3.4 s the old regex took at this size) so it flags a genuine
complexity regression, not CI jitter.
"""

from __future__ import annotations

import time

import pytest

from src.api.routes.channels import _EMAIL_RE as CHANNEL_EMAIL_RE
from src.services.profile.linkedin_parser import _EMAIL_RE as CV_EMAIL_RE

# Non-matching tail (trailing space) forces full backtracking on the old regex.
_PATHOLOGICAL = "a@" + "b." * 8000 + " "
_MAX_SECONDS = 1.0


@pytest.mark.real_sleep  # measures wall-clock; conftest mocks asyncio.sleep, not time
def test_channel_email_regex_is_not_quadratic():
    start = time.perf_counter()
    CHANNEL_EMAIL_RE.match(_PATHOLOGICAL)
    elapsed = time.perf_counter() - start
    assert elapsed < _MAX_SECONDS, (
        f"channels._EMAIL_RE took {elapsed:.2f}s on an 8k payload — "
        "ReDoS regression (the vulnerable version took ~3.4s)"
    )


@pytest.mark.real_sleep
def test_cv_email_regex_is_not_quadratic():
    start = time.perf_counter()
    list(CV_EMAIL_RE.finditer(_PATHOLOGICAL))
    elapsed = time.perf_counter() - start
    assert elapsed < _MAX_SECONDS, (
        f"linkedin_parser._EMAIL_RE took {elapsed:.2f}s on an 8k payload — "
        "ReDoS regression (uploaded-CV input surface)"
    )


@pytest.mark.parametrize(
    "email",
    ["a@b.co", "user@example.com", "first.last@sub.example.co.uk", "x+tag@mail-host.io"],
)
def test_channel_regex_still_accepts_valid_emails(email):
    assert CHANNEL_EMAIL_RE.match(email), f"{email} should be accepted"


@pytest.mark.parametrize(
    "bad",
    ["no-at-sign", "a@b", "a@@b.co", "a b@c.co", "@b.co", "a@.co"],
)
def test_channel_regex_still_rejects_invalid(bad):
    assert not CHANNEL_EMAIL_RE.match(bad), f"{bad} should be rejected"


def test_cv_regex_still_extracts_emails_from_prose():
    text = "Contact me at first.last@sub.example.co.uk or backup@mail-host.io please."
    found = [m.group(0) for m in CV_EMAIL_RE.finditer(text)]
    assert "first.last@sub.example.co.uk" in found
    assert "backup@mail-host.io" in found
