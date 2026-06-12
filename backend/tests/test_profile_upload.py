"""V-04: CV upload size cap + MIME allowlist — server-side enforcement tests.

The /api/profile route must enforce a 10 MB cap and accept only PDF/DOCX,
regardless of what the client sends. These tests prove the server-side
rejection independently of the frontend validation.
"""
from __future__ import annotations

import asyncio
import io
from contextlib import asynccontextmanager

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from migrations import runner
from src.services.channels import crypto


@asynccontextmanager
async def _noop_lifespan(app):
    yield


@pytest.fixture
def api(monkeypatch, tmp_path):
    """Minimal test client with a real DB + auth wired up."""
    db_path = str(tmp_path / "test.db")

    async def _bootstrap():
        from src.repositories.database import JobDatabase
        db = JobDatabase(db_path)
        await db.init_db()
        await db.close()
        await runner.up(db_path)

    asyncio.run(_bootstrap())

    from pathlib import Path
    import sys as _sys

    from src.api import auth_deps, dependencies
    from src.api.routes import auth as auth_route
    from src.api.routes import channels as channels_route
    from src.core import settings
    from src.api.main import app

    patched = Path(db_path)
    monkeypatch.setattr(settings, "DB_PATH", patched, raising=True)
    monkeypatch.setattr(dependencies, "DB_PATH", patched, raising=True)
    monkeypatch.setattr(auth_deps, "DB_PATH", patched, raising=True)
    monkeypatch.setattr(auth_route, "DB_PATH", patched, raising=True)
    monkeypatch.setattr(channels_route, "DB_PATH", patched, raising=True)
    monkeypatch.setattr(dependencies, "_db", None, raising=False)

    for _mod in list(_sys.modules.values()):
        _name = getattr(_mod, "__name__", "")
        if _name.startswith(("src.", "migrations")) and getattr(_mod, "DB_PATH", None) is not None:
            monkeypatch.setattr(_mod, "DB_PATH", patched, raising=False)

    crypto.set_test_key(Fernet.generate_key().decode("ascii"))
    monkeypatch.setenv("SESSION_SECRET", "test-secret-" + "y" * 40)

    app.router.lifespan_context = _noop_lifespan  # type: ignore[assignment]
    client = TestClient(app)
    return client


def _register_and_login(client: TestClient, email: str = "user@example.com") -> None:
    r = client.post("/api/auth/register", json={"email": email, "password": "s3cretpassword"})
    assert r.status_code == 201, r.text


# ---------------------------------------------------------------------------
# Size cap — 413
# ---------------------------------------------------------------------------

def test_cv_upload_rejects_oversized_file(api, monkeypatch):
    """POST /api/profile with a file > 10 MB must return 413."""
    _register_and_login(api)
    oversized = b"X" * (10 * 1024 * 1024 + 1)
    r = api.post(
        "/api/profile",
        files={"cv": ("cv.pdf", io.BytesIO(oversized), "application/pdf")},
    )
    assert r.status_code == 413, f"Expected 413 for oversized file, got {r.status_code}: {r.text}"
    assert "10 MB" in r.json().get("detail", "")


def test_cv_upload_accepts_file_at_size_boundary(api, monkeypatch):
    """POST /api/profile with a file exactly at 10 MB must not 413 on size check.
    (We can't fully parse it without an LLM, so we mock parse_cv_async.)"""
    import src.api.routes.profile as profile_route
    from src.services.profile.models import CVData

    async def _fake_parse(path: str) -> CVData:
        return CVData(raw_text="test", skills=["python"], job_titles=["Engineer"])

    monkeypatch.setattr(profile_route, "parse_cv_async", _fake_parse)

    _register_and_login(api)
    exactly_10mb = b"%PDF-1.4 " + b"X" * (10 * 1024 * 1024 - 9)
    r = api.post(
        "/api/profile",
        files={"cv": ("cv.pdf", io.BytesIO(exactly_10mb), "application/pdf")},
    )
    assert r.status_code != 413, f"Rejected a file exactly at the limit: {r.text}"


# ---------------------------------------------------------------------------
# MIME / extension allowlist — 415
# ---------------------------------------------------------------------------

def test_cv_upload_rejects_txt_file(api):
    """POST /api/profile with a .txt file must return 415."""
    _register_and_login(api)
    r = api.post(
        "/api/profile",
        files={"cv": ("notes.txt", io.BytesIO(b"plain text"), "text/plain")},
    )
    assert r.status_code == 415, f"Expected 415 for .txt, got {r.status_code}: {r.text}"
    assert "PDF" in r.json().get("detail", "") or "DOCX" in r.json().get("detail", "")


def test_cv_upload_rejects_no_extension(api):
    """POST /api/profile with a filename lacking a recognised extension must return 415."""
    _register_and_login(api)
    r = api.post(
        "/api/profile",
        files={"cv": ("cv", io.BytesIO(b"data"), "application/octet-stream")},
    )
    assert r.status_code == 415, f"Expected 415 for no-extension, got {r.status_code}: {r.text}"


def test_cv_upload_accepts_pdf(api, monkeypatch):
    """POST /api/profile with a valid PDF (small, mocked parser) must return 200."""
    import src.api.routes.profile as profile_route
    from src.services.profile.models import CVData

    async def _fake_parse(path: str) -> CVData:
        return CVData(raw_text="test", skills=["python"], job_titles=["Engineer"])

    monkeypatch.setattr(profile_route, "parse_cv_async", _fake_parse)

    _register_and_login(api)
    minimal_pdf = b"%PDF-1.4\n%%EOF"
    r = api.post(
        "/api/profile",
        files={"cv": ("cv.pdf", io.BytesIO(minimal_pdf), "application/pdf")},
    )
    assert r.status_code == 200, f"Expected 200 for valid PDF, got {r.status_code}: {r.text}"


def test_cv_upload_accepts_docx(api, monkeypatch):
    """POST /api/profile with a valid DOCX must return 200."""
    import src.api.routes.profile as profile_route
    from src.services.profile.models import CVData

    async def _fake_parse(path: str) -> CVData:
        return CVData(raw_text="test", skills=["python"], job_titles=["Engineer"])

    monkeypatch.setattr(profile_route, "parse_cv_async", _fake_parse)

    _register_and_login(api)
    # Minimal DOCX magic bytes (PK zip header)
    minimal_docx = b"PK\x03\x04" + b"\x00" * 20
    r = api.post(
        "/api/profile",
        files={"cv": ("resume.docx", io.BytesIO(minimal_docx),
                      "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    assert r.status_code == 200, f"Expected 200 for valid DOCX, got {r.status_code}: {r.text}"
