"""The email channel must be built on a transport this host can actually use.

#318 — every email channel used to be built as ``mailtos://``, i.e. SMTP. But
Railway blocks outbound SMTP ports (25/465/587); that is precisely why
``services/auth/email_sender.py`` was rewritten off ``smtplib`` onto Resend's
HTTPS API, and why magic-link email only started arriving after that change.

So the old builder produced channels that could never deliver on the platform
Job360 actually runs on: Apprise would time out, the dispatcher would record
``ok=False``, and the user would see silence — indistinguishable from the
"nobody configured anything" bug this issue is about.

These tests pin the precedence and, importantly, that the URLs we emit are ones
Apprise can actually parse. ``Apprise.add()`` returns False (it does not raise)
on a malformed URL, so a bad shape would fail silently at send time — the exact
class of bug this whole change exists to kill.
"""
from __future__ import annotations

import pytest

from src.services.channels.email_url import build_email_apprise_url

_ALL_VARS = ("RESEND_API_KEY", "SMTP_EMAIL", "SMTP_PASSWORD", "SMTP_FROM")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Never read the developer's real .env — it holds a live Resend key."""
    for var in _ALL_VARS:
        monkeypatch.delenv(var, raising=False)


def test_prefers_resend_when_key_present(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_fake_key")
    monkeypatch.setenv("SMTP_FROM", "alerts@job360.uk")
    # SMTP creds present too — Resend must still win, because SMTP cannot leave
    # the host.
    monkeypatch.setenv("SMTP_EMAIL", "sender@gmail.com")
    monkeypatch.setenv("SMTP_PASSWORD", "app_password")

    url = build_email_apprise_url("dest@example.com")
    assert url == "resend://re_fake_key:alerts@job360.uk/dest@example.com/"


def test_reads_resend_key_hidden_in_smtp_password(monkeypatch):
    """Some deploys put the Resend key in SMTP_PASSWORD; email_sender honours
    that, so the alert path must honour it identically or the two disagree."""
    monkeypatch.setenv("SMTP_EMAIL", "login@job360.uk")
    monkeypatch.setenv("SMTP_PASSWORD", "re_looks_like_a_resend_key")

    url = build_email_apprise_url("dest@example.com")
    assert url is not None and url.startswith("resend://")
    # from_addr falls back to SMTP_EMAIL when SMTP_FROM is unset.
    assert "login@job360.uk" in url


def test_falls_back_to_mailtos_for_local_smtp(monkeypatch):
    monkeypatch.setenv("SMTP_EMAIL", "sender@gmail.com")
    monkeypatch.setenv("SMTP_PASSWORD", "app_password")

    url = build_email_apprise_url("dest@example.com")
    assert url is not None and url.startswith("mailtos://")
    assert "to=dest%40example.com" in url


def test_returns_none_when_nothing_configured():
    """Caller decides: 503 for an explicit request, silent skip when seeding.

    Returning a URL here would create a channel that cannot deliver — the
    silent-failure shape this issue is made of.
    """
    assert build_email_apprise_url("dest@example.com") is None


def test_rejects_a_bad_address(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_fake_key")
    with pytest.raises(ValueError):
        build_email_apprise_url("not-an-email")


@pytest.mark.parametrize(
    "dest",
    [
        "plain@example.com",
        "plus+tag@example.com",
        "dotted.name@sub.domain.co.uk",
        "dash-name@my-host.io",
    ],
)
def test_emitted_resend_url_is_parseable_by_apprise(monkeypatch, dest):
    """The URL must survive Apprise's own parser.

    ``Apprise.add()`` returns False rather than raising on a malformed URL, so
    without this the builder could emit garbage and every send would no-op with
    no error anywhere. Percent-encoding the from/to pair, for instance, makes
    the resend plugin reject the URL outright.
    """
    import apprise

    monkeypatch.setenv("RESEND_API_KEY", "re_fake_key")
    monkeypatch.setenv("SMTP_FROM", "alerts@job360.uk")

    url = build_email_apprise_url(dest)
    assert url is not None

    ap = apprise.Apprise()
    assert ap.add(url) is True, f"Apprise rejected the URL we build for {dest}"
    server = list(ap)[0]
    assert server.targets == [dest]
    assert server.from_addr[1] == "alerts@job360.uk"
