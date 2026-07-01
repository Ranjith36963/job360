"""Tests for /livez, /readyz, and validate_required_env().

TDD for the Phase 2 ops-hardening health probes.
"""

import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.main import app

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
# /readyz — readiness probe
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_readyz_returns_200_when_db_ok(monkeypatch):
    """Healthy DB + no Redis URL -> 200 with db:ok, redis:skipped."""
    monkeypatch.delenv("REDIS_URL", raising=False)

    # Patch pg.connect() to succeed with a mock that allows ``SELECT 1``.
    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock(return_value=None)
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=False)

    with patch("src.repositories.pg.connect", return_value=mock_conn):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/readyz")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ready"
    assert data["checks"]["db"] == "ok"
    assert data["checks"]["redis"] == "skipped"


@pytest.mark.asyncio
async def test_readyz_returns_503_when_db_fails(monkeypatch):
    """DB error -> 503 with db:error."""
    monkeypatch.delenv("REDIS_URL", raising=False)

    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock(side_effect=RuntimeError("connection refused"))
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=False)

    with patch("src.repositories.pg.connect", return_value=mock_conn):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/readyz")

    assert resp.status_code == 503
    data = resp.json()
    assert data["status"] == "not ready"
    assert data["checks"]["db"] == "error"


@pytest.mark.asyncio
async def test_readyz_redis_skipped_when_url_unset(monkeypatch):
    """When REDIS_URL is absent, redis check is 'skipped' (not 'error')."""
    monkeypatch.delenv("REDIS_URL", raising=False)

    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock(return_value=None)
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=False)

    with patch("src.repositories.pg.connect", return_value=mock_conn):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/readyz")

    assert resp.json()["checks"]["redis"] == "skipped"


def _fake_redis_modules(mock_redis_client) -> dict:
    """Build a fake ``redis`` / ``redis.asyncio`` entry for sys.modules.

    We can't ``patch("redis.asyncio.from_url", ...)`` when redis isn't
    installed because ``patch`` tries to import the module first.  Instead
    we inject a minimal fake into ``sys.modules`` so the ``import redis.asyncio``
    inside health.py finds our mock.
    """
    fake_aioredis = MagicMock()
    fake_aioredis.from_url.return_value = mock_redis_client

    fake_redis = types.ModuleType("redis")
    fake_redis.asyncio = fake_aioredis  # type: ignore[attr-defined]

    return {"redis": fake_redis, "redis.asyncio": fake_aioredis}


@pytest.mark.asyncio
async def test_readyz_redis_ok_when_url_set_and_ping_succeeds(monkeypatch):
    """When REDIS_URL is set and Redis pings OK, check is 'ok'."""
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")

    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock(return_value=None)
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=False)

    mock_redis = AsyncMock()
    mock_redis.ping = AsyncMock(return_value=True)
    mock_redis.aclose = AsyncMock(return_value=None)

    with patch("src.repositories.pg.connect", return_value=mock_conn):
        with patch.dict(sys.modules, _fake_redis_modules(mock_redis)):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/readyz")

    assert resp.status_code == 200
    assert resp.json()["checks"]["redis"] == "ok"


@pytest.mark.asyncio
async def test_readyz_redis_error_when_ping_fails(monkeypatch):
    """When REDIS_URL is set but Redis is unreachable, check is 'error' and status is 503."""
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")

    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock(return_value=None)
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=False)

    mock_redis = AsyncMock()
    mock_redis.ping = AsyncMock(side_effect=ConnectionRefusedError("no redis"))
    mock_redis.aclose = AsyncMock(return_value=None)

    with patch("src.repositories.pg.connect", return_value=mock_conn):
        with patch.dict(sys.modules, _fake_redis_modules(mock_redis)):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/readyz")

    assert resp.status_code == 503
    assert resp.json()["checks"]["redis"] == "error"


# ---------------------------------------------------------------------------
# validate_required_env()
# ---------------------------------------------------------------------------


def test_validate_required_env_noop_in_dev(monkeypatch):
    """No-op when APP_ENV is not 'production' and RAILWAY_ENVIRONMENT unset."""
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)
    # Even with missing secrets, should not raise in dev.
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    monkeypatch.delenv("CHANNEL_ENCRYPTION_KEY", raising=False)

    from src.core.settings import validate_required_env

    validate_required_env()  # must not raise


def test_validate_required_env_raises_in_production_with_missing_vars(monkeypatch):
    """Raises RuntimeError listing all missing vars when APP_ENV=production."""
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql://job360:job360dev@localhost:5433/job360")
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    monkeypatch.delenv("CHANNEL_ENCRYPTION_KEY", raising=False)

    from src.core.settings import validate_required_env

    with pytest.raises(RuntimeError) as exc_info:
        validate_required_env()

    msg = str(exc_info.value)
    assert "SESSION_SECRET" in msg
    assert "CHANNEL_ENCRYPTION_KEY" in msg
    assert "Missing required environment variables for production" in msg


def test_validate_required_env_raises_for_railway_environment(monkeypatch):
    """Also gates on RAILWAY_ENVIRONMENT (Railway.app auto-sets this)."""
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    monkeypatch.delenv("CHANNEL_ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://x:y@host/db")

    from src.core.settings import validate_required_env

    with pytest.raises(RuntimeError) as exc_info:
        validate_required_env()

    assert "SESSION_SECRET" in str(exc_info.value)


def test_validate_required_env_passes_when_all_set(monkeypatch):
    """No error when all required vars are present in production."""
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SESSION_SECRET", "a" * 32)
    monkeypatch.setenv("CHANNEL_ENCRYPTION_KEY", "b" * 32)
    monkeypatch.setenv("DATABASE_URL", "postgresql://job360:job360dev@localhost:5433/job360")

    from src.core.settings import validate_required_env

    validate_required_env()  # must not raise


def test_validate_required_env_error_message_is_human_readable(monkeypatch):
    """Error message lists ALL missing vars at once, not one at a time."""
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    monkeypatch.delenv("CHANNEL_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    from src.core.settings import validate_required_env

    with pytest.raises(RuntimeError) as exc_info:
        validate_required_env()

    msg = str(exc_info.value)
    # All three vars listed in one message.
    assert "SESSION_SECRET" in msg
    assert "CHANNEL_ENCRYPTION_KEY" in msg
    assert "DATABASE_URL" in msg
