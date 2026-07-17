"""Search lifecycle audit (full-lifecycle logging gap F).

Starting a search — the core user action — now emits a `search_started` audit
line (and `search_completed` / `search_failed` from the background task). The
audit logger is propagate=False, so we attach caplog's handler directly.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from migrations import runner
from src.repositories import pg, pgsync
from src.services.channels import crypto


@asynccontextmanager
async def _noop_lifespan(app):
    yield


@pytest.fixture
def temp_db(monkeypatch, tmp_path):
    db_path = str(tmp_path / "test.db")

    async def _bootstrap():
        async with pg.connect(db_path) as db:
            await db.executescript(
                """
                CREATE TABLE user_actions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, job_id INTEGER NOT NULL,
                    action TEXT NOT NULL, notes TEXT DEFAULT '', created_at TEXT NOT NULL, UNIQUE(job_id)
                );
                CREATE TABLE applications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, job_id INTEGER NOT NULL,
                    stage TEXT NOT NULL DEFAULT 'applied', notes TEXT DEFAULT '',
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(job_id)
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
    for mod in (settings, dependencies, auth_deps, auth_route):
        monkeypatch.setattr(mod, "DB_PATH", patched, raising=True)
    crypto.set_test_key(Fernet.generate_key().decode("ascii"))
    monkeypatch.setenv("SESSION_SECRET", "test-secret-" + "x" * 40)
    yield db_path


@pytest.fixture
def client(temp_db):
    from src.api.main import app

    app.router.lifespan_context = _noop_lifespan  # type: ignore[assignment]
    with TestClient(app) as c:
        yield c


def _register_verified(client, db_path, email):
    r = client.post("/api/auth/register", json={"email": email, "password": "Correct-Horse-9"})
    assert r.status_code == 201, r.text
    con = pgsync.connect(db_path)
    con.execute("UPDATE users SET email_verified_at=datetime('now') WHERE email=?", (email,))
    con.commit()
    con.close()


def test_search_emits_started_audit(client, temp_db, caplog, monkeypatch):
    async def _fake_run_search(**kwargs):
        return {"new_jobs": 0}

    monkeypatch.setattr("src.api.routes.search.run_search", _fake_run_search)

    audit = logging.getLogger("job360.audit")
    audit.addHandler(caplog.handler)
    audit.setLevel(logging.INFO)
    caplog.handler.setLevel(logging.INFO)
    try:
        _register_verified(client, temp_db, "searchaudit@example.com")
        r = client.post("/api/search")
        assert r.status_code == 200, r.text
    finally:
        audit.removeHandler(caplog.handler)

    events = {getattr(rec, "event", "") for rec in caplog.records}
    assert "search_started" in events, f"search_started not audited; saw {events}"
