"""Rule #4 guard: the suite never reaches the real internet or sends real email.

A developer's repo-root ``.env`` holds real Resend credentials, and
``settings`` loads it on import. Before ``conftest._offline_email`` every test
that registered a user sent a real verification email — ``aioresponses`` only
catches aiohttp, and the email sender uses httpx.
"""
from __future__ import annotations

import os

import httpx
import pytest


def test_email_credentials_are_blank_in_tests():
    from tests.conftest import _EMAIL_ENV_KEYS

    assert not [k for k in _EMAIL_ENV_KEYS if os.environ.get(k)]


@pytest.mark.asyncio
async def test_a_real_httpx_call_is_refused():
    async with httpx.AsyncClient() as client:
        with pytest.raises(RuntimeError, match="real network call"):
            await client.get("https://api.resend.com/emails")


def test_a_real_sync_httpx_call_is_refused():
    with httpx.Client() as client, pytest.raises(RuntimeError, match="real network call"):
        client.get("https://api.resend.com/emails")


@pytest.mark.asyncio
async def test_sending_with_a_key_still_never_leaves_the_machine(monkeypatch):
    # Even a test that sets a key cannot reach Resend: the transport refuses.
    from src.services.auth import email_sender

    monkeypatch.setenv("RESEND_API_KEY", "re_test_not_real")
    monkeypatch.setenv("SMTP_EMAIL", "noreply@example.com")
    try:
        sent = await email_sender.send_system_email(to_email="a@example.com", subject="s", body_text="b")
    except RuntimeError as exc:
        assert "real network call" in str(exc)
    else:
        assert sent is False
