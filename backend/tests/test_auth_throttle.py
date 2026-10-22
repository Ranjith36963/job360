"""Registration + password-reset rate-limit regression tests.

Covers two hardening fixes in ``src/api/routes/auth.py``:

* **M3** — ``POST /api/auth/register`` had ZERO rate limit, so one host could
  mass-create accounts. Now throttled per client IP (10 / hour). The 11th
  attempt from the same IP gets 429.
* **LOCKOUT** — ``POST /api/auth/password-reset/request`` was throttled on the
  client IP only, so rotating IPs let an attacker email-bomb one address. Now
  there is ALSO a per-email throttle (3 / hour) that fires even when every
  request comes from a different IP — and stays silent (204, no enumeration).

The limiter (``services/auth/rate_limit.py``) keeps state in process-global
dicts, so ``reset_for_tests()`` is called in setup (there is also an autouse
reset in conftest, but this file is explicit per the task).
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

from migrations import runner
from src.repositories import pg
from src.services.auth import rate_limit


@asynccontextmanager
async def _noop_lifespan(app):
    yield


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    rate_limit.reset_for_tests()
    yield
    rate_limit.reset_for_tests()


@pytest.fixture
def temp_db(monkeypatch, tmp_path):
    """Fresh migrated DB with DB_PATH patched on every importer."""
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

    monkeypatch.setenv("SESSION_SECRET", "test-secret-" + "x" * 40)

    yield db_path


@pytest.fixture
def client(temp_db):
    from src.api.main import app

    app.router.lifespan_context = _noop_lifespan  # type: ignore[assignment]
    with TestClient(app) as c:
        yield c


def _client_from_ip(host: str) -> TestClient:
    """A TestClient whose requests present ``host`` as ``request.client.host``.

    ``password_reset_request`` derives its per-IP throttle bucket from
    ``src.api.client_ip``, which is ``request.client.host`` while no trusted
    proxy hops are configured (the test default), so to
    prove the per-EMAIL throttle bites across DIFFERENT source IPs we need each
    request to actually arrive from a different client host."""
    from src.api.main import app

    app.router.lifespan_context = _noop_lifespan  # type: ignore[assignment]
    return TestClient(app, client=(host, 50000))


# ─── M3: register per-IP throttle ────────────────────────────────────────────


def test_register_throttled_after_ten_from_one_ip(client):
    """10 registrations from one IP succeed; the 11th is 429."""
    # All 10 use distinct emails so nothing 409s — only the IP throttle can bite.
    for i in range(10):
        r = client.post(
            "/api/auth/register",
            json={"email": f"user{i}@example.com", "password": "s3cretpassword"},
        )
        assert r.status_code == 201, r.text
        client.cookies.clear()

    r = client.post(
        "/api/auth/register",
        json={"email": "user10@example.com", "password": "s3cretpassword"},
    )
    assert r.status_code == 429, r.text
    assert "registration attempts" in r.json()["detail"].lower()


def test_register_429_does_not_leak_email_existence(client):
    """When throttled, an already-registered email and a brand-new email both
    get the SAME 429 — the throttle fires before any DB lookup, so it can't be
    used to enumerate accounts."""
    for i in range(10):
        r = client.post(
            "/api/auth/register",
            json={"email": f"seed{i}@example.com", "password": "s3cretpassword"},
        )
        assert r.status_code == 201, r.text
        client.cookies.clear()

    # Existing email → still 429 (not 409), so no "email already registered" leak.
    r_existing = client.post(
        "/api/auth/register",
        json={"email": "seed0@example.com", "password": "s3cretpassword"},
    )
    r_new = client.post(
        "/api/auth/register",
        json={"email": "never-seen@example.com", "password": "s3cretpassword"},
    )
    assert r_existing.status_code == 429, r_existing.text
    assert r_new.status_code == 429, r_new.text
    assert r_existing.json()["detail"] == r_new.json()["detail"]


# ─── LOCKOUT: password-reset per-email throttle across IPs ────────────────────


def test_password_reset_throttled_per_email_across_ips(temp_db, monkeypatch):
    """One email can only trigger 3 reset requests / hour even when every
    request arrives from a DIFFERENT source IP — the per-IP throttle (1/60s)
    never fires here because each IP is a fresh bucket, so this isolates the
    per-EMAIL dimension.

    The endpoint stays silent (always 204, no enumeration): we assert the email
    SERVICE is skipped once the email throttle trips, not any status change."""
    import src.api.routes.auth as auth_route

    calls: list[str] = []

    async def _fake_request_password_reset(*, db_path, email, frontend_origin):
        calls.append(email)

    monkeypatch.setattr(
        auth_route.auth_password_reset,
        "request_password_reset",
        _fake_request_password_reset,
    )

    email = "target@example.com"
    # 3 requests, each from a DISTINCT client IP → every per-IP bucket is fresh
    # and passes; only the shared per-email bucket accumulates.
    for i in range(3):
        r = _client_from_ip(f"10.0.0.{i}").post(
            "/api/auth/password-reset/request", json={"email": email}
        )
        assert r.status_code == 204, r.text

    # 4th request, yet another fresh IP: still 204 (silent), but the reset
    # service must NOT have been invoked — the per-email throttle suppressed it.
    r = _client_from_ip("10.0.0.99").post(
        "/api/auth/password-reset/request", json={"email": email}
    )
    assert r.status_code == 204, r.text
    assert calls == [email, email, email], f"expected 3 sends before throttle, got {calls}"


def test_password_reset_email_throttle_is_per_email(temp_db, monkeypatch):
    """A different email is unaffected by another email's throttle."""
    import src.api.routes.auth as auth_route

    calls: list[str] = []

    async def _fake_request_password_reset(*, db_path, email, frontend_origin):
        calls.append(email)

    monkeypatch.setattr(
        auth_route.auth_password_reset,
        "request_password_reset",
        _fake_request_password_reset,
    )

    # Exhaust email A's throttle — each from a fresh IP so only the email bucket bites.
    for i in range(4):
        _client_from_ip(f"172.16.0.{i}").post(
            "/api/auth/password-reset/request", json={"email": "a@example.com"}
        )

    # Email B, first ever request, still goes through.
    r = _client_from_ip("172.16.0.200").post(
        "/api/auth/password-reset/request", json={"email": "b@example.com"}
    )
    assert r.status_code == 204, r.text
    assert "b@example.com" in calls


# ─── Gap-audit fix #6: forged X-Forwarded-For + per-email login cap ──────────

_LOGIN_EMAIL = "victim@example.com"
_LOGIN_PW = "correct-horse-battery"


def _register_victim(c: TestClient) -> None:
    r = c.post("/api/auth/register", json={"email": _LOGIN_EMAIL, "password": _LOGIN_PW})
    assert r.status_code == 201, r.text
    c.cookies.clear()


def _login(c: TestClient, password: str, xff: str | None = None):
    headers = {"x-forwarded-for": xff} if xff is not None else {}
    return c.post(
        "/api/auth/login", json={"email": _LOGIN_EMAIL, "password": password}, headers=headers
    )


def test_login_lockout_survives_rotating_forged_forwarded_for(client, monkeypatch):
    """One proxy hop trusted: the attacker forges a NEW leftmost entry on every
    try, but the proxy-written rightmost entry is the key, so the email+IP
    lockout still bites after LOGIN_MAX_ATTEMPTS. (With the old leftmost read
    every attempt was a fresh bucket and this never locked.)"""
    from src.core import settings

    monkeypatch.setattr(settings, "TRUSTED_PROXY_HOPS", 1)
    _register_victim(client)
    for i in range(settings.LOGIN_MAX_ATTEMPTS):
        r = _login(client, "wrong", xff=f"6.6.6.{i}, 203.0.113.9")
        assert r.status_code == 401, r.text
    r = _login(client, "wrong", xff="6.6.6.250, 203.0.113.9")
    assert r.status_code == 429, r.text
    # The real owner, behind the same proxy from a different address, is unaffected.
    r = _login(client, _LOGIN_PW, xff="198.51.100.20")
    assert r.status_code == 200, r.text


def test_login_lockout_ignores_forwarded_for_when_untrusted(client, monkeypatch):
    """Hops 0 (the default): the header is ignored outright, so forging it
    changes nothing and the socket peer is the key."""
    from src.core import settings

    monkeypatch.setattr(settings, "TRUSTED_PROXY_HOPS", 0)
    _register_victim(client)
    for i in range(settings.LOGIN_MAX_ATTEMPTS):
        assert _login(client, "wrong", xff=f"6.6.6.{i}").status_code == 401
    assert _login(client, "wrong", xff="7.7.7.7").status_code == 429


def test_login_per_email_cap_trips_across_addresses(temp_db, monkeypatch):
    """Distributed stuffing: every address stays under its own email+IP lock,
    but the per-email cap (LOGIN_EMAIL_MAX_ATTEMPTS) stops the account taking
    more guesses — even the correct password then gets 429."""
    from src.core import settings

    monkeypatch.setattr(settings, "TRUSTED_PROXY_HOPS", 0)
    monkeypatch.setattr(settings, "LOGIN_EMAIL_MAX_ATTEMPTS", 50)  # opt-in: off by default
    per_ip = settings.LOGIN_MAX_ATTEMPTS
    cap = settings.LOGIN_EMAIL_MAX_ATTEMPTS
    assert cap > per_ip  # larger than the per-IP limit
    _register_victim(_client_from_ip("192.0.2.1"))

    n_addresses = cap // per_ip  # 10 distinct addresses at the defaults
    for a in range(n_addresses):
        c = _client_from_ip(f"10.1.{a}.1")
        for _ in range(per_ip):
            assert _login(c, "wrong").status_code == 401

    fresh = _client_from_ip("10.9.9.9")
    r = _login(fresh, "wrong")
    assert r.status_code == 429, r.text
    assert "Retry-After" in r.headers
    assert _login(fresh, _LOGIN_PW).status_code == 429


def test_login_per_email_cap_is_off_by_default(temp_db, monkeypatch):
    """Shipped default: no per-email cap, so distributed wrong guesses from many
    addresses never lock the owner out of the right password."""
    from src.core import settings

    assert settings.LOGIN_EMAIL_MAX_ATTEMPTS == 0
    monkeypatch.setattr(settings, "TRUSTED_PROXY_HOPS", 0)
    _register_victim(_client_from_ip("192.0.2.1"))
    for a in range(12):
        c = _client_from_ip(f"10.8.{a}.1")
        for _ in range(settings.LOGIN_MAX_ATTEMPTS):
            assert _login(c, "wrong").status_code == 401
    assert _login(_client_from_ip("10.9.9.9"), _LOGIN_PW).status_code == 200


def test_login_per_email_cap_needs_many_addresses(temp_db, monkeypatch):
    """One attacker address cannot push the account toward the cap past its own
    email+IP lock: once locked it records nothing more, and the owner on
    another address still signs in."""
    from src.core import settings

    monkeypatch.setattr(settings, "TRUSTED_PROXY_HOPS", 0)
    monkeypatch.setattr(settings, "LOGIN_EMAIL_MAX_ATTEMPTS", 2 * settings.LOGIN_MAX_ATTEMPTS)
    _register_victim(_client_from_ip("192.0.2.1"))
    attacker = _client_from_ip("10.6.6.6")
    for _ in range(settings.LOGIN_MAX_ATTEMPTS):
        assert _login(attacker, "wrong").status_code == 401
    for _ in range(20):  # hammering while locked adds no failures
        assert _login(attacker, "wrong").status_code == 429
    assert _login(_client_from_ip("198.51.100.20"), _LOGIN_PW).status_code == 200


def test_login_per_email_cap_zero_disables(temp_db, monkeypatch):
    from src.core import settings

    monkeypatch.setattr(settings, "TRUSTED_PROXY_HOPS", 0)
    monkeypatch.setattr(settings, "LOGIN_EMAIL_MAX_ATTEMPTS", 0)
    _register_victim(_client_from_ip("192.0.2.1"))
    for a in range(12):
        c = _client_from_ip(f"10.2.{a}.1")
        for _ in range(settings.LOGIN_MAX_ATTEMPTS):
            assert _login(c, "wrong").status_code == 401
    assert _login(_client_from_ip("10.9.9.9"), _LOGIN_PW).status_code == 200


def test_register_throttle_ignores_forged_leftmost_entry(client, monkeypatch):
    """The register per-IP bucket is keyed on the trusted hop too."""
    from src.core import settings

    monkeypatch.setattr(settings, "TRUSTED_PROXY_HOPS", 1)
    for i in range(10):
        r = client.post(
            "/api/auth/register",
            json={"email": f"spoof{i}@example.com", "password": "s3cretpassword"},
            headers={"x-forwarded-for": f"6.6.6.{i}, 203.0.113.9"},
        )
        assert r.status_code == 201, r.text
        client.cookies.clear()
    r = client.post(
        "/api/auth/register",
        json={"email": "spoof10@example.com", "password": "s3cretpassword"},
        headers={"x-forwarded-for": "6.6.6.99, 203.0.113.9"},
    )
    assert r.status_code == 429, r.text


# ─── Security review P1: concurrent bursts + IPv6 /64 ────────────────────────


def test_concurrent_burst_from_one_peer_is_capped(temp_db, monkeypatch):
    """60 wrong passwords fired CONCURRENTLY from one peer. Checking the lock
    before the awaited DB read and recording after it let every one pass the
    check (60 x 401, no 429) and then trip the per-email cap, locking the owner
    out everywhere. Admission is now atomic: at most LOGIN_MAX_ATTEMPTS get a
    401, the rest 429, and the owner on another peer still signs in."""
    import httpx

    from src.api.main import app
    from src.core import settings

    monkeypatch.setattr(settings, "TRUSTED_PROXY_HOPS", 0)
    app.router.lifespan_context = _noop_lifespan  # type: ignore[assignment]
    _register_victim(_client_from_ip("192.0.2.1"))

    async def _burst() -> list[int]:
        transport = httpx.ASGITransport(app=app, client=("10.6.6.6", 40000))
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
            responses = await asyncio.gather(*[
                ac.post("/api/auth/login", json={"email": _LOGIN_EMAIL, "password": "wrong"})
                for _ in range(60)
            ])
        return [r.status_code for r in responses]

    codes = asyncio.run(_burst())
    assert set(codes) <= {401, 429}, codes
    assert codes.count(401) <= settings.LOGIN_MAX_ATTEMPTS, codes
    assert codes.count(429) == 60 - codes.count(401)
    assert _login(_client_from_ip("198.51.100.20"), _LOGIN_PW).status_code == 200


def test_ipv6_addresses_in_one_slash64_share_a_bucket(temp_db, monkeypatch):
    """One IPv6 host owns a whole /64: rotating through it buys no fresh
    email+IP buckets. A different /64 is a different bucket."""
    from src.core import settings

    monkeypatch.setattr(settings, "TRUSTED_PROXY_HOPS", 0)
    _register_victim(_client_from_ip("192.0.2.1"))
    codes = [
        _login(_client_from_ip(f"2001:db8:1:2::{i + 1:x}"), "wrong").status_code
        for i in range(10)
    ]
    n = settings.LOGIN_MAX_ATTEMPTS
    assert codes == [401] * n + [429] * (10 - n), codes
    assert _login(_client_from_ip("2001:db8:1:3::1"), _LOGIN_PW).status_code == 200


def test_successful_logins_do_not_count_toward_the_email_cap(temp_db, monkeypatch):
    """Every attempt is pre-counted; a successful one refunds its own count."""
    from src.core import settings

    monkeypatch.setattr(settings, "TRUSTED_PROXY_HOPS", 0)
    monkeypatch.setattr(settings, "LOGIN_EMAIL_MAX_ATTEMPTS", 2)
    _register_victim(_client_from_ip("192.0.2.1"))
    for a in range(4):
        assert _login(_client_from_ip(f"10.3.{a}.1"), _LOGIN_PW).status_code == 200
    assert _login(_client_from_ip("10.3.9.1"), "wrong").status_code == 401


def test_both_locks_return_the_same_429(temp_db, monkeypatch):
    """Which bucket refused is never revealed: email+IP and per-email locks
    give an identical status, body and Retry-After."""
    from src.core import settings

    monkeypatch.setattr(settings, "TRUSTED_PROXY_HOPS", 0)
    monkeypatch.setattr(settings, "LOGIN_EMAIL_MAX_ATTEMPTS", 2 * settings.LOGIN_MAX_ATTEMPTS)
    _register_victim(_client_from_ip("192.0.2.1"))
    a, b = _client_from_ip("10.4.0.1"), _client_from_ip("10.4.0.2")
    for c in (a, b):
        for _ in range(settings.LOGIN_MAX_ATTEMPTS):
            assert _login(c, "wrong").status_code == 401
    ip_lock = _login(a, "wrong")  # a's email+IP bucket is full
    email_lock = _login(_client_from_ip("10.4.0.3"), "wrong")  # fresh address, email cap full
    assert ip_lock.status_code == email_lock.status_code == 429
    assert ip_lock.json() == email_lock.json()
    assert ip_lock.headers["Retry-After"] == email_lock.headers["Retry-After"]


# ─── admit_login_attempt / refund_login_attempt (in-memory) ──────────────────


def test_admit_counts_both_buckets_and_refused_counts_neither():
    for _ in range(3):
        assert rate_limit.admit_login_attempt(
            "ip", "em", ip_max=3, email_max=10, window_seconds=900
        ).admitted
    refused = rate_limit.admit_login_attempt("ip", "em", ip_max=3, email_max=10, window_seconds=900)
    assert (refused.admitted, refused.lock) == (False, rate_limit.LOCK_EMAIL_IP)
    # The refused attempt was not added to the per-email bucket.
    assert not rate_limit.is_locked("em", max_failures=4, window_seconds=900)
    assert rate_limit.is_locked("em", max_failures=3, window_seconds=900)


def test_admit_refuses_on_email_cap_without_counting_ip():
    for i in range(2):
        rate_limit.admit_login_attempt(f"ip{i}", "em", ip_max=5, email_max=2, window_seconds=900)
    refused = rate_limit.admit_login_attempt("ip9", "em", ip_max=5, email_max=2, window_seconds=900)
    assert (refused.admitted, refused.lock) == (False, rate_limit.LOCK_EMAIL)
    assert not rate_limit.is_locked("ip9", max_failures=1, window_seconds=900)


def test_refund_removes_only_its_own_count():
    first = rate_limit.admit_login_attempt("ip", "em", ip_max=5, email_max=5, window_seconds=900)
    second = rate_limit.admit_login_attempt("ip2", "em", ip_max=5, email_max=5, window_seconds=900)
    rate_limit.refund_login_attempt("em", second)
    assert rate_limit.is_locked("em", max_failures=1, window_seconds=900)
    assert not rate_limit.is_locked("em", max_failures=2, window_seconds=900)
    rate_limit.refund_login_attempt("em", first)
    rate_limit.refund_login_attempt("em", first)  # second refund is a no-op
    assert not rate_limit.is_locked("em", max_failures=1, window_seconds=900)
