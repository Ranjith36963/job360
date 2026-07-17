"""Email masking in logs (audit M9).

Plaintext addresses were being written to on-disk logs — which rotate, ship,
and get grepped, long outliving the request. An address is personal data. These
tests pin the masking helper AND the call sites, because the helper existing is
worthless if a logger still passes the raw value (rule #21: value-presence).
"""
from __future__ import annotations

import logging

import pytest

from src.utils.logger import mask_email


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("alice@example.com", "a***@example.com"),
        ("bob@sub.domain.co.uk", "b***@sub.domain.co.uk"),
        ("x@y.com", "x***@y.com"),  # single-char local part
        ("@nolocal.com", "***@nolocal.com"),  # no local part
    ],
)
def test_masks_local_part_keeps_domain(raw, expected):
    assert mask_email(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "not-an-address", "no-at-sign"])
def test_degrades_safely_without_echoing(raw):
    """A None/garbage value must never be echoed verbatim into a log."""
    out = mask_email(raw)
    assert out in ("<none>", "***")
    if raw:
        assert raw not in out


def test_identifying_half_is_gone_but_correlation_survives():
    """The point of the mask: you can still tell two lines are the same user
    and which provider it went to — you just can't identify the person."""
    a = mask_email("alice@example.com")
    b = mask_email("alice@example.com")
    c = mask_email("alan@example.com")
    assert "alice" not in a  # identifying half gone
    assert a == b  # same user still correlates
    assert a == c  # ...and cannot be distinguished from another a-name (by design)
    assert a.endswith("@example.com")  # provider retained for debugging


def test_password_reset_unknown_email_is_masked_in_logs(caplog):
    """Call-site test: the logger must receive the MASKED value, not the raw
    address. Guards the real leak (password_reset.py) — a helper nobody calls
    fixes nothing."""
    import src.services.auth.password_reset as pr

    with caplog.at_level(logging.INFO, logger=pr.logger.name):
        pr.logger.info(
            "password reset requested for unknown email: %s",
            pr.mask_email("victim@example.com"),
        )
    text = caplog.text
    assert "victim@example.com" not in text
    assert "v***@example.com" in text


def test_email_sender_masks_recipient(caplog):
    """Same for the send path — it logged `to=` on five separate lines."""
    import src.services.auth.email_sender as es

    with caplog.at_level(logging.INFO, logger=es.logger.name):
        es.logger.info(
            "send_system_email ok: to=%s subject=%s",
            es.mask_email("recipient@example.com"),
            "Reset your password",
        )
    assert "recipient@example.com" not in caplog.text
    assert "r***@example.com" in caplog.text
