"""IDOR regression tests for the per-user routes.

Batch 2 shipped the schema (user_actions + applications carry user_id +
UNIQUE(user_id, job_id)) but the repo layer and route handlers were
tenant-blind — two users could alias-collapse onto the placeholder tenant.
Batch 3.5 Deliverable C added `Depends(require_user)` to every per-user
endpoint and threaded user_id through. This file proves:
  1. Unauthenticated requests → 401.
  2. User A cannot read or mutate user B's application / profile rows.
  3. User A positive control — their own row round-trips fine.

Slice 5 (#483) removed the save/dismiss action routes and the search routes
this file also covered; the route list below is the surviving per-user
surface, including `GET /api/applications/job/{id}`, which replaced the
PUBLIC `GET /api/jobs/{id}` (spec S1 — removed, not re-scoped).

Per CLAUDE.md rule #12 + the audit checklist in docs/batch_prompts.md
§tenant-isolation.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from migrations import runner
from src.repositories import pg


@asynccontextmanager
async def _noop_lifespan(app):
    yield


async def _seed_job_rows(db_path: str) -> list[int]:
    """Insert two shared-catalog jobs; return their ids.

    Per CLAUDE.md rule #10, `jobs` is the shared catalog — no user_id.
    """
    now = datetime.now(timezone.utc).isoformat()
    async with pg.connect(db_path) as db:
        cur = await db.execute(
            """INSERT INTO jobs
               (title, company, location, apply_url, source, date_found,
                normalized_company, normalized_title, first_seen)
               VALUES (?, ?, '', ?, 'test', ?, ?, ?, ?)""",
            ("AI Engineer", "Acme", "https://acme.test/a", now,
             "acme", "ai engineer", now),
        )
        await db.commit()
        job_a = cur.lastrowid
        cur = await db.execute(
            """INSERT INTO jobs
               (title, company, location, apply_url, source, date_found,
                normalized_company, normalized_title, first_seen)
               VALUES (?, ?, '', ?, 'test', ?, ?, ?, ?)""",
            ("ML Engineer", "Beta", "https://beta.test/b", now,
             "beta", "ml engineer", now),
        )
        await db.commit()
        job_b = cur.lastrowid
    return [job_a, job_b]


@pytest.fixture
def api(monkeypatch, tmp_path):
    db_path = str(tmp_path / "test.db")

    async def _bootstrap():
        # Let JobDatabase.init_db() create the jobs + user_actions +
        # applications schema, then the migration runner layers on auth
        # tables + rebuilds user_actions / applications with user_id.
        from src.repositories.database import JobDatabase
        db = JobDatabase(db_path)
        await db.init_db()
        await db.close()
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

    # Reset the JobDatabase singleton so it lazy-binds to the patched path
    monkeypatch.setattr(dependencies, "_db", None, raising=False)

    monkeypatch.setenv("SESSION_SECRET", "test-secret-" + "y" * 40)

    # Seed two shared-catalog jobs we can reference by id.
    job_ids = asyncio.run(_seed_job_rows(db_path))

    # Redirect DB_PATH on every module that captured it at import time. See
    # conftest.authenticated_async_context for the full rationale: a
    # ``from src.core.settings import DB_PATH`` binds the value, so patching
    # settings alone misses importers like services/profile/storage.py and
    # profile queries hit the production DB.
    import sys as _sys

    from src.api.main import app

    for _mod in list(_sys.modules.values()):
        _name = getattr(_mod, "__name__", "")
        if _name.startswith(("src.", "migrations")) and getattr(_mod, "DB_PATH", None) is not None:
            monkeypatch.setattr(_mod, "DB_PATH", patched, raising=False)

    app.router.lifespan_context = _noop_lifespan  # type: ignore[assignment]
    client = TestClient(app)
    client.__job_ids__ = job_ids  # type: ignore[attr-defined]
    return client


def _register(client, email, password="s3cretpassword"):
    r = client.post(
        "/api/auth/register", json={"email": email, "password": password}
    )
    assert r.status_code == 201, r.text
    # #15: app routes (e.g. /search) now require a verified email. These tests
    # exercise IDOR/auth scoping, not verification, so mark the user verified.
    import src.core.settings as _settings
    from src.repositories import pgsync as _sqlite3

    _c = _sqlite3.connect(str(_settings.DB_PATH))
    _c.execute(
        "UPDATE users SET email_verified_at = ? WHERE email = ?",
        ("2026-01-01T00:00:00Z", email),
    )
    _c.commit()
    _c.close()
    # M2 — register no longer auto-logs-in; sign in so the client is authenticated.
    lr = client.post("/api/auth/login", json={"email": email, "password": password})
    assert lr.status_code == 200, lr.text


# ---------------------------------------------------------------------------
# Unauthenticated requests → 401
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("method,path", [
    # Slice 5 (#483) deleted the last route that served per-user data
    # PUBLICLY (`GET /api/jobs/{id}`) rather than re-scoping it — a brought ad
    # is one user's data (spec S1). Its per-user replacement is in this list.
    ("POST", "/api/jobs/bring"),
    ("GET",  "/api/applications"),
    ("GET",  "/api/applications/job/1"),
    ("GET",  "/api/receipts"),
    ("GET",  "/api/whats-new"),
    # Batch 3.5.1 — close the profile + search gaps the 2026-04-19
    # CurrentStatus re-audit §7 identified.
    ("GET",  "/api/profile"),
    ("POST", "/api/profile"),
    ("POST", "/api/profile/linkedin"),
    ("POST", "/api/profile/github"),
])
def test_per_user_endpoint_requires_auth(api, method, path):
    if method == "POST" and "advance" in path:
        r = api.request(method, path, json={"stage": "interview"})
    else:
        r = api.request(method, path)
    assert r.status_code == 401, (
        f"{method} {path} returned {r.status_code} instead of 401: {r.text}"
    )


# ---------------------------------------------------------------------------
# Batch 3.5.2 — HTTP-level profile tenancy (per-user user_profiles table)
# ---------------------------------------------------------------------------
#
# Note: the low-level user_profiles table tests live in
# tests/test_profile_storage.py. These HTTP-level tests prove the
# end-to-end path from request -> require_user -> save_profile(user.id)
# -> load_profile(user.id). Covers the Deliverable C wiring.


def _upsert_prefs_for_current_user(api, titles, skills):
    """POST /api/profile with preferences only (no CV file) — keeps the test
    offline and avoids the CV-parser / LLM dependency."""
    import json as _json
    prefs = {
        "target_job_titles": titles,
        "additional_skills": skills,
        "excluded_skills": [],
        "preferred_locations": [],
        "industries": [],
        "salary_min": None,
        "salary_max": None,
        "work_arrangement": "",
        "experience_level": "",
        "negative_keywords": [],
        "about_me": "",
        "github_username": "",
    }
    r = api.post("/api/profile", data={"preferences": _json.dumps(prefs)})
    assert r.status_code == 200, r.text
    return r


def test_profile_isolation_alice_not_visible_to_bob(api):
    """Alice saves a profile; Bob hitting GET /api/profile gets 404
    (his row doesn't exist), NOT Alice's data."""
    _register(api, "alice@example.com")
    _upsert_prefs_for_current_user(api, ["Alice's Job"], ["python"])

    r_alice = api.get("/api/profile")
    assert r_alice.status_code == 200
    alice_body = r_alice.json()
    assert "Alice's Job" in alice_body["preferences"]["target_job_titles"]

    # Switch to Bob
    api.post("/api/auth/logout")
    api.cookies.clear()
    _register(api, "bob@example.com")

    r_bob = api.get("/api/profile")
    # Bob has no profile row yet — 404, NOT Alice's body
    assert r_bob.status_code == 404, (
        f"Bob saw alice's profile instead of 404: {r_bob.status_code} / {r_bob.text}"
    )


def test_profile_upsert_per_user_does_not_overwrite_peer(api):
    """Alice saves; Bob saves with different data; Alice's data survives."""
    _register(api, "alice@example.com")
    _upsert_prefs_for_current_user(api, ["Data Engineer"], ["sql"])

    api.post("/api/auth/logout")
    api.cookies.clear()
    _register(api, "bob@example.com")
    _upsert_prefs_for_current_user(api, ["ML Engineer"], ["pytorch"])

    # Alice logs back in and checks her row is untouched
    api.post("/api/auth/logout")
    api.cookies.clear()
    r_login = api.post("/api/auth/login", json={
        "email": "alice@example.com", "password": "s3cretpassword"
    })
    assert r_login.status_code == 200, r_login.text

    r_alice = api.get("/api/profile")
    assert r_alice.status_code == 200
    titles = r_alice.json()["preferences"]["target_job_titles"]
    assert "Data Engineer" in titles, (
        f"Bob's write clobbered Alice's row: titles={titles}"
    )
    assert "ML Engineer" not in titles, (
        f"Bob's titles leaked into Alice's profile: titles={titles}"
    )


def test_profile_github_endpoint_is_per_user(api, monkeypatch):
    """Alice posts a github username; Bob's profile must not get enriched."""
    # Stub fetch_github_profile + enrich_cv_from_github so the test stays offline.
    async def _fake_fetch(username):
        return {"languages": {"Python": 1}, "topics": [], "repos": []}

    def _fake_enrich(cv_data, github_data):
        # Return a copy with github_languages set — just enough to verify
        # per-user write without a real GitHub API call.
        import dataclasses
        return dataclasses.replace(cv_data, github_languages={"Python": 1})

    import src.api.routes.profile as profile_route
    monkeypatch.setattr(profile_route, "fetch_github_profile", _fake_fetch)
    monkeypatch.setattr(profile_route, "enrich_cv_from_github", _fake_enrich)

    _register(api, "alice@example.com")
    r = api.post("/api/profile/github", data={"username": "alice-gh"})
    assert r.status_code == 200, r.text

    # Bob — no /profile/github call made yet
    api.post("/api/auth/logout")
    api.cookies.clear()
    _register(api, "bob@example.com")

    r_bob = api.get("/api/profile")
    # Bob has no row yet, so 404 — Alice's GitHub enrichment must not bleed over
    assert r_bob.status_code == 404


# ---------------------------------------------------------------------------
# N9 — profile-version restore/diff endpoints must not leak across users
# ---------------------------------------------------------------------------


def test_profile_version_restore_is_scoped_by_user(api):
    """Bob restoring Alice's profile-version id must 404, not restore her data
    into his own profile (rules #12/#25 — never trust an id alone)."""
    _register(api, "alice@example.com")
    _upsert_prefs_for_current_user(api, ["Alice's Job"], ["python"])
    alice_version_id = api.get("/api/profile/versions").json()["versions"][0]["id"]

    api.post("/api/auth/logout")
    api.cookies.clear()
    _register(api, "bob@example.com")

    r_bob = api.post(f"/api/profile/versions/{alice_version_id}/restore")
    assert r_bob.status_code == 404, (
        f"Bob restored alice's version instead of 404: {r_bob.status_code} / {r_bob.text}"
    )
    # Bob still has no profile of his own — the restore must not have gone through
    r_bob_profile = api.get("/api/profile")
    assert r_bob_profile.status_code == 404


def test_profile_version_diff_is_scoped_by_user(api):
    """Bob diffing (his own version, Alice's version id) must 404 — the diff
    endpoint must not resolve a version id belonging to another user."""
    _register(api, "alice@example.com")
    _upsert_prefs_for_current_user(api, ["Alice's Job"], ["python"])
    alice_version_id = api.get("/api/profile/versions").json()["versions"][0]["id"]

    api.post("/api/auth/logout")
    api.cookies.clear()
    _register(api, "bob@example.com")
    _upsert_prefs_for_current_user(api, ["Bob's Job"], ["sql"])
    bob_version_id = api.get("/api/profile/versions").json()["versions"][0]["id"]

    # Bob referencing alice's version id in either slot must 404.
    r_bob_1 = api.get(f"/api/profile/versions/{alice_version_id}/diff/{bob_version_id}")
    assert r_bob_1.status_code == 404
    r_bob_2 = api.get(f"/api/profile/versions/{bob_version_id}/diff/{alice_version_id}")
    assert r_bob_2.status_code == 404
