"""Automated end-to-end lifecycle test — "Loop 2" as a committed guardrail.

This is the automated, repeatable form of the manual /verify-job360 run. It drives
the FULL Job360 journey through the real API with deterministic mocks (LLM + search
stubbed → no network, no quota dependence) and asserts BOTH:
  1. behaviour — every step returns the right status / DB row, AND
  2. observability — every logging stream actually filled. A DARK stream is a
     test FAILURE (that's how a broken/forgotten code path gets caught).

Plus edge cases (unverified-search gate, login lockout, multi-user IDOR isolation,
malformed CV) and 4xx-reason logging. Offline + deterministic (root rule #4): the
LLM and the source-fetching search are mocked, so this runs in the gate every time.

What it does NOT cover (needs infra, documented for honesty):
  - real notification DELIVERY. The prerequisites differ per channel, and the
    old blanket "needs Redis + SMTP creds" was wrong for both:
      * webhook — needs Redis and a RUNNING ARQ worker, because the send is
        queued, not inline.
      * email — needs EITHER ``RESEND_API_KEY`` (the production path; Railway
        blocks outbound SMTP ports 25/465/587) OR real SMTP credentials for a
        local/self-hosted relay. See ``services/channels/email_url.py``.
    This text is documentation only — it does not gate pytest. The dispatch
    *logic* is covered by test_dispatch_logging / test_worker_logging.
  - the browser UI — that's the Playwright suite (frontend/tests/e2e).
"""
from __future__ import annotations

import asyncio
import io
import logging
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from migrations import runner
from src.repositories import pgsync
from src.services.channels import crypto

_PW = "Correct-Horse-9"


@asynccontextmanager
async def _noop_lifespan(app):
    yield


@pytest.fixture
def app_db(monkeypatch, tmp_path):
    """Full-schema tmp DB + DB_PATH redirected on every importer (IDOR pattern)."""
    db_path = str(tmp_path / "test.db")

    async def _bootstrap():
        from src.repositories.database import JobDatabase

        db = JobDatabase(db_path)
        await db.init_db()
        await db.close()
        await runner.up(db_path)

    asyncio.run(_bootstrap())

    from pathlib import Path

    patched = Path(db_path)
    from src.api import dependencies

    monkeypatch.setattr(dependencies, "_db", None, raising=False)
    for mod in list(sys.modules.values()):
        name = getattr(mod, "__name__", "")
        if name.startswith(("src.", "migrations")) and getattr(mod, "DB_PATH", None) is not None:
            monkeypatch.setattr(mod, "DB_PATH", patched, raising=False)

    crypto.set_test_key(Fernet.generate_key().decode("ascii"))
    monkeypatch.setenv("SESSION_SECRET", "test-secret-" + "z" * 40)
    yield db_path


@pytest.fixture
def client(app_db):
    from src.api.main import app

    app.router.lifespan_context = _noop_lifespan  # type: ignore[assignment]
    with TestClient(app) as c:
        yield c


# ─── helpers ─────────────────────────────────────────────────────────────────


def _register(client, email) -> str:
    r = client.post("/api/auth/register", json={"email": email, "password": _PW})
    assert r.status_code == 201, r.text
    # M2 — register no longer auto-logs-in or returns the id; sign in for both.
    lr = client.post("/api/auth/login", json={"email": email, "password": _PW})
    assert lr.status_code == 200, lr.text
    return lr.json()["id"]


def _mark_verified(db_path, email):
    con = pgsync.connect(db_path)
    con.execute("UPDATE users SET email_verified_at=datetime('now') WHERE email=?", (email,))
    con.commit()
    con.close()


def _seed_job_and_feed(db_path, user_id) -> int:
    """Seed one catalog job + one user_feed row so /jobs returns it for the user."""
    now = datetime.now(timezone.utc).isoformat()
    con = pgsync.connect(db_path)
    cur = con.execute(
        "INSERT INTO jobs (title, company, location, apply_url, source, date_found, "
        "normalized_company, normalized_title, first_seen, staleness_state) "
        "VALUES (?, ?, '', ?, 'test', ?, ?, ?, ?, 'active')",
        ("Senior ML Engineer", "Acme", "https://acme.test/job", now, "acme", "senior ml engineer", now),
    )
    job_id = cur.lastrowid
    con.execute(
        "INSERT INTO user_feed (user_id, job_id, score, bucket, status, created_at, updated_at) "
        "VALUES (?, ?, ?, 'recent', 'active', ?, ?)",
        (user_id, job_id, 88, now, now),
    )
    con.commit()
    con.close()
    return job_id


class _LogCapture:
    """Capture every job360.* event (incl. the propagate=False audit logger)."""

    def __init__(self, caplog):
        self.caplog = caplog
        self._loggers = [logging.getLogger("job360"), logging.getLogger("job360.audit")]

    def __enter__(self):
        self.caplog.handler.setLevel(logging.INFO)
        for lg in self._loggers:
            lg.addHandler(self.caplog.handler)
            lg.setLevel(logging.INFO)
        return self

    def __exit__(self, *a):
        for lg in self._loggers:
            lg.removeHandler(self.caplog.handler)

    def events(self):
        return {getattr(r, "event", "") for r in self.caplog.records}


# ─── the full lifecycle ──────────────────────────────────────────────────────


def test_full_lifecycle_fills_every_log_stream(client, app_db, caplog, monkeypatch):
    # deterministic CV extraction (no LLM / no quota)
    import src.api.routes.profile as profile_route

    monkeypatch.setattr(profile_route, "extract_text", lambda path: "cv text", raising=False)

    async def _fake_extract(profile):
        profile.cv_data.skills = ["Python", "PyTorch", "RAG"]
        profile.cv_data.job_titles = ["ML Engineer"]
        return profile

    monkeypatch.setattr(profile_route, "run_two_pass_extraction", _fake_extract, raising=False)

    # deterministic search (no source fetching)
    async def _fake_run_search(**kwargs):
        return {"new_jobs": 1, "sources_queried": 40}

    monkeypatch.setattr("src.api.routes.search.run_search", _fake_run_search)

    with _LogCapture(caplog) as logs:
        email = "e2e@example.com"
        uid = _register(client, email)
        assert client.get("/api/auth/me").status_code == 200
        # search is gated until verified
        assert client.post("/api/search").status_code == 403
        _mark_verified(app_db, email)

        # CV upload → deterministic skills
        r = client.post(
            "/api/profile",
            files={"cv": ("cv.pdf", io.BytesIO(b"%PDF-1.4\n%%EOF"), "application/pdf")},
        )
        assert r.status_code == 200, r.text

        # search (verified) → 200 + audit
        assert client.post("/api/search").status_code == 200

        # seed a job+feed row so downstream steps have data deterministically
        job_id = _seed_job_and_feed(app_db, uid)
        feed = client.get("/api/jobs?limit=5")
        assert feed.status_code == 200
        assert feed.json().get("total", 0) >= 1

        assert client.get(f"/api/jobs/{job_id}").status_code == 200
        assert client.post(f"/api/jobs/{job_id}/action", json={"action": "liked"}).status_code == 200
        assert client.post(f"/api/pipeline/{job_id}", json={}).status_code in (200, 201)

        # channel create + test-send
        ch = client.post(
            "/api/settings/channels",
            json={"channel_type": "webhook", "display_name": "t", "credential": "https://example.com/h"},
        )
        assert ch.status_code == 201, ch.text
        cid = ch.json()["id"]
        assert client.post(f"/api/settings/channels/{cid}/test").status_code == 200

        # notification rule
        assert client.put(
            "/api/settings/notification-rule",
            json={"score_threshold": 60, "notify_mode": "instant", "enabled": True},
        ).status_code == 200

        # frontend → server log bridge
        assert client.post(
            "/api/client-log", json={"level": "error", "message": "e2e ui error", "context": "window.onerror"}
        ).status_code == 204

        # a 404 + 422 → 4xx reason logging
        assert client.get("/api/jobs/99999999").status_code == 404
        assert client.post(f"/api/jobs/{job_id}/action", json={"action": 123}).status_code == 422

        assert client.post("/api/auth/logout").status_code == 204
        assert client.get("/api/auth/me").status_code == 401

        seen = logs.events()

    # EVERY stream must have filled — a dark one is a regression.
    required = {
        "http_request",          # A access log
        "register",              # auth
        "session_created",       # K
        "session_revoked",       # K
        "job_action",            # C
        "pipeline_create",       # C
        "search_started",        # F
        "channel_created",       # L
        "channel_test_send",     # L
        "notification_rule_saved",  # L
        "client_event",          # D
        "http_error",            # 3b (the 404)
        "validation_error",      # 3b (the 422)
    }
    missing = required - seen
    assert not missing, f"log streams that did NOT fill (dark paths): {sorted(missing)}"


# ─── edge cases ──────────────────────────────────────────────────────────────


def test_edge_unverified_user_blocked_from_search(client):
    _register(client, "unverified@example.com")
    assert client.post("/api/search").status_code == 403  # email_not_verified


def test_edge_login_lockout_after_five_failures(client):
    _register(client, "lockme@example.com")
    for _ in range(5):
        client.post("/api/auth/login", json={"email": "lockme@example.com", "password": "wrong"})
    r = client.post("/api/auth/login", json={"email": "lockme@example.com", "password": _PW})
    assert r.status_code == 429
    assert "Retry-After" in r.headers


def test_edge_idor_user_cannot_see_another_users_feed(client, app_db):
    from src.api.main import app

    uid_a = _register(client, "alice@example.com")
    _mark_verified(app_db, "alice@example.com")
    _seed_job_and_feed(app_db, uid_a)
    assert client.get("/api/jobs").json().get("total", 0) >= 1  # Alice sees her job

    with TestClient(app) as bob:
        _register(bob, "bob@example.com")
        _mark_verified(app_db, "bob@example.com")
        # Bob has no feed rows → must NOT see Alice's job.
        assert bob.get("/api/jobs").json().get("total", 0) == 0


def test_edge_malformed_cv_returns_controlled_error_not_crash(client, app_db, caplog):
    """A corrupt PDF must surface a CONTROLLED error (real extract_text catches the
    parse failure → returns "" → route raises 503), NOT an unhandled 500 crash."""
    _register(client, "badcv@example.com")
    _mark_verified(app_db, "badcv@example.com")
    with _LogCapture(caplog) as logs:
        r = client.post(
            "/api/profile",
            files={"cv": ("x.pdf", io.BytesIO(b"this is not a real pdf"), "application/pdf")},
        )
    # 415 (bad ext) / 503 (unreadable) are deliberate HTTPExceptions; 500 = crash.
    assert r.status_code in (415, 503), f"expected a controlled error, got {r.status_code}: {r.text}"
    assert "unhandled_exception" not in logs.events(), "malformed CV crashed instead of failing gracefully"
