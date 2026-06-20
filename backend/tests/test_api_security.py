"""Step-1 Cohort C — security/correctness tests for B9 + B12.

B9: ``JobDatabase.get_recent_jobs`` must NOT serve rows whose
    ``staleness_state='expired'``. ``NULL`` is treated as
    "not yet classified" (active) — defence-in-depth until the
    staleness writer lands in Batch S1.5.

B12: ``POST /search`` must enforce ``MAX_CONCURRENT_SEARCHES_PER_USER``.
    A 4th concurrent run from the same user returns HTTP 429. Other
    users are unaffected by one user's burst.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from migrations import runner
from src.repositories.database import JobDatabase
from src.services.channels import crypto

# ---------------------------------------------------------------------------
# B9 — get_recent_jobs filters expired rows
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expired_jobs_filtered():
    """Active + NULL rows surface; ``staleness_state='expired'`` rows do not."""
    db = JobDatabase(":memory:")
    await db.init_db()
    try:
        now = datetime.now(timezone.utc).isoformat()
        # Three rows — same date, distinct (company, title) so the UNIQUE
        # index doesn't collapse them.
        await db._conn.execute(
            "INSERT INTO jobs (title, company, location, apply_url, source, "
            "date_found, normalized_company, normalized_title, first_seen, "
            "staleness_state) VALUES (?, ?, '', ?, 'reed', ?, ?, ?, ?, 'active')",
            ("Active Role", "ActiveCo", "https://e/1", now, "activeco", "active role", now),
        )
        await db._conn.execute(
            "INSERT INTO jobs (title, company, location, apply_url, source, "
            "date_found, normalized_company, normalized_title, first_seen, "
            "staleness_state) VALUES (?, ?, '', ?, 'reed', ?, ?, ?, ?, 'expired')",
            ("Expired Role", "ExpiredCo", "https://e/2", now, "expiredco", "expired role", now),
        )
        await db._conn.execute(
            "INSERT INTO jobs (title, company, location, apply_url, source, "
            "date_found, normalized_company, normalized_title, first_seen, "
            "staleness_state) VALUES (?, ?, '', ?, 'reed', ?, ?, ?, ?, NULL)",
            ("Null Role", "NullCo", "https://e/3", now, "nullco", "null role", now),
        )
        await db._conn.commit()

        rows = await db.get_recent_jobs(days=30)
        titles = {r["title"] for r in rows}

        assert "Active Role" in titles, "active row must be served"
        assert "Null Role" in titles, "NULL staleness must be treated as active"
        assert "Expired Role" not in titles, "expired row must be filtered out"
        assert len(rows) == 2
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# C-1 — get_job_by_id_with_enrichment must mirror the staleness filter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_job_by_id_with_enrichment_filters_expired():
    """A single-job lookup must NOT surface a ghost-detected expired posting.

    Symmetric with test_expired_jobs_filtered: the list path
    (get_recent_jobs_with_enrichment) and the by-id path
    (get_job_by_id_with_enrichment) must apply the same staleness predicate
    so the JobResponse for /jobs/:id can't show what /jobs hides.
    """
    db = JobDatabase(":memory:")
    await db.init_db()
    try:
        now = datetime.now(timezone.utc).isoformat()
        await db._conn.execute(
            "INSERT INTO jobs (title, company, location, apply_url, source, "
            "date_found, normalized_company, normalized_title, first_seen, "
            "staleness_state) VALUES (?, ?, '', ?, 'reed', ?, ?, ?, ?, 'active')",
            ("Active Role", "ActiveCo", "https://e/a", now, "activeco", "active role", now),
        )
        await db._conn.execute(
            "INSERT INTO jobs (title, company, location, apply_url, source, "
            "date_found, normalized_company, normalized_title, first_seen, "
            "staleness_state) VALUES (?, ?, '', ?, 'reed', ?, ?, ?, ?, 'expired')",
            ("Expired Role", "ExpiredCo", "https://e/x", now, "expiredco", "expired role", now),
        )
        await db._conn.commit()

        active_id = (await (await db._conn.execute("SELECT id FROM jobs WHERE title='Active Role'")).fetchone())[0]
        expired_id = (await (await db._conn.execute("SELECT id FROM jobs WHERE title='Expired Role'")).fetchone())[0]

        active_row = await db.get_job_by_id_with_enrichment(active_id)
        expired_row = await db.get_job_by_id_with_enrichment(expired_id)

        assert active_row is not None
        assert active_row["title"] == "Active Role"
        assert expired_row is None, "expired single-row lookup must return None"
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# B12 — per-user concurrent search cap
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _noop_lifespan(app):
    yield


async def _bootstrap_db(db_path: str) -> None:
    db = JobDatabase(db_path)
    await db.init_db()
    await db.close()
    await runner.up(db_path)


def _register_user_and_get_cookie(app, email: str) -> str:
    sync_client = TestClient(app)
    r = sync_client.post(
        "/api/auth/register",
        json={"email": email, "password": "s3cretpassword"},
    )
    assert r.status_code == 201, r.text
    cookie = sync_client.cookies.get("job360_session")
    sync_client.close()
    assert cookie
    # #15: /search now requires a verified email; this test checks the
    # per-user concurrency cap, not verification — mark the user verified.
    import sqlite3 as _sqlite3

    import src.core.settings as _settings

    _c = _sqlite3.connect(str(_settings.DB_PATH))
    _c.execute(
        "UPDATE users SET email_verified_at = ? WHERE email = ?",
        ("2026-01-01T00:00:00Z", email),
    )
    _c.commit()
    _c.close()
    return cookie


@pytest.mark.asyncio
async def test_search_concurrent_cap_per_user(monkeypatch, tmp_path):
    """3 concurrent POST /search succeed; 4th returns 429; user 2 unaffected."""
    db_path = tmp_path / "test.db"
    await _bootstrap_db(str(db_path))

    from src.api import auth_deps, dependencies
    from src.api.routes import auth as auth_route
    from src.api.routes import channels as channels_route
    from src.api.routes import search as search_route
    from src.core import settings

    monkeypatch.setattr(settings, "DB_PATH", db_path, raising=True)
    monkeypatch.setattr(dependencies, "DB_PATH", db_path, raising=True)
    monkeypatch.setattr(auth_deps, "DB_PATH", db_path, raising=True)
    monkeypatch.setattr(auth_route, "DB_PATH", db_path, raising=True)
    monkeypatch.setattr(channels_route, "DB_PATH", db_path, raising=True)
    monkeypatch.setattr(dependencies, "_db", None, raising=False)

    crypto.set_test_key(Fernet.generate_key().decode("ascii"))
    monkeypatch.setenv("SESSION_SECRET", "test-secret-" + "z" * 40)
    monkeypatch.setattr(settings, "MAX_CONCURRENT_SEARCHES_PER_USER", 3, raising=True)

    # Replace run_search with a slow stub so dispatched runs stay in
    # `running` for the duration of the test — letting us prove the cap
    # counts in-flight runs, not completed ones.
    release = asyncio.Event()

    async def _slow_run_search(*args, **kwargs):
        await release.wait()
        return {}

    monkeypatch.setattr(search_route, "run_search", _slow_run_search, raising=True)
    # Reset the module-level _runs dict between tests for isolation.
    search_route._runs.clear()

    from src.api.main import app

    app.router.lifespan_context = _noop_lifespan  # type: ignore[assignment]

    cookie_u1 = _register_user_and_get_cookie(app, "u1@example.com")
    cookie_u2 = _register_user_and_get_cookie(app, "u2@example.com")

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            cookies={"job360_session": cookie_u1},
        ) as u1:
            r1 = await u1.post("/api/search")
            r2 = await u1.post("/api/search")
            r3 = await u1.post("/api/search")
            assert r1.status_code == 200, r1.text
            assert r2.status_code == 200, r2.text
            assert r3.status_code == 200, r3.text

            # 4th — must be rate-limited
            r4 = await u1.post("/api/search")
            assert r4.status_code == 429, r4.text
            assert "Too many concurrent searches" in r4.json()["detail"]

        # User 2 with their own cookie — cap is per-user, not global.
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            cookies={"job360_session": cookie_u2},
        ) as u2:
            r_u2 = await u2.post("/api/search")
            assert r_u2.status_code == 200, r_u2.text
    finally:
        # Release any in-flight slow-run tasks so the event loop can shut
        # down cleanly without dangling background coroutines.
        release.set()
        # Give dispatched tasks a tick to observe the event.
        await asyncio.sleep(0)
        search_route._runs.clear()
