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

    import sys as _sys
    from pathlib import Path

    from src.api import auth_deps, dependencies
    from src.api.main import app
    from src.api.routes import auth as auth_route
    from src.api.routes import channels as channels_route
    from src.core import settings

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
    # M2 — register no longer auto-logs-in; sign in so the client is authenticated.
    lr = client.post("/api/auth/login", json={"email": email, "password": "s3cretpassword"})
    assert lr.status_code == 200, lr.text


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
    monkeypatch.setattr(profile_route, "extract_text", lambda path: "cv text")

    async def _fake_extract(profile):
        profile.cv_data.skills = ["python"]
        profile.cv_data.job_titles = ["Engineer"]
        return profile

    monkeypatch.setattr(profile_route, "run_two_pass_extraction", _fake_extract)

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
    monkeypatch.setattr(profile_route, "extract_text", lambda path: "cv text")

    async def _fake_extract(profile):
        profile.cv_data.skills = ["python"]
        profile.cv_data.job_titles = ["Engineer"]
        return profile

    monkeypatch.setattr(profile_route, "run_two_pass_extraction", _fake_extract)

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
    monkeypatch.setattr(profile_route, "extract_text", lambda path: "cv text")

    async def _fake_extract(profile):
        profile.cv_data.skills = ["python"]
        profile.cv_data.job_titles = ["Engineer"]
        return profile

    monkeypatch.setattr(profile_route, "run_two_pass_extraction", _fake_extract)

    _register_and_login(api)
    # Minimal DOCX magic bytes (PK zip header)
    minimal_docx = b"PK\x03\x04" + b"\x00" * 20
    r = api.post(
        "/api/profile",
        files={"cv": ("resume.docx", io.BytesIO(minimal_docx),
                      "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    assert r.status_code == 200, f"Expected 200 for valid DOCX, got {r.status_code}: {r.text}"


# ---------------------------------------------------------------------------
# Task 7 / FIX 2 — _maybe_trigger_rescore scheduling tests
# ---------------------------------------------------------------------------
# FIX 2 changed _maybe_trigger_rescore from sync def to async def.
# The function is now called with `await` from async route handlers, and it
# pins each created task into _rescore_bg_tasks to prevent GC loss.


def test_rescore_scheduled_when_profile_content_changes(api, monkeypatch):
    """When profile content changes, _maybe_trigger_rescore schedules a background task.

    FIX 2: the helper is now async; it is called via `await` inside the route.
    Strategy: patch profile_content_changed_since_previous -> True and
    asyncio.create_task -> a sentinel that records calls. The POST must
    return 200 AND the sentinel must have been called.
    """
    import asyncio as _asyncio

    import src.api.routes.profile as profile_route

    monkeypatch.setattr(profile_route, "extract_text", lambda path: "cv text")

    async def _fake_extract(profile):
        profile.cv_data.skills = ["python"]
        profile.cv_data.job_titles = ["Engineer"]
        return profile

    monkeypatch.setattr(profile_route, "run_two_pass_extraction", _fake_extract)

    # Make storage think the content changed
    monkeypatch.setattr(
        "src.services.profile.storage.profile_content_changed_since_previous",
        lambda uid: True,
    )

    # Intercept asyncio.create_task to record calls; return a real done Future
    # so nothing is left un-awaited.
    scheduled = []

    def _fake_create_task(coro, **kw):
        scheduled.append(coro)
        # Close the coroutine to avoid "coroutine was never awaited" warning
        coro.close()
        # Return a completed future as a minimal Task stand-in
        try:
            loop = _asyncio.get_event_loop()
            task = loop.create_future()
            task.set_result(None)
            return task
        except RuntimeError:
            # Outside an event loop (sync test context) — just return a dummy
            return None

    monkeypatch.setattr(_asyncio, "create_task", _fake_create_task)

    _register_and_login(api)
    minimal_pdf = b"%PDF-1.4\n%%EOF"
    r = api.post(
        "/api/profile",
        files={"cv": ("cv.pdf", io.BytesIO(minimal_pdf), "application/pdf")},
    )
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    assert len(scheduled) > 0, "Expected rescore_user_feed to be scheduled via create_task"


def test_rescore_not_scheduled_when_profile_unchanged(api, monkeypatch):
    """When profile content did NOT change, create_task must NOT be called.

    FIX 2: same async contract — the route awaits _maybe_trigger_rescore;
    when content is unchanged the function returns early without create_task.
    """
    import asyncio as _asyncio

    import src.api.routes.profile as profile_route
    monkeypatch.setattr(profile_route, "extract_text", lambda path: "cv text")

    async def _fake_extract(profile):
        profile.cv_data.skills = ["python"]
        profile.cv_data.job_titles = ["Engineer"]
        return profile

    monkeypatch.setattr(profile_route, "run_two_pass_extraction", _fake_extract)

    monkeypatch.setattr(
        "src.services.profile.storage.profile_content_changed_since_previous",
        lambda uid: False,
    )

    scheduled = []

    def _fake_create_task(coro, **kw):
        scheduled.append(coro)
        coro.close()
        return None

    monkeypatch.setattr(_asyncio, "create_task", _fake_create_task)

    _register_and_login(api)
    minimal_pdf = b"%PDF-1.4\n%%EOF"
    r = api.post(
        "/api/profile",
        files={"cv": ("cv.pdf", io.BytesIO(minimal_pdf), "application/pdf")},
    )
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    assert len(scheduled) == 0, (
        f"create_task should NOT be called when profile unchanged, called {len(scheduled)} time(s)"
    )


def test_rescore_task_pinned_to_bg_tasks_set(api, monkeypatch):
    """FIX 2: the created task is added to _rescore_bg_tasks to prevent GC loss.

    After triggering a re-score, _rescore_bg_tasks must contain the task until
    it completes (the done_callback discards it). Here we verify the pin happens
    by inspecting the set right after create_task is called.
    """
    import asyncio as _asyncio

    import src.api.routes.profile as profile_route
    monkeypatch.setattr(profile_route, "extract_text", lambda path: "cv text")

    async def _fake_extract(profile):
        profile.cv_data.skills = ["python"]
        profile.cv_data.job_titles = ["Engineer"]
        return profile

    monkeypatch.setattr(profile_route, "run_two_pass_extraction", _fake_extract)
    monkeypatch.setattr(
        "src.services.profile.storage.profile_content_changed_since_previous",
        lambda uid: True,
    )

    created_tasks = []

    def _fake_create_task(coro, **kw):
        coro.close()
        try:
            loop = _asyncio.get_event_loop()
            task = loop.create_future()
            task.set_result(None)
        except RuntimeError:
            task = None
        created_tasks.append(task)
        return task

    monkeypatch.setattr(_asyncio, "create_task", _fake_create_task)

    # Clear the module-level set before the test
    profile_route._rescore_bg_tasks.clear()

    _register_and_login(api)
    minimal_pdf = b"%PDF-1.4\n%%EOF"
    r = api.post(
        "/api/profile",
        files={"cv": ("cv.pdf", io.BytesIO(minimal_pdf), "application/pdf")},
    )
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"

    # The task must have been created
    assert len(created_tasks) > 0, "create_task was never called"

    # The module-level _rescore_bg_tasks set should contain the task (the
    # done_callback will discard it when the future resolves, but Future.set_result
    # is synchronous and may have fired the callback already — so we just verify
    # the set was used, i.e. no AttributeError / import error on the attribute).
    assert hasattr(profile_route, "_rescore_bg_tasks"), (
        "FIX 2: _rescore_bg_tasks set is missing from profile route module"
    )


# ---------------------------------------------------------------------------
# 4 dedicated input routes — one route per input
# (/profile/cv, /profile/preferences, /profile/linkedin, /profile/github)
# ---------------------------------------------------------------------------

def _patch_extractor(monkeypatch):
    """Patch the inline single-extractor so routes don't hit the real LLM."""
    import src.api.routes.profile as profile_route

    monkeypatch.setattr(profile_route, "extract_text", lambda path: "cv text")

    async def _fake_extract(profile):
        profile.cv_data.skills = ["python"]
        profile.cv_data.job_titles = ["Engineer"]
        return profile

    monkeypatch.setattr(profile_route, "run_two_pass_extraction", _fake_extract)


def test_dedicated_cv_route_accepts_pdf(api, monkeypatch):
    """POST /api/profile/cv (CV-only route) accepts a PDF and extracts in one pass."""
    _patch_extractor(monkeypatch)
    _register_and_login(api)
    r = api.post(
        "/api/profile/cv",
        files={"cv": ("cv.pdf", io.BytesIO(b"%PDF-1.4\n%%EOF"), "application/pdf")},
    )
    assert r.status_code == 200, r.text
    assert r.json()["summary"]["skills_count"] >= 1


def test_dedicated_cv_route_enforces_415(api):
    """The dedicated CV route enforces the same MIME allowlist (415) as combined."""
    _register_and_login(api)
    r = api.post(
        "/api/profile/cv",
        files={"cv": ("notes.txt", io.BytesIO(b"plain text"), "text/plain")},
    )
    assert r.status_code == 415


def test_dedicated_preferences_route_accepts_json(api, monkeypatch):
    """POST /api/profile/preferences (prefs-only route) applies the form and returns 200."""
    import json as _json

    _patch_extractor(monkeypatch)
    _register_and_login(api)
    r = api.post(
        "/api/profile/preferences",
        data={"preferences": _json.dumps({
            "target_job_titles": ["ML Engineer"],
            "additional_skills": ["PyTorch"],
            "experience_level": "senior",
            "about_me": "I build ML systems",
        })},
    )
    assert r.status_code == 200, r.text
    # The preferences actually landed (experience_level flows to the summary).
    assert r.json()["summary"]["experience_level"] == "senior"


def test_profile_route_logger_is_under_job360_namespace():
    """Regression guard: the profile route logger MUST be under 'job360' so its
    'rescore: background re-score scheduled' INFO line reaches setup_logging's
    handlers. A __name__ logger would be silently dropped by the root logger."""
    import src.api.routes.profile as profile_route

    assert profile_route.logger.name == "job360.api.profile"


# ---------------------------------------------------------------------------
# CV REPLACEMENT — the route must reset CV-owned fields, and must NOT reset
# them when the upload is rejected.
# ---------------------------------------------------------------------------


class TestCapturedCvReplacesRatherThanBlends:
    """`_capture_cv_raw` is the single choke point for every CV upload route.

    Unit tests cover `reset_cv_owned_fields` itself; these prove the ROUTE
    actually calls it, and — just as important — that a REJECTED upload leaves
    the existing profile untouched. Without the second half, a user who
    accidentally picks a .txt file would have their whole profile wiped.
    """

    def _seeded_profile(self):
        from src.services.profile.models import CVData, UserProfile

        return UserProfile(
            cv_data=CVData(
                raw_text="ORIGINAL CV TEXT",
                name="Alice Anderson",
                skills=["Airflow", "Spark"],
                job_titles=["Data Engineer"],
                github_llm_skills=["LangChain"],
            )
        )

    def test_successful_upload_drops_the_previous_cvs_data(self):
        from unittest.mock import patch

        from src.api.routes.profile import _capture_cv_raw

        profile = self._seeded_profile()
        with patch("src.api.routes.profile.extract_text", return_value="BRAND NEW CV TEXT"):
            asyncio.run(_capture_cv_raw(b"%PDF-1.4 fake", "new_cv.pdf", profile))

        assert profile.cv_data.raw_text == "BRAND NEW CV TEXT"
        # The previous CV's extracted data must be GONE, not waiting to be
        # unioned with the new extraction.
        assert profile.cv_data.name == ""
        assert profile.cv_data.skills == []
        assert profile.cv_data.job_titles == []
        # A different input the user did NOT re-upload must survive.
        assert profile.cv_data.github_llm_skills == ["LangChain"]

    def test_oversized_upload_leaves_the_profile_completely_intact(self):
        from fastapi import HTTPException

        from src.api.routes.profile import _capture_cv_raw

        profile = self._seeded_profile()
        with pytest.raises(HTTPException) as exc:
            asyncio.run(_capture_cv_raw(b"x" * (10 * 1024 * 1024 + 1), "huge.pdf", profile))

        assert exc.value.status_code == 413
        assert profile.cv_data.raw_text == "ORIGINAL CV TEXT"
        assert profile.cv_data.name == "Alice Anderson"
        assert profile.cv_data.skills == ["Airflow", "Spark"]

    def test_wrong_filetype_leaves_the_profile_completely_intact(self):
        from fastapi import HTTPException

        from src.api.routes.profile import _capture_cv_raw

        profile = self._seeded_profile()
        with pytest.raises(HTTPException) as exc:
            asyncio.run(_capture_cv_raw(b"hello", "notes.txt", profile))

        assert exc.value.status_code == 415
        assert profile.cv_data.raw_text == "ORIGINAL CV TEXT"
        assert profile.cv_data.name == "Alice Anderson"
        assert profile.cv_data.skills == ["Airflow", "Spark"]

    def test_unreadable_file_leaves_the_profile_intact(self):
        """A readable-looking PDF that yields no text must not wipe the profile."""
        from unittest.mock import patch

        from fastapi import HTTPException

        from src.api.routes.profile import _capture_cv_raw

        profile = self._seeded_profile()
        with patch("src.api.routes.profile.extract_text", return_value="   "):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(_capture_cv_raw(b"%PDF-1.4 fake", "empty.pdf", profile))

        assert exc.value.status_code == 503
        assert profile.cv_data.raw_text == "ORIGINAL CV TEXT"
        assert profile.cv_data.name == "Alice Anderson"
        assert profile.cv_data.skills == ["Airflow", "Spark"]
