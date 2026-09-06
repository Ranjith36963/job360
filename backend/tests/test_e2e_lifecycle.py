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
  - the browser UI — that's the Playwright suite (frontend/tests/e2e).
"""
from __future__ import annotations

import asyncio
import io
import logging
import sys
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

from migrations import runner
from src.repositories import pgsync

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


_AD = {
    "title": "Senior ML Engineer",
    "company": "Acme",
    "location": "London",
    "apply_url": "https://acme.test/job",
    "description": "Build and ship ML services. Python, PyTorch, RAG. " * 4,
}


def _bring(client, **overrides) -> dict:
    """Bring one ad through the REAL front door.

    Slice 5 (#483) replaced the seeded catalog+feed rows this used to fake: the
    only way a job enters the system now is `POST /jobs/bring`, and what it
    creates is an Application, not a feed row.
    """
    r = client.post("/api/jobs/bring", json={**_AD, **overrides})
    assert r.status_code == 200, r.text
    return r.json()


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

    with _LogCapture(caplog) as logs:
        email = "e2e@example.com"
        _register(client, email)
        assert client.get("/api/auth/me").status_code == 200
        # the tailor is gated until verified (it spends an LLM call)
        assert client.post("/api/tailor/1/generate").status_code == 403
        _mark_verified(app_db, email)

        # CV upload → deterministic skills
        r = client.post(
            "/api/profile",
            files={"cv": ("cv.pdf", io.BytesIO(b"%PDF-1.4\n%%EOF"), "application/pdf")},
        )
        assert r.status_code == 200, r.text

        # bring an ad → job row + Application, no score anywhere
        brought = _bring(client)
        job_id = brought["job"]["id"]
        application_id = brought["application_id"]
        assert brought["status"] == "considering"
        assert "scored" not in brought and "match_score" not in brought["job"]

        # the spine reads back
        detail = client.get(f"/api/applications/{application_id}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["job_id"] == job_id
        assert client.get("/api/applications").json()["applications"], "the list must show it"
        assert client.get(f"/api/applications/job/{job_id}").status_code == 200

        # the agent's OWN fit verdict — Job360 stores it, never computes it
        assert client.put(
            f"/api/applications/{application_id}/fit",
            json={"score": 72, "verdict": "worth applying"},
        ).status_code == 200

        # a receipt
        assert client.post(
            f"/api/applications/{application_id}/receipt", json={"channel": "company site"}
        ).status_code == 201

        # frontend → server log bridge
        assert client.post(
            "/api/client-log", json={"level": "error", "message": "e2e ui error", "context": "window.onerror"}
        ).status_code == 204

        # a 404 + 422 → 4xx reason logging
        assert client.get("/api/applications/99999999").status_code == 404
        assert client.post("/api/jobs/bring", json={**_AD, "title": "   "}).status_code == 422

        assert client.post("/api/auth/logout").status_code == 204
        assert client.get("/api/auth/me").status_code == 401

        seen = logs.events()

    # EVERY stream must have filled — a dark one is a regression.
    required = {
        "http_request",          # A access log
        "register",              # auth
        "session_created",       # K
        "session_revoked",       # K
        "job_brought",           # C — the product's front door
        "client_event",          # D
        "http_error",            # 3b (the 404)
        "validation_error",      # 3b (the 422)
    }
    missing = required - seen
    assert not missing, f"log streams that did NOT fill (dark paths): {sorted(missing)}"


# ─── edge cases ──────────────────────────────────────────────────────────────


def test_edge_unverified_user_blocked_from_the_tailor(client):
    """The one remaining `require_verified_user` gate: tailoring spends a paid
    LLM call. Bringing a job does not, so it is `require_user` only."""
    _register(client, "unverified@example.com")
    assert client.post("/api/tailor/1/generate").status_code == 403  # email_not_verified


def test_edge_login_lockout_after_five_failures(client):
    _register(client, "lockme@example.com")
    for _ in range(5):
        client.post("/api/auth/login", json={"email": "lockme@example.com", "password": "wrong"})
    r = client.post("/api/auth/login", json={"email": "lockme@example.com", "password": _PW})
    assert r.status_code == 429
    assert "Retry-After" in r.headers


def test_edge_idor_user_cannot_see_another_users_applications(client, app_db):
    """Slice 5 (#483) made the ad itself per-user: `jobs` is still the shared
    table (rule #10), but the only read of it is scoped through the caller's
    own application, so Bob cannot reach Alice's paste by guessing an id."""
    from src.api.main import app

    _register(client, "alice@example.com")
    _mark_verified(app_db, "alice@example.com")
    brought = _bring(client)
    job_id = brought["job"]["id"]
    assert client.get("/api/applications").json()["applications"]

    with TestClient(app) as bob:
        _register(bob, "bob@example.com")
        _mark_verified(app_db, "bob@example.com")
        assert bob.get("/api/applications").json()["applications"] == []
        # Bob never brought this job — the ad text must be unreachable.
        assert bob.get(f"/api/applications/job/{job_id}").status_code == 404
        assert bob.get(f"/api/applications/{brought['application_id']}").status_code == 404


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
