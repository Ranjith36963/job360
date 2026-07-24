"""Tests for the Phase −2 post-code-review fixes.

Covers:
  fix #1+#2: register schedules verification via BackgroundTasks instead
             of inline await — and a deliberately-broken verification
             call doesn't 500 the register response (orphan-account guard).
  fix #3:    rate limit on /verify-email/request (429 after first call
             within a minute) and on /password-reset/request (silent
             204 to preserve no-enumeration contract).
  fix #4:    HTML-escape interpolated email + frontend_origin in the two
             transactional email body builders.
  fix #5:    _classify docstring matches actual behavior; non-streak
             single error stays "ok" — sanity check the spec is honest.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from migrations import runner
from src.api.routes import runs as runs_route
from src.repositories import pg
from src.services.auth import email_verification, password_reset, rate_limit, tokens
from src.services.channels import crypto


@asynccontextmanager
async def _noop_lifespan(app):
    yield


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    """Every test gets fresh rate-limit buckets — otherwise cross-test pollution
    would make the first test in the file pass and the rest fail spuriously."""
    rate_limit.reset_for_tests()
    yield
    rate_limit.reset_for_tests()


@pytest.fixture
def temp_db(monkeypatch, tmp_path):
    db_path = str(tmp_path / "test.db")

    async def _bootstrap():
        async with pg.connect(db_path) as db:
            await db.executescript(
                """
                CREATE TABLE user_actions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id INTEGER NOT NULL,
                    action TEXT NOT NULL,
                    notes TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    UNIQUE(job_id)
                );
                CREATE TABLE applications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id INTEGER NOT NULL,
                    stage TEXT NOT NULL DEFAULT 'applied',
                    notes TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(job_id)
                );
                """
            )
            await db.commit()
        await runner.up(db_path)

    asyncio.run(_bootstrap())

    from pathlib import Path

    from src.api import auth_deps, dependencies
    from src.api.routes import auth as auth_route
    from src.core import settings

    patched = Path(db_path)
    monkeypatch.setattr(settings, "DB_PATH", patched, raising=True)
    monkeypatch.setattr(dependencies, "DB_PATH", patched, raising=True)
    monkeypatch.setattr(auth_deps, "DB_PATH", patched, raising=True)
    monkeypatch.setattr(auth_route, "DB_PATH", patched, raising=True)

    crypto.set_test_key(Fernet.generate_key().decode("ascii"))
    monkeypatch.setenv("SESSION_SECRET", "test-secret-" + "x" * 40)

    yield db_path


@pytest.fixture
def client(temp_db):
    from src.api.main import app

    app.router.lifespan_context = _noop_lifespan  # type: ignore[assignment]
    with TestClient(app) as c:
        yield c


# ─── fix #1 + #2: register doesn't 500 when verification raises ─────────────


def test_register_returns_201_even_if_verification_email_raises(
    client, monkeypatch, temp_db
):
    """If request_email_verification raises (e.g. DB problem), register
    must still return 201 — the user row was committed, so the response
    must NOT propagate the background-task failure as 500.
    """
    async def _explode(**kwargs):
        raise RuntimeError("simulated DB failure in verification path")

    monkeypatch.setattr(
        email_verification, "request_email_verification", _explode
    )

    r = client.post(
        "/api/auth/register",
        json={"email": "alice@example.com", "password": "s3cretpassword"},
    )
    # Pre-fix this would have been 500; post-fix it's still 201.
    assert r.status_code == 201, r.text
    # M2 — register now returns a generic body (no email) and sets no cookie.
    # This test's point is that a failing verification email doesn't turn register
    # into a 500; the sibling test asserts the user row persists.
    assert r.json().get("status") == "ok"
    assert "job360_session" not in r.cookies


def test_register_user_row_persists_after_simulated_verification_error(
    client, monkeypatch, temp_db
):
    """The user row stays committed even when the background task fails —
    proves the failure is decoupled from the response."""
    async def _explode(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(
        email_verification, "request_email_verification", _explode
    )

    client.post(
        "/api/auth/register",
        json={"email": "alice@example.com", "password": "s3cretpassword"},
    )

    async def _row():
        async with pg.connect(temp_db) as db:
            db.row_factory = pg.Row
            cur = await db.execute(
                "SELECT id FROM users WHERE email = ?", ("alice@example.com",)
            )
            return await cur.fetchone()

    row = asyncio.run(_row())
    assert row is not None, "user row missing despite 201 response"


# ─── fix #3: rate limit on /verify-email/request ────────────────────────────


def test_verify_email_resend_rate_limited_to_429(client, monkeypatch):
    captured = []
    orig = tokens.generate_token

    def _spy():
        raw, h = orig()
        captured.append(raw)
        return raw, h

    monkeypatch.setattr(tokens, "generate_token", _spy)

    client.post(
        "/api/auth/register",
        json={"email": "alice@example.com", "password": "s3cretpassword"},
    )
    # M2 — register no longer auto-logs-in; verify-email/request needs a session.
    client.post(
        "/api/auth/login",
        json={"email": "alice@example.com", "password": "s3cretpassword"},
    )
    # The register itself triggers one verification email — that consumed
    # the user's bucket too? No: register routes through register, not
    # verify-email/request, so the bucket is fresh on first resend.
    r1 = client.post("/api/auth/verify-email/request")
    assert r1.status_code == 204
    # Immediate second hit must be rate-limited.
    r2 = client.post("/api/auth/verify-email/request")
    assert r2.status_code == 429
    assert "too many" in r2.json().get("detail", "").lower()


def test_verify_email_resend_429_does_not_issue_token(client, monkeypatch, temp_db):
    """Rate-limited request must not consume an issue: no row added on 429."""
    client.post(
        "/api/auth/register",
        json={"email": "alice@example.com", "password": "s3cretpassword"},
    )
    # M2 — register no longer auto-logs-in; verify-email/request needs a session.
    client.post(
        "/api/auth/login",
        json={"email": "alice@example.com", "password": "s3cretpassword"},
    )
    client.post("/api/auth/verify-email/request")  # 204

    async def _count():
        async with pg.connect(temp_db) as db:
            cur = await db.execute(
                "SELECT COUNT(*) FROM email_verifications WHERE email = ?",
                ("alice@example.com",),
            )
            return (await cur.fetchone())[0]

    before = asyncio.run(_count())
    r = client.post("/api/auth/verify-email/request")
    assert r.status_code == 429
    after = asyncio.run(_count())
    assert after == before, f"429 still inserted a token row ({before} -> {after})"


# ─── fix #3: silent rate limit on /password-reset/request ──────────────────


def test_password_reset_request_rate_limited_silently_returns_204(client):
    # No-enumeration: same response shape regardless of rate-limit state.
    r1 = client.post(
        "/api/auth/password-reset/request",
        json={"email": "anyone@example.com"},
    )
    assert r1.status_code == 204
    r2 = client.post(
        "/api/auth/password-reset/request",
        json={"email": "anyone@example.com"},
    )
    # Crucially still 204 — preserves the no-enumeration contract by
    # being indistinguishable from "valid request, email sent".
    assert r2.status_code == 204


def test_password_reset_request_rate_limit_skips_token_issue(
    client, temp_db
):
    """The rate-limited call returns 204 but issues no token row."""
    client.post(
        "/api/auth/register",
        json={"email": "alice@example.com", "password": "s3cretpassword"},
    )

    # First call: real password-reset by Alice. Issues a token.
    client.post(
        "/api/auth/password-reset/request",
        json={"email": "alice@example.com"},
    )

    async def _count():
        async with pg.connect(temp_db) as db:
            db.row_factory = pg.Row
            cur = await db.execute(
                """
                SELECT COUNT(*) FROM password_resets pr
                INNER JOIN users u ON u.id = pr.user_id
                WHERE u.email = ?
                """,
                ("alice@example.com",),
            )
            return (await cur.fetchone())[0]

    after_first = asyncio.run(_count())
    # Second call within window: rate-limited, no new row.
    client.post(
        "/api/auth/password-reset/request",
        json={"email": "alice@example.com"},
    )
    after_second = asyncio.run(_count())
    assert after_first == after_second, (
        f"rate-limited password-reset still inserted a row ({after_first} -> {after_second})"
    )


# ─── fix #4: HTML escape in email bodies ────────────────────────────────────


def test_password_reset_html_body_escapes_email():
    subject, text, html_body = password_reset._build_reset_email(
        to_email="<script>alert(1)</script>@example.com",
        raw_token="abc123",
        frontend_origin="https://job360.app",
    )
    # The HTML body must NOT contain the raw script tag.
    assert "<script>alert(1)</script>" not in html_body
    # It must contain the escaped form.
    assert "&lt;script&gt;" in html_body
    # The plain-text body is untouched — that's fine, text/plain isn't rendered.
    assert "<script>" in text


def test_password_reset_html_body_escapes_frontend_origin_with_injection():
    subject, text, html_body = password_reset._build_reset_email(
        to_email="alice@example.com",
        raw_token="abc",
        frontend_origin='https://job360.app/"><script>alert(1)</script>',
    )
    # The raw injection must NOT survive into the href.
    assert '<script>alert(1)</script>' not in html_body


def test_email_verification_html_body_escapes_email():
    subject, text, html_body = email_verification._build_verification_email(
        to_email="<img src=x onerror=alert(1)>@example.com",
        raw_token="xyz",
        frontend_origin="https://job360.app",
    )
    assert "<img src=x onerror=alert(1)>" not in html_body
    assert "&lt;img" in html_body


def test_password_reset_url_token_is_percent_encoded():
    """A raw token that happens to contain & or = (defensive) must not
    break the query string parsing."""
    subject, text, html_body = password_reset._build_reset_email(
        to_email="alice@example.com",
        raw_token="raw&token=evil",
        frontend_origin="https://job360.app",
    )
    # Both bodies should contain percent-encoded version.
    assert "raw%26token%3Devil" in text
    assert "raw%26token%3Devil" in html_body


# ─── fix #5: _classify docstring matches actual behavior ───────────────────


def test_classify_single_error_not_streak_is_ok():
    """Sanity check: the docstring previously implied a single error
    triggers warning; the actual behavior (post-fix) requires either
    >50% error rate or a zero-streak. A 1-error-in-5 run with no zeros
    stays 'ok'.
    """
    assert runs_route._classify(zero_streak=0, total=5, errored=1) == "ok"


def test_classify_critical_on_three_zero_streak():
    assert runs_route._classify(zero_streak=3, total=10, errored=0) == "critical"


def test_classify_warning_on_high_error_rate():
    # 6 errors out of 10 = 60% — above 50% threshold.
    assert runs_route._classify(zero_streak=0, total=10, errored=6) == "warning"


def test_classify_warning_on_one_third_zero_rate():
    # Exercise the zero-rate warning branch (clause 2b): zero_streak >= 1 AND
    # zero_streak/total >= 0.33. The streak must stay BELOW the critical
    # threshold (>= 3), otherwise clause 1 returns "critical" first. So use
    # zero_streak=2/total=6 (= 33%, streak < 3) — the original 4/10 was buggy:
    # streak=4 trips critical, contradicting test_classify_critical (streak=3).
    assert runs_route._classify(zero_streak=2, total=6, errored=0) == "warning"


# ─── rate_limit module sanity ───────────────────────────────────────────────


def test_rate_limit_first_call_allowed():
    assert rate_limit.check_and_record("k", max_in_window=1, window_seconds=60) is True


def test_rate_limit_second_call_blocked():
    rate_limit.check_and_record("k", max_in_window=1, window_seconds=60)
    assert rate_limit.check_and_record("k", max_in_window=1, window_seconds=60) is False


def test_rate_limit_distinct_keys_independent():
    rate_limit.check_and_record("k1", max_in_window=1, window_seconds=60)
    assert rate_limit.check_and_record("k2", max_in_window=1, window_seconds=60) is True


def test_rate_limit_higher_quota():
    assert rate_limit.check_and_record("k", max_in_window=3, window_seconds=60) is True
    assert rate_limit.check_and_record("k", max_in_window=3, window_seconds=60) is True
    assert rate_limit.check_and_record("k", max_in_window=3, window_seconds=60) is True
    assert rate_limit.check_and_record("k", max_in_window=3, window_seconds=60) is False
