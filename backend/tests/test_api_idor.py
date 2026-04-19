"""IDOR regression tests for legacy per-user routes.

Batch 2 shipped the schema (user_actions + applications now carry
user_id + UNIQUE(user_id, job_id)) but the repo layer and route
handlers were tenant-blind — two users hitting /api/jobs/{id}/action
would alias-collapse onto the placeholder tenant.

Batch 3.5 Deliverable C adds `Depends(require_user)` to every per-user
endpoint and threads user_id through JobDatabase action+application
methods. This file proves:
  1. Unauthenticated requests → 401.
  2. User A cannot read or mutate user B's action / application rows.
  3. User A positive control — their own row round-trips fine.

Per CLAUDE.md rule #12 + the audit checklist in docs/batch_prompts.md
§tenant-isolation.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import aiosqlite
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from migrations import runner
from src.services.channels import crypto


@asynccontextmanager
async def _noop_lifespan(app):
    yield


async def _seed_job_rows(db_path: str) -> list[int]:
    """Insert two shared-catalog jobs; return their ids.

    Per CLAUDE.md rule #10, `jobs` is the shared catalog — no user_id.
    """
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(db_path) as db:
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
    from src.api.routes import channels as channels_route
    from src.core import settings

    patched = Path(db_path)
    monkeypatch.setattr(settings, "DB_PATH", patched, raising=True)
    monkeypatch.setattr(dependencies, "DB_PATH", patched, raising=True)
    monkeypatch.setattr(auth_deps, "DB_PATH", patched, raising=True)
    monkeypatch.setattr(auth_route, "DB_PATH", patched, raising=True)
    monkeypatch.setattr(channels_route, "DB_PATH", patched, raising=True)

    # Reset the JobDatabase singleton so it lazy-binds to the patched path
    monkeypatch.setattr(dependencies, "_db", None, raising=False)

    crypto.set_test_key(Fernet.generate_key().decode("ascii"))
    monkeypatch.setenv("SESSION_SECRET", "test-secret-" + "y" * 40)

    # Seed two shared-catalog jobs we can reference by id.
    job_ids = asyncio.run(_seed_job_rows(db_path))

    from src.api.main import app
    app.router.lifespan_context = _noop_lifespan  # type: ignore[assignment]
    client = TestClient(app)
    client.__job_ids__ = job_ids  # type: ignore[attr-defined]
    return client


def _register(client, email, password="s3cretpassword"):
    r = client.post(
        "/api/auth/register", json={"email": email, "password": password}
    )
    assert r.status_code == 201, r.text


# ---------------------------------------------------------------------------
# Unauthenticated requests → 401
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("method,path", [
    ("GET",  "/api/jobs"),
    ("POST", "/api/jobs/1/action"),
    ("DELETE", "/api/jobs/1/action"),
    ("GET",  "/api/actions"),
    ("GET",  "/api/actions/counts"),
    ("GET",  "/api/pipeline"),
    ("GET",  "/api/pipeline/counts"),
    ("GET",  "/api/pipeline/reminders"),
    ("POST", "/api/pipeline/1"),
    ("POST", "/api/pipeline/1/advance"),
    # Batch 3.5.1 — close the profile + search gaps the 2026-04-19
    # CurrentStatus re-audit §7 identified.
    ("GET",  "/api/profile"),
    ("POST", "/api/profile"),
    ("POST", "/api/profile/linkedin"),
    ("POST", "/api/profile/github"),
    ("POST", "/api/search"),
    ("GET",  "/api/search/abc123/status"),
])
def test_per_user_endpoint_requires_auth(api, method, path):
    if method == "POST" and "action" in path:
        r = api.request(method, path, json={"action": "liked"})
    elif method == "POST" and "advance" in path:
        r = api.request(method, path, json={"stage": "interview"})
    else:
        r = api.request(method, path)
    assert r.status_code == 401, (
        f"{method} {path} returned {r.status_code} instead of 401: {r.text}"
    )


# ---------------------------------------------------------------------------
# Cross-user isolation — actions
# ---------------------------------------------------------------------------


def test_action_isolation_alice_cannot_see_bobs_actions(api):
    """Alice creates an action; Bob's /api/actions must not show it."""
    job_a, job_b = api.__job_ids__  # type: ignore[attr-defined]

    _register(api, "alice@example.com")
    r = api.post(f"/api/jobs/{job_a}/action", json={"action": "liked"})
    assert r.status_code == 200, r.text

    # Alice sees it
    r_alice = api.get("/api/actions")
    assert r_alice.status_code == 200
    alice_actions = r_alice.json().get("actions", [])
    assert any(a["job_id"] == job_a for a in alice_actions)

    # Switch to Bob
    api.post("/api/auth/logout")
    api.cookies.clear()
    _register(api, "bob@example.com")

    # Bob must see empty list (no access to alice's action rows)
    r_bob = api.get("/api/actions")
    assert r_bob.status_code == 200
    bob_actions = r_bob.json().get("actions", [])
    assert bob_actions == [], f"Bob leaked alice's actions: {bob_actions}"


def test_action_counts_scoped_by_user(api):
    job_a, _ = api.__job_ids__  # type: ignore[attr-defined]

    _register(api, "alice@example.com")
    api.post(f"/api/jobs/{job_a}/action", json={"action": "liked"})

    r_alice = api.get("/api/actions/counts")
    assert r_alice.json().get("liked", 0) == 1

    api.post("/api/auth/logout")
    api.cookies.clear()
    _register(api, "bob@example.com")

    r_bob = api.get("/api/actions/counts")
    assert r_bob.json().get("liked", 0) == 0, (
        f"Bob saw alice's liked count: {r_bob.json()}"
    )


def test_action_delete_is_scoped_by_user(api):
    """Bob deleting an action on a job he never touched must not affect alice's row."""
    job_a, _ = api.__job_ids__  # type: ignore[attr-defined]

    _register(api, "alice@example.com")
    api.post(f"/api/jobs/{job_a}/action", json={"action": "applied"})

    api.post("/api/auth/logout")
    api.cookies.clear()
    _register(api, "bob@example.com")

    # Bob tries to delete an action he doesn't own — action should be a no-op
    # from the repo layer (DELETE ... WHERE user_id = bob matches 0 rows)
    r_bob_delete = api.delete(f"/api/jobs/{job_a}/action")
    # Depending on implementation, either 200 (idempotent) or 404 is acceptable —
    # what matters is that Alice's row survives.
    assert r_bob_delete.status_code in (200, 204, 404), r_bob_delete.text

    api.post("/api/auth/logout")
    api.cookies.clear()

    # Alice logs back in and must still see her action
    r = api.post("/api/auth/login", json={
        "email": "alice@example.com", "password": "s3cretpassword"
    })
    assert r.status_code == 200, r.text
    r_alice = api.get("/api/actions")
    alice_actions = r_alice.json().get("actions", [])
    assert any(a["job_id"] == job_a and a["action"] == "applied"
               for a in alice_actions), (
        f"Bob's delete clobbered alice's row: {alice_actions}"
    )


# ---------------------------------------------------------------------------
# Cross-user isolation — pipeline
# ---------------------------------------------------------------------------


def test_pipeline_isolation_alice_cannot_see_bobs_applications(api):
    job_a, _ = api.__job_ids__  # type: ignore[attr-defined]

    _register(api, "alice@example.com")
    r = api.post(f"/api/pipeline/{job_a}")
    assert r.status_code == 200, r.text

    r_alice = api.get("/api/pipeline")
    assert r_alice.status_code == 200
    alice_apps = r_alice.json().get("applications", [])
    assert any(a["job_id"] == job_a for a in alice_apps)

    api.post("/api/auth/logout")
    api.cookies.clear()
    _register(api, "bob@example.com")

    r_bob = api.get("/api/pipeline")
    bob_apps = r_bob.json().get("applications", [])
    assert bob_apps == [], f"Bob leaked alice's applications: {bob_apps}"


def test_pipeline_counts_scoped_by_user(api):
    job_a, _ = api.__job_ids__  # type: ignore[attr-defined]

    _register(api, "alice@example.com")
    api.post(f"/api/pipeline/{job_a}")

    r_alice = api.get("/api/pipeline/counts")
    assert r_alice.json().get("applied", 0) == 1

    api.post("/api/auth/logout")
    api.cookies.clear()
    _register(api, "bob@example.com")

    r_bob = api.get("/api/pipeline/counts")
    assert r_bob.json().get("applied", 0) == 0


def test_pipeline_advance_cannot_target_other_users_row(api):
    """Bob calling advance on a job_id he never added must 404, not update alice's row."""
    job_a, _ = api.__job_ids__  # type: ignore[attr-defined]

    _register(api, "alice@example.com")
    api.post(f"/api/pipeline/{job_a}")

    api.post("/api/auth/logout")
    api.cookies.clear()
    _register(api, "bob@example.com")

    r_bob = api.post(
        f"/api/pipeline/{job_a}/advance",
        json={"stage": "interview"},
    )
    # Acceptable results: 404 (Bob has no application for this job) or
    # 200 with a newly-created-for-bob row. NOT acceptable: advancing
    # alice's row.
    assert r_bob.status_code in (200, 404), r_bob.text

    # Log alice back in and check her row is untouched
    api.post("/api/auth/logout")
    api.cookies.clear()
    api.post("/api/auth/login", json={
        "email": "alice@example.com", "password": "s3cretpassword"
    })
    r_alice = api.get("/api/pipeline")
    alice_apps = r_alice.json().get("applications", [])
    assert any(a["job_id"] == job_a and a["stage"] == "applied"
               for a in alice_apps), (
        f"Bob's advance modified alice's row: {alice_apps}"
    )


# ---------------------------------------------------------------------------
# Positive control — same-user round-trip
# ---------------------------------------------------------------------------


def test_action_roundtrip_for_authenticated_user(api):
    job_a, _ = api.__job_ids__  # type: ignore[attr-defined]
    _register(api, "alice@example.com")

    r = api.post(f"/api/jobs/{job_a}/action", json={"action": "liked"})
    assert r.status_code == 200

    r2 = api.get("/api/actions")
    actions = r2.json().get("actions", [])
    assert len(actions) == 1
    assert actions[0]["job_id"] == job_a
    assert actions[0]["action"] == "liked"

    r3 = api.delete(f"/api/jobs/{job_a}/action")
    assert r3.status_code == 200

    r4 = api.get("/api/actions")
    assert r4.json().get("actions", []) == []


# ---------------------------------------------------------------------------
# Batch 3.5.1 — search run_id user-scoping (existence-hiding via 404)
# ---------------------------------------------------------------------------


def test_search_run_id_is_scoped_by_user(api, monkeypatch):
    """Alice creates a run; Bob hitting that run_id's status must 404.

    The POST handler stores `user_id` on the _runs[run_id] record; the
    GET handler returns 404 (not 403) when run["user_id"] != user.id,
    so an attacker cannot distinguish "does not exist" from "exists but
    not mine" — no oracle for run_id enumeration.
    """
    # Stub run_search so the background task completes instantly and
    # predictably — we are testing the gate, not the pipeline.
    async def _fake_run_search(**kwargs):
        return {
            "total_found": 0,
            "new_jobs": 0,
            "sources_queried": 0,
            "per_source": {},
        }

    import src.api.routes.search as search_route
    monkeypatch.setattr(search_route, "run_search", _fake_run_search)
    # Reset module-level _runs dict so prior tests don't leak run_ids.
    monkeypatch.setattr(search_route, "_runs", {}, raising=True)

    _register(api, "alice@example.com")
    r = api.post("/api/search")
    assert r.status_code == 200, r.text
    run_id = r.json()["run_id"]

    # Alice can read her own run (positive control)
    r_alice = api.get(f"/api/search/{run_id}/status")
    assert r_alice.status_code == 200
    assert r_alice.json()["run_id"] == run_id

    # Switch to Bob
    api.post("/api/auth/logout")
    api.cookies.clear()
    _register(api, "bob@example.com")

    r_bob = api.get(f"/api/search/{run_id}/status")
    assert r_bob.status_code == 404, (
        f"cross-user read of alice's run_id should 404 "
        f"(existence hiding), got {r_bob.status_code}: {r_bob.text}"
    )


def test_search_status_for_unknown_run_id_returns_404(api, monkeypatch):
    """Unknown run_id → 404 for an authenticated user."""
    import src.api.routes.search as search_route
    monkeypatch.setattr(search_route, "_runs", {}, raising=True)

    _register(api, "alice@example.com")
    r = api.get("/api/search/nonexistent123/status")
    assert r.status_code == 404


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
