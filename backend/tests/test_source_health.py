"""/api/runs/source-health endpoint tests (Phase −2 item D).

Covers:
- Empty: fresh install → empty source list, runs_considered=0
- Aggregation: per_source counts + errors + durations across multiple runs
- Traffic-light classification: 3 consecutive 0-job runs → critical
- Authentication: anonymous → 401
- Per-user scoping: only the caller's run_log rows are aggregated
"""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from migrations import runner
from src.repositories import pg
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


async def _seed_run(
    db_path,
    user_id,
    *,
    per_source: dict,
    per_source_errors: dict | None = None,
    per_source_duration: dict | None = None,
    timestamp: str | None = None,
):
    """Insert a run_log row directly so we can simulate run history without
    actually firing the pipeline."""
    now = timestamp or datetime.now(timezone.utc).isoformat()
    async with pg.connect(db_path) as db:
        await db.execute(
            """
            INSERT INTO run_log(
                timestamp, total_found, new_jobs, sources_queried,
                per_source, run_uuid, per_source_errors,
                per_source_duration, total_duration, user_id
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now,
                sum(v for v in per_source.values() if isinstance(v, int)),
                0,
                len(per_source),
                json.dumps(per_source),
                None,
                json.dumps(per_source_errors) if per_source_errors is not None else None,
                json.dumps(per_source_duration) if per_source_duration is not None else None,
                None,
                user_id,
            ),
        )
        await db.commit()


def _register(client, email="alice@example.com", password="s3cretpassword"):
    r = client.post("/api/auth/register", json={"email": email, "password": password})
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ─── Anonymous → 401 ────────────────────────────────────────────────────────


def test_source_health_requires_session(client):
    r = client.get("/api/runs/source-health")
    assert r.status_code == 401


# ─── Empty install → empty result ───────────────────────────────────────────


def test_source_health_empty_when_no_runs(client):
    _register(client)
    r = client.get("/api/runs/source-health")
    assert r.status_code == 200
    body = r.json()
    assert body == {"sources": [], "runs_considered": 0}


# ─── Aggregation across multiple runs ───────────────────────────────────────


def test_source_health_aggregates_counts_and_errors(client, temp_db):
    user_id = _register(client)

    asyncio.run(_seed_run(
        temp_db, user_id,
        per_source={"reed": 10, "linkedin": 0, "greenhouse": 25},
        per_source_errors={"linkedin": "regex returned no matches"},
        per_source_duration={"reed": 1.5, "linkedin": 0.2, "greenhouse": 3.0},
        timestamp="2026-05-25T10:00:00Z",
    ))
    asyncio.run(_seed_run(
        temp_db, user_id,
        per_source={"reed": 8, "linkedin": 5, "greenhouse": 30},
        per_source_duration={"reed": 1.7, "linkedin": 0.8, "greenhouse": 2.9},
        timestamp="2026-05-26T10:00:00Z",
    ))

    r = client.get("/api/runs/source-health")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["runs_considered"] == 2

    by_name = {s["source"]: s for s in body["sources"]}
    assert set(by_name) == {"reed", "linkedin", "greenhouse"}

    reed = by_name["reed"]
    assert reed["runs_observed"] == 2
    assert reed["runs_zero_jobs"] == 0
    assert reed["runs_errored"] == 0
    assert reed["avg_duration_seconds"] == 1.6   # mean(1.5, 1.7)
    assert reed["health"] == "ok"

    linkedin = by_name["linkedin"]
    assert linkedin["runs_observed"] == 2
    assert linkedin["runs_zero_jobs"] == 1
    assert linkedin["runs_errored"] == 1
    # Most-recent run is "linkedin: 5"; last_job_count tracks newest-first
    assert linkedin["last_job_count"] == 5
    assert linkedin["last_error"] == "regex returned no matches"


# ─── Traffic light: 3+ consecutive zero-runs → critical ─────────────────────


def test_source_health_critical_on_3_consecutive_zeros(client, temp_db):
    user_id = _register(client)
    for ts in ("2026-05-26T10:00:00Z", "2026-05-25T10:00:00Z", "2026-05-24T10:00:00Z"):
        asyncio.run(_seed_run(
            temp_db, user_id,
            per_source={"linkedin": 0, "reed": 10},
            timestamp=ts,
        ))

    r = client.get("/api/runs/source-health")
    body = r.json()
    by_name = {s["source"]: s for s in body["sources"]}
    assert by_name["linkedin"]["health"] == "critical"
    assert by_name["reed"]["health"] == "ok"


# ─── Per-user isolation: another user's runs don't leak in ──────────────────


def test_source_health_scoped_per_user(client, temp_db):
    alice_id = _register(client, "alice@example.com")
    asyncio.run(_seed_run(
        temp_db, alice_id,
        per_source={"reed": 5},
        timestamp="2026-05-26T10:00:00Z",
    ))

    # Register Bob (TestClient now holds Bob's session cookie).
    bob_id = _register(client, "bob@example.com")
    asyncio.run(_seed_run(
        temp_db, bob_id,
        per_source={"linkedin": 12},
        timestamp="2026-05-26T11:00:00Z",
    ))

    # Bob's view: only his run is aggregated.
    r = client.get("/api/runs/source-health")
    body = r.json()
    assert body["runs_considered"] == 1
    names = [s["source"] for s in body["sources"]]
    assert names == ["linkedin"]


# ─── Stable ordering: critical → warning → ok, alpha within ─────────────────


def test_source_health_results_ordered_by_priority(client, temp_db):
    user_id = _register(client)
    for ts in ("2026-05-26T10:00:00Z", "2026-05-25T10:00:00Z", "2026-05-24T10:00:00Z"):
        asyncio.run(_seed_run(
            temp_db, user_id,
            per_source={"linkedin": 0, "reed": 10, "greenhouse": 25},
            timestamp=ts,
        ))

    r = client.get("/api/runs/source-health")
    body = r.json()
    healths = [s["health"] for s in body["sources"]]
    # linkedin (critical) must come before the ok ones
    assert healths[0] == "critical"
    assert healths[1:] == ["ok", "ok"]
    # Alpha within tier: greenhouse < reed
    ok_names = [s["source"] for s in body["sources"] if s["health"] == "ok"]
    assert ok_names == ["greenhouse", "reed"]
