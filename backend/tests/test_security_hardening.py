"""Security-hardening batch — TDD coverage for findings H4, H5, L3.

H4 — Sentry must NOT ship PII: ``send_default_pii=False`` and a ``before_send``
     hook strips ``Cookie`` / ``Authorization`` / ``set-cookie`` request headers
     and any ``password`` field before an event leaves the process.
H5 — the session cookie ``Secure`` flag must be gated on the SAME prod
     detection as HSTS / Sentry (``APP_ENV=production`` OR ``RAILWAY_ENVIRONMENT``
     set), not on the stale ``JOB360_ENV=="prod"`` check.
L3 — CORS must fail closed: never pair ``allow_credentials=True`` with a
     wildcard (``*``) origin, and never boot prod with a wildcard/empty origin.
"""

from __future__ import annotations

import pytest
from fastapi import Response

# ---------------------------------------------------------------------------
# H4 — Sentry before_send scrubber
# ---------------------------------------------------------------------------


def test_sentry_scrub_removes_sensitive_headers_and_password():
    from src.api.main import _scrub_event

    event = {
        "request": {
            "headers": {
                "Cookie": "job360_session=secret-token",
                "Authorization": "Bearer abc123",
                "Set-Cookie": "job360_session=leak",
                "User-Agent": "pytest",
            },
            "data": {"email": "a@b.com", "password": "hunter2"},
        },
        "extra": {"password": "nested-secret", "keep": "ok"},
    }

    scrubbed = _scrub_event(event, None)

    headers = scrubbed["request"]["headers"]
    assert "Cookie" not in headers
    assert "Authorization" not in headers
    assert "Set-Cookie" not in headers
    # non-sensitive header preserved
    assert headers.get("User-Agent") == "pytest"
    # password stripped everywhere, harmless fields kept
    assert "password" not in scrubbed["request"]["data"]
    assert scrubbed["request"]["data"]["email"] == "a@b.com"
    assert "password" not in scrubbed["extra"]
    assert scrubbed["extra"]["keep"] == "ok"


def test_sentry_scrub_handles_missing_request():
    from src.api.main import _scrub_event

    # Must never raise on a minimal / odd-shaped event.
    assert _scrub_event({}, None) == {}
    assert _scrub_event({"request": None}, None) == {"request": None}


def test_sentry_init_disables_default_pii(monkeypatch):
    """``sentry_sdk.init`` is called with ``send_default_pii=False`` + a hook."""
    import src.api.main as main_mod
    import src.core.settings as settings_mod

    captured = {}

    def fake_init(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(settings_mod, "SENTRY_DSN", "https://x@example.com/1")
    monkeypatch.setattr(main_mod.sentry_sdk, "init", fake_init)
    # _init_sentry only reports from a deployed prod env (prod-gate). Mark prod so
    # it reaches the init() call whose PII settings this test asserts on.
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")

    main_mod._init_sentry()

    assert captured.get("send_default_pii") is False
    assert callable(captured.get("before_send"))


# ---------------------------------------------------------------------------
# H5 — cookie Secure gated on the shared prod detection
# ---------------------------------------------------------------------------


def _cookie_header(response: Response) -> str:
    raw = response.raw_headers
    for key, val in raw:
        if key.decode().lower() == "set-cookie":
            return val.decode()
    return ""


def test_session_cookie_secure_when_railway_env_set(monkeypatch):
    from src.api.routes import auth

    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("JOB360_ENV", raising=False)
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")

    resp = Response()
    auth._set_session_cookie(resp, "cookie-value")
    assert "Secure" in _cookie_header(resp)


def test_session_cookie_secure_when_app_env_production(monkeypatch):
    from src.api.routes import auth

    monkeypatch.delenv("JOB360_ENV", raising=False)
    monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)
    monkeypatch.setenv("APP_ENV", "production")

    resp = Response()
    auth._set_session_cookie(resp, "cookie-value")
    assert "Secure" in _cookie_header(resp)


def test_session_cookie_insecure_in_dev(monkeypatch):
    from src.api.routes import auth

    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("JOB360_ENV", raising=False)
    monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)

    resp = Response()
    auth._set_session_cookie(resp, "cookie-value")
    assert "Secure" not in _cookie_header(resp)


# ---------------------------------------------------------------------------
# L3 — CORS fails closed on wildcard + credentials
# ---------------------------------------------------------------------------


def test_cors_wildcard_disables_credentials_in_dev():
    from src.api.main import _resolve_cors_credentials

    assert _resolve_cors_credentials(["*"], is_prod=False) is False


def test_cors_wildcard_raises_in_prod():
    from src.api.main import _resolve_cors_credentials

    with pytest.raises(RuntimeError):
        _resolve_cors_credentials(["*"], is_prod=True)


def test_cors_empty_origins_raises_in_prod():
    from src.api.main import _resolve_cors_credentials

    with pytest.raises(RuntimeError):
        _resolve_cors_credentials([], is_prod=True)


def test_cors_normal_origins_allow_credentials():
    from src.api.main import _resolve_cors_credentials

    assert _resolve_cors_credentials(["https://job360.uk"], is_prod=True) is True
    assert _resolve_cors_credentials(["http://localhost:3000"], is_prod=False) is True
