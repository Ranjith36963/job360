"""User-action audit events (full-lifecycle logging piece C).

The audit log used to carry only auth events. Now the key "user did something"
mutations — liking a job, adding/advancing a pipeline application — also emit an
audit line (who / what / when), so we can reconstruct a user's actions.

The audit logger is `propagate=False`, so we attach caplog's handler to it
directly to capture its records.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

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
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id INTEGER NOT NULL, action TEXT NOT NULL,
                    notes TEXT DEFAULT '', created_at TEXT NOT NULL, UNIQUE(job_id)
                );
                CREATE TABLE applications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id INTEGER NOT NULL, stage TEXT NOT NULL DEFAULT 'applied',
                    notes TEXT DEFAULT '', created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL, UNIQUE(job_id)
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

    # A job for the user to act on (id = 1).
    now = datetime.now(timezone.utc).isoformat()
    con = pgsync.connect(db_path)
    con.execute(
        "INSERT INTO jobs (title, company, location, apply_url, source, date_found, "
        "normalized_company, normalized_title, first_seen, staleness_state) "
        "VALUES (?, ?, '', ?, 'reed', ?, ?, ?, ?, 'active')",
        ("Engineer", "Acme", "https://e/1", now, "acme", "engineer", now),
    )
    con.commit()
    con.close()
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
    # M2 — register no longer auto-logs-in; sign in so the client is authenticated.
    lr = client.post("/api/auth/login", json={"email": email, "password": "Correct-Horse-9"})
    assert lr.status_code == 200, lr.text


def test_like_and_pipeline_emit_audit_events(client, temp_db, caplog):
    audit = logging.getLogger("job360.audit")
    audit.addHandler(caplog.handler)
    audit.setLevel(logging.INFO)
    caplog.handler.setLevel(logging.INFO)
    try:
        _register_verified(client, temp_db, "auditme@example.com")
        r = client.post("/api/jobs/1/action", json={"action": "liked"})
        assert r.status_code == 200, r.text
        r = client.post("/api/pipeline/1", json={})
        assert r.status_code in (200, 201), r.text
    finally:
        audit.removeHandler(caplog.handler)

    events = {getattr(rec, "event", "") for rec in caplog.records}
    assert "job_action" in events, f"job_action not audited; saw {events}"
    assert "pipeline_create" in events, f"pipeline_create not audited; saw {events}"
