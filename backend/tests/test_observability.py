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
async def test_json_api_gets_strict_csp_and_permissions_policy():
    """JSON routes get a strict CSP (no CDN script origin) and a Permissions-Policy."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/health")
    csp = resp.headers["content-security-policy"]
    assert csp == "default-src 'none'; frame-ancestors 'none'"  # exact: no CDN script origin
    pp = resp.headers.get("permissions-policy", "")
    for feature in ("camera=()", "microphone=()", "geolocation=()", "payment=()", "usb=()"):
        assert feature in pp


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/docs", "/redoc"])
async def test_docs_pages_keep_relaxed_csp_for_swagger(path):
    """Swagger/ReDoc (dev/CI only) still load their CDN assets."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(path)
    assert resp.status_code == 200, f"{path} not mounted in the test env"
    csp = resp.headers["content-security-policy"]
    from src.api.middleware import _DOCS_CSP

    assert csp == _DOCS_CSP  # whole policy, exact (CodeQL flags host-literal containment tests)
    assert "frame-ancestors 'none'" in csp
    assert "permissions-policy" in resp.headers


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
    assert calls[0]["max_request_body_size"] == "never"
    assert calls[0]["include_local_variables"] is False
    # Frame locals are scrubbed at every depth: a token inside a dict local (headers, a decrypted state)
    # is caught too, not only a local NAMED token.
    event = {"exception": {"values": [{"stacktrace": {"frames": [{"vars": {
        "headers": {"Authorization": "Bearer sk-ant-oat01-x"}, "state": {"token": "sk-ant-oat01-x"},
    }}]}}]}}
    calls[0]["event_scrubber"].scrub_event(event)
    assert "sk-ant-oat01-x" not in str(event)


def test_sentry_drops_the_framework_frames_variables_that_hold_the_raw_body():
    """FastAPI's request handler keeps the raw body as ``body_bytes``; no key-name scrubber knows it."""
    from src.core.observability import _scrub_pii

    raw = b'{"email":"a@b.c","password":"hunter2-secret"}'
    event = {"exception": {"values": [{"stacktrace": {"frames": [
        {"module": "starlette.routing", "vars": {"scope": "{...}"}},
        {"module": "fastapi.routing", "vars": {"body_bytes": repr(raw)}},
        {"module": "src.api.routes.auth", "vars": {"attempt": 2}},
    ]}}]}}
    frames = _scrub_pii(event, None)["exception"]["values"][0]["stacktrace"]["frames"]
    assert "hunter2-secret" not in str(event)
    assert [f.get("vars") for f in frames] == [None, None, {"attempt": 2}]   # the filter itself leaves ours; init_sentry sends no locals at all (test above)


def test_sentry_event_carries_no_frame_locals_from_our_own_code(monkeypatch):
    """C1/D3: the scrubber matches dict KEYS only, so the repr of a pydantic model in a frame local
    (email, password) or a local named ``current_password`` would go out in plaintext. Real client,
    capturing transport, the exact kwargs ``init_sentry`` passes."""
    import sentry_sdk
    from pydantic import BaseModel
    from sentry_sdk.transport import Transport

    import src.core.settings as settings
    from src.core.observability import init_sentry

    class LoginRequest(BaseModel):
        email: str
        password: str

    sent: list = []

    class Capture(Transport):
        def capture_envelope(self, envelope):
            for item in envelope.items:
                if item.payload.json is not None:
                    sent.append(item.payload.json)

    monkeypatch.setattr(settings, "SENTRY_DSN", "https://key@o0.ingest.example.invalid/1")
    monkeypatch.setenv("APP_ENV", "production")
    real_init = sentry_sdk.init
    monkeypatch.setattr(sentry_sdk, "init", lambda **kw: real_init(**{**kw, "transport": Capture()}))

    # Built at runtime: Sentry also ships source lines, so a literal in this file would show up anyway.
    fakes = ("fake-current-" + "pw-1234", "fake.person@" + "example.invalid", "fake-login-" + "pw-5678")

    def fails():
        current_password = fakes[0]
        req = LoginRequest(email=fakes[1], password=fakes[2])
        raise RuntimeError(f"boom {len(current_password)} {type(req).__name__}")

    try:
        assert init_sentry() is True
        try:
            fails()
        except RuntimeError:
            sentry_sdk.capture_exception()
        sentry_sdk.flush()
    finally:
        sentry_sdk.get_client().close()
    assert sent, "no event was captured"
    dumped = str(sent)
    for fake in fakes:
        assert fake not in dumped


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
