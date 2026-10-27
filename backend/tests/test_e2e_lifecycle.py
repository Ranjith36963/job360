"""Automated end-to-end lifecycle test — "Loop 2" as a committed guardrail.

This is the automated, repeatable form of the manual /verify run (see .claude/skills/run-job360). It drives
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
import json
import logging
import sys
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

from migrations import runner
from src.repositories import pgsync
from tests._signin import sign_in

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
    _, user_id = sign_in(client, email)
    return user_id


def _unverify(db_path, email):
    # A magic-link sign-in verifies the address; tests of the unverified gate undo that.
    con = pgsync.connect(db_path)
    con.execute("UPDATE users SET email_verified_at = NULL WHERE email = ?", (email,))
    con.commit()
    con.close()


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


_CV_TEXT = "Jane Doe\nML Engineer\nShipped RAG services in Python and PyTorch."
_MCP_NOTE = "e2e: the agent read the tailored CV over MCP"


def _artifact_row(db_path, application_id, artifact_id) -> dict:
    con = pgsync.connect(db_path)
    try:
        cur = con.execute(
            "SELECT kind, text, made_by, profile_version FROM application_artifacts "
            "WHERE id=? AND application_id=?",
            (artifact_id, application_id),
        )
        row = cur.fetchone()
    finally:
        con.close()
    assert row is not None, "the tailor stored no artifact row"
    return dict(zip(("kind", "text", "made_by", "profile_version"), row))


def _event_rows(db_path, application_id) -> list[dict]:
    con = pgsync.connect(db_path)
    try:
        cur = con.execute(
            "SELECT id, event_type, detail FROM application_events WHERE application_id=? ORDER BY id",
            (application_id,),
        )
        rows = cur.fetchall()
    finally:
        con.close()
    return [dict(zip(("id", "event_type", "detail"), r)) for r in rows]


async def _mcp_get_and_record(token: str, application_id: int) -> tuple[dict, dict]:
    """get_application then record_event over the real /api/mcp endpoint, the
    way the MCP tests drive it (bearer token, in-process ASGI transport)."""
    import httpx2
    from mcp.client import Client
    from mcp.client.streamable_http import streamable_http_client

    from src.api.main import app
    from src.api.mcp_server import mcp_runtime

    def _ok(result) -> dict:
        assert not result.is_error, result.content[0].text
        return json.loads(result.content[0].text)

    http = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )
    async with mcp_runtime():
        async with Client(streamable_http_client("http://test/api/mcp", http_client=http)) as mcp:
            got = _ok(await mcp.call_tool(
                "get_application", {"application_id": application_id, "with_artifact_text": True}
            ))
            recorded = _ok(await mcp.call_tool(
                "record_event", {"application_id": application_id, "event_type": "note", "detail": _MCP_NOTE}
            ))
    return got, recorded


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
        # This test asserts the `register` log stream fills, so it keeps the password sign-up.
        assert client.post("/api/auth/register", json={"email": email, "password": _PW}).status_code == 201
        assert client.post("/api/auth/login", json={"email": email, "password": _PW}).status_code == 200
        assert client.get("/api/auth/me").status_code == 200
        # the tailor is gated until verified (it hands out a finished document)
        assert client.get("/api/tailor/1").status_code == 403
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
            json={"score": 72, "verdict": "worth applying", "score_source": "full_ad"},
        ).status_code == 200

        # the tailor, as the now-VERIFIED user: a CV version saved on THIS
        # application (M3 — a new artifact row stamped made_by/profile_version).
        # Decision 28: the tailor calls no LLM, so there is nothing to mock.
        assert client.get(f"/api/tailor/{job_id}").json()["documents"] == []
        saved = client.patch(f"/api/tailor/{job_id}/cv", json={"text": _CV_TEXT})
        assert saved.status_code == 200, saved.text
        assert saved.json()["made_by"] == "human" and saved.json()["version_no"] == 1
        bundle = client.get(f"/api/tailor/{job_id}")
        assert bundle.status_code == 200, bundle.text
        assert bundle.json()["application_id"] == application_id
        assert [(d["doc_kind"], d["text"]) for d in bundle.json()["documents"]] == [("cv", _CV_TEXT)]
        art = _artifact_row(app_db, application_id, saved.json()["artifact_id"])
        assert art["kind"] == "cv" and art["text"] == _CV_TEXT, art
        assert art["made_by"] == "human" and art["profile_version"] is not None, art

        # MCP — the agent's door, over the same application (M5: tools call the
        # route functions). Read it back, then append one event (M3).
        token = client.post("/api/tokens", json={"name": "e2e-agent"})
        assert token.status_code == 201, token.text
        got, recorded = asyncio.run(_mcp_get_and_record(token.json()["token"], application_id))
        assert got["id"] == application_id and got["job_id"] == job_id
        cv_versions = [a for a in got["artifacts"] if a["kind"] == "cv"]
        assert [(a["id"], a["text"], a["made_by"]) for a in cv_versions] == [
            (saved.json()["artifact_id"], _CV_TEXT, "human")
        ]
        rows = _event_rows(app_db, application_id)
        assert ("note", _MCP_NOTE) in [(r["event_type"], r["detail"]) for r in rows], rows
        assert recorded["event_id"] in {r["id"] for r in rows if r["event_type"] == "note"}, recorded

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
        "mcp_tool_call",         # the agent's door (MCP audit)
        "client_event",          # D
        "http_error",            # 3b (the 404)
        "validation_error",      # 3b (the 422)
    }
    missing = required - seen
    assert not missing, f"log streams that did NOT fill (dark paths): {sorted(missing)}"


# ─── edge cases ──────────────────────────────────────────────────────────────


def test_edge_unverified_user_blocked_from_the_tailor(client, app_db):
    """The one remaining `require_verified_user` gate: the tailor hands the user
    a finished PDF / DOCX. Bringing a job does not, so it is `require_user` only."""
    _register(client, "unverified@example.com")
    _unverify(app_db, "unverified@example.com")
    assert client.get("/api/tailor/1").status_code == 403  # email_not_verified


def test_edge_login_lockout_after_five_failures(client):
    # Password behaviour is this test's subject, so it keeps a real password account.
    r = client.post("/api/auth/register", json={"email": "lockme@example.com", "password": _PW})
    assert r.status_code == 201, r.text
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
