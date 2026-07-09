"""Failure-path coverage for send_system_email.

The magic-link/verification suites only ever exercised the happy path (they
monkeypatch the sender to succeed). These tests pin the *failure* contract:
a broken provider must return False (never raise) AND log at ERROR so a silent
prod outage is visible in logs — the whole point of PLAN-2's log-level bump.
"""
import logging

import httpx

from src.services.auth import email_sender


class _Resp:
    """Minimal stand-in for an httpx.Response."""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        self.text = "error body from provider"


class _FakeAsyncClient:
    """Drop-in for httpx.AsyncClient whose POST always 500s."""

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, *args, **kwargs):
        return _Resp(500)


async def test_resend_5xx_returns_false_and_logs_error(monkeypatch, caplog):
    """A Resend 5xx must not raise, must return False, and must log at ERROR."""
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

    with caplog.at_level(logging.ERROR, logger="job360.auth.email_sender"):
        ok = await email_sender.send_system_email(
            to_email="someone@example.com", subject="s", body_text="b"
        )

    assert ok is False
    assert any(
        r.levelno == logging.ERROR for r in caplog.records
    ), "delivery failure must log at ERROR so it is visible in prod logs"


async def test_no_provider_configured_returns_false_and_logs_error(monkeypatch, caplog):
    """No Resend key and no SMTP creds -> False + ERROR (silent-config guard)."""
    for var in ("RESEND_API_KEY", "SMTP_PASSWORD", "SMTP_EMAIL", "SMTP_FROM"):
        monkeypatch.delenv(var, raising=False)

    with caplog.at_level(logging.ERROR, logger="job360.auth.email_sender"):
        ok = await email_sender.send_system_email(
            to_email="someone@example.com", subject="s", body_text="b"
        )

    assert ok is False
    assert any(r.levelno == logging.ERROR for r in caplog.records)
