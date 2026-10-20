"""Tests for /livez, /readyz, and validate_required_env().

TDD for the Phase 2 ops-hardening health probes.
"""

import logging
import os
from unittest.mock import patch

import psycopg
import pytest
from httpx import ASGITransport, AsyncClient

from src.api.main import app
from src.core import settings
from src.repositories import pg
from src.repositories import pool as poolmod

# ---------------------------------------------------------------------------
# /livez — liveness probe (dependency-free)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_livez_returns_200():
    """Always 200, no DB or Redis touch."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/livez")
    assert resp.status_code == 200
    assert resp.json() == {"status": "alive"}


@pytest.mark.asyncio
async def test_livez_succeeds_even_when_db_is_broken():
    """Liveness must not fail because the DB is down."""
    # Make the pg connection raise — livez should still return 200.
    with patch("src.repositories.pg.connect") as mock_connect:
        mock_connect.side_effect = RuntimeError("DB is gone")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/livez")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# /readyz — readiness probe (Railway's deploy healthcheck since slice 21)
# ---------------------------------------------------------------------------


async def _get_readyz():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get("/api/readyz")


@pytest.mark.asyncio
async def test_readyz_test_mode_runs_select_1_on_the_real_db():
    """Test mode has no request-path pool: a real fresh connection, pool null."""
    resp = await _get_readyz()
    assert resp.status_code == 200
    assert resp.json() == {"status": "ready", "pool": None}


@pytest.mark.asyncio
async def test_readyz_returns_200_with_stats_from_the_request_pool(monkeypatch):
    """Production branch: borrows from the SAME pool get_request_db uses."""
    monkeypatch.setattr(pg, "TEST_MODE", False)
    await poolmod.close_pool()
    try:
        resp = await _get_readyz()
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "ready"
        stats = body["pool"]
        # The probe returned its connection, and nothing else holds one.
        assert stats["size"] >= 1
        assert stats["idle"] == stats["size"]
        assert stats["used"] == 0
        assert stats["waiting"] == 0
        assert stats["max"] == int(os.getenv("DB_POOL_MAX_SIZE", "10"))
        # And it really was the app-wide singleton the request path uses.
        assert poolmod._pool is not None
        assert poolmod.stats(poolmod._pool)["size"] == stats["size"]
    finally:
        await poolmod.close_pool()


@pytest.mark.asyncio
async def test_readyz_returns_503_pool_timeout_when_the_pool_is_pinned(monkeypatch, caplog):
    """Every pooled connection held -> 503 within the timeout, not a hang."""
    p = poolmod.build_pool(pg.DEFAULT_DSN, min_size=1, max_size=1)
    await p.open()
    await p.wait()
    monkeypatch.setattr(pg, "TEST_MODE", False)
    monkeypatch.setattr(poolmod, "_pool", p)
    monkeypatch.setattr(settings, "READYZ_POOL_TIMEOUT_S", 0.3)
    try:
        with caplog.at_level(logging.WARNING, logger="src.api.routes.health"):
            async with poolmod.acquire(p):  # pin the only connection
                resp = await _get_readyz()
        assert resp.status_code == 503
        assert resp.json() == {"status": "not_ready", "reason": "pool_timeout"}
        logged = [r for r in caplog.records if getattr(r, "event", "") == "readyz_not_ready"]
        assert [r.reason for r in logged] == ["pool_timeout"]
        assert logged[0].error == "PoolTimeout"
    finally:
        await p.close()


@pytest.mark.asyncio
async def test_readyz_returns_503_db_error_without_leaking_the_dsn(monkeypatch):
    """A DB failure answers 503 db_error; the driver's message stays out of the body."""

    async def _broken_pool():
        raise psycopg.OperationalError(
            "connection to server at db.internal failed: password=hunter2"
        )

    monkeypatch.setattr(pg, "TEST_MODE", False)
    monkeypatch.setattr(poolmod, "get_pool", _broken_pool)
    resp = await _get_readyz()
    assert resp.status_code == 503
    assert resp.json() == {"status": "not_ready", "reason": "db_error"}
    assert "hunter2" not in resp.text and "db.internal" not in resp.text


@pytest.mark.asyncio
async def test_readyz_returns_503_when_db_fails_in_test_mode():
    """Test-mode branch: a failing fresh connection is a 503 too, never a 500."""
    with patch("src.repositories.pg.connect", side_effect=RuntimeError("connection refused")):
        resp = await _get_readyz()
    assert resp.status_code == 503
    assert resp.json() == {"status": "not_ready", "reason": "db_error"}


# ---------------------------------------------------------------------------
# validate_required_env()
# ---------------------------------------------------------------------------


def test_validate_required_env_noop_in_dev(monkeypatch):
    """No-op when APP_ENV is not 'production' and RAILWAY_ENVIRONMENT unset."""
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)
    # Even with missing secrets, should not raise in dev.
    monkeypatch.delenv("SESSION_SECRET", raising=False)

    from src.core.settings import validate_required_env

    validate_required_env()  # must not raise


def test_validate_required_env_raises_in_production_with_missing_vars(monkeypatch):
    """Raises RuntimeError listing all missing vars when APP_ENV=production."""
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql://job360:job360dev@localhost:5433/job360")
    monkeypatch.delenv("SESSION_SECRET", raising=False)

    from src.core.settings import validate_required_env

    with pytest.raises(RuntimeError) as exc_info:
        validate_required_env()

    msg = str(exc_info.value)
    assert "SESSION_SECRET" in msg
    assert "Missing required environment variables for production" in msg


def test_validate_required_env_raises_for_railway_environment(monkeypatch):
    """Also gates on RAILWAY_ENVIRONMENT (Railway.app auto-sets this)."""
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://x:y@host/db")

    from src.core.settings import validate_required_env

    with pytest.raises(RuntimeError) as exc_info:
        validate_required_env()

    assert "SESSION_SECRET" in str(exc_info.value)


def test_validate_required_env_passes_when_all_set(monkeypatch):
    """No error when all required vars are present in production."""
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SESSION_SECRET", "a" * 32)
    monkeypatch.setenv("DATABASE_URL", "postgresql://job360:job360dev@localhost:5433/job360")

    from src.core.settings import validate_required_env

    validate_required_env()  # must not raise


def test_validate_required_env_error_message_is_human_readable(monkeypatch):
    """Error message lists ALL missing vars at once, not one at a time."""
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    from src.core.settings import validate_required_env

    with pytest.raises(RuntimeError) as exc_info:
        validate_required_env()

    msg = str(exc_info.value)
    # Both vars listed in one message.
    assert "SESSION_SECRET" in msg
    assert "DATABASE_URL" in msg
