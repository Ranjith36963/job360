"""Phase-3 observability + security tests.

Covers:
1. Security headers on every response (HSTS absent in non-prod).
2. Sentry init: empty DSN → no-op; real DSN → sentry_sdk.init called.
3. Auth: failed login emits WARNING on the audit logger.
"""
from __future__ import annotations

import logging

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.main import app  # noqa: E402 – imported after stdlib/third-party as required

# ---------------------------------------------------------------------------
# 1. Security headers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_security_headers_present_on_health():
    """Every response must carry the required security headers."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/health")
    assert resp.status_code == 200
    assert resp.headers.get("x-content-type-options") == "nosniff"
    assert resp.headers.get("x-frame-options") == "DENY"
    assert resp.headers.get("referrer-policy") == "strict-origin-when-cross-origin"
    assert "content-security-policy" in resp.headers


@pytest.mark.asyncio
async def test_hsts_absent_in_non_prod():
    """HSTS must NOT be set in dev/test (no HTTPS, would break local dev)."""
    import os

    # Make absolutely sure we're not in a prod env for this test.
    env_backup = {k: os.environ.pop(k, None) for k in ("APP_ENV", "RAILWAY_ENVIRONMENT")}
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/health")
        assert "strict-transport-security" not in resp.headers
    finally:
        for k, v in env_backup.items():
            if v is not None:
                os.environ[k] = v


# ---------------------------------------------------------------------------
# 2. Sentry init
# ---------------------------------------------------------------------------


def test_sentry_no_dsn_no_raise(monkeypatch):
    """_init_sentry() with an empty DSN must not raise and must not call sentry_sdk.init."""
    import sentry_sdk

    from src.api import main as app_main

    monkeypatch.setattr("src.core.settings.SENTRY_DSN", "", raising=False)
    calls: list = []
    monkeypatch.setattr(sentry_sdk, "init", lambda **kwargs: calls.append(kwargs))
    app_main._init_sentry()  # must be a no-op
    assert calls == [], "_init_sentry() should not call sentry_sdk.init when DSN is empty"


def test_sentry_dsn_but_not_prod_no_init(monkeypatch):
    """DSN present but NOT a prod environment (local dev / tests) → no init.

    This is the guard that stops local crashes (port-in-use, dev DB timeouts)
    from leaking into the production Sentry project and drowning out real signal.
    """
    import sentry_sdk

    from src.api import main as app_main

    fake_dsn = "https://abc123@o123456.ingest.sentry.io/7654321"
    monkeypatch.setattr("src.core.settings.SENTRY_DSN", fake_dsn, raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)
    calls: list = []
    monkeypatch.setattr(sentry_sdk, "init", lambda **kwargs: calls.append(kwargs))
    app_main._init_sentry()
    assert calls == [], "Sentry must NOT init outside production, even with a DSN set"


def test_sentry_with_dsn_in_prod_calls_init(monkeypatch):
    """DSN + a deployed prod environment → sentry_sdk.init called once, tagged prod."""
    import sentry_sdk

    from src.api import main as app_main

    fake_dsn = "https://abc123@o123456.ingest.sentry.io/7654321"
    monkeypatch.setattr("src.core.settings.SENTRY_DSN", fake_dsn, raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    calls: list = []
    monkeypatch.setattr(sentry_sdk, "init", lambda **kwargs: calls.append(kwargs))
    app_main._init_sentry()
    assert len(calls) == 1, "sentry_sdk.init should be called exactly once in prod"
    assert calls[0]["dsn"] == fake_dsn
    assert calls[0]["environment"] == "production"


# ---------------------------------------------------------------------------
# 3. Auth logging — failed login emits WARNING on audit logger
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failed_login_emits_warning(authenticated_async_context, caplog):
    """A wrong-credential login attempt must emit a WARNING on job360.audit."""
    audit_logger = logging.getLogger("job360.audit")
    # caplog adds its handler to the root logger; audit uses propagate=False so
    # we add caplog's handler directly to the audit logger for the duration.
    audit_logger.addHandler(caplog.handler)
    try:
        with caplog.at_level(logging.WARNING, logger="job360.audit"):
            async with authenticated_async_context() as client:
                resp = await client.post(
                    "/api/auth/login",
                    json={"email": "nobody@nowhere.com", "password": "wrongpassword"},
                )
    finally:
        audit_logger.removeHandler(caplog.handler)

    assert resp.status_code == 401
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings, "failed login must emit at least one WARNING on job360.audit"
    # Safety: no password or full token in the log record
    for record in warnings:
        assert "wrongpassword" not in str(record.getMessage())
