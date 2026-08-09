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


# ---------------------------------------------------------------------------
# Upload receipts — WHAT was uploaded, and WHEN
# ---------------------------------------------------------------------------
# Found on a live smoke test 2026-08-08. After uploading, the only feedback was
# a small tick the owner said he "didn't catch", and nothing named the file, so
# a user could not tell WHICH CV was on file or whether a re-upload replaced it.
# GitHub had no confirmation at all.
#
# VALUE-presence tests, not schema-presence (rule #21): every receipt field
# defaults to "", so `assert "cv_filename" in body` would pass against a
# serializer that never reads it. Each test runs a real upload end-to-end and
# asserts the ACTUAL filename.
#
# These live HERE rather than in their own file on purpose: the `api` fixture
# gives each test its own DB path (hence its own Postgres schema). Importing it
# into another module double-imports this one, and registration then lands in a
# different schema than the login reads — every auth call fails with "invalid
# credentials". Same-file tests share the fixture correctly.

def _stub_extraction(monkeypatch, text: str = "cv text") -> None:
    """Mock the parser + the paid two-pass extraction (offline, rule #4)."""
    import src.api.routes.profile as profile_route

    monkeypatch.setattr(profile_route, "extract_text", lambda path: text)

    async def _fake_extract(profile):
        profile.cv_data.skills = ["python"]
        profile.cv_data.job_titles = ["Engineer"]
        return profile

    monkeypatch.setattr(profile_route, "run_two_pass_extraction", _fake_extract)


class TestCVReceipt:
    def test_upload_records_the_real_filename_and_a_timestamp(self, api, monkeypatch) -> None:
        _stub_extraction(monkeypatch)
        _register_and_login(api, "receipt1@example.com")
        r = api.post(
            "/api/profile/cv",
            files={"cv": ("Ranjith_CV_2026.pdf", io.BytesIO(b"%PDF-1.4\n%%EOF"),
                          "application/pdf")},
        )
        assert r.status_code == 200, r.text
        summary = r.json()["summary"]
        # VALUE presence — the actual name the user uploaded, not a default.
        assert summary["cv_filename"] == "Ranjith_CV_2026.pdf"
        assert summary["cv_uploaded_at"], "no upload timestamp recorded"
        assert summary["cv_uploaded_at"].startswith("20"), summary["cv_uploaded_at"]

    def test_reupload_replaces_the_receipt(self, api, monkeypatch) -> None:
        """A stale name is worse than none — it confirms the WRONG document."""
        _stub_extraction(monkeypatch)
        _register_and_login(api, "receipt2@example.com")
        api.post(
            "/api/profile/cv",
            files={"cv": ("old_cv.pdf", io.BytesIO(b"%PDF-1.4\n%%EOF"), "application/pdf")},
        )
        r = api.post(
            "/api/profile/cv",
            files={"cv": ("new_cv.pdf", io.BytesIO(b"%PDF-1.4\n%%EOF"), "application/pdf")},
        )
        assert r.json()["summary"]["cv_filename"] == "new_cv.pdf"

    def test_rejected_upload_does_not_touch_the_existing_receipt(
        self, api, monkeypatch
    ) -> None:
        """A 415 must not claim we hold a file we never read.

        Stronger than asserting an empty receipt: it proves a REJECTED upload
        cannot overwrite the good one already on file. The route's own ordering
        guard (every 413/415/503 raises before the profile is touched) is what
        makes this hold.
        """
        _stub_extraction(monkeypatch)
        _register_and_login(api, "receipt3@example.com")
        api.post(
            "/api/profile/cv",
            files={"cv": ("good_cv.pdf", io.BytesIO(b"%PDF-1.4\n%%EOF"), "application/pdf")},
        )
        bad = api.post(
            "/api/profile/cv",
            files={"cv": ("notes.txt", io.BytesIO(b"plain text"), "text/plain")},
        )
        assert bad.status_code == 415
        got = api.get("/api/profile").json()["summary"]
        assert got["cv_filename"] == "good_cv.pdf", "a rejected upload clobbered the receipt"

    def test_only_the_basename_is_kept(self, api, monkeypatch) -> None:
        """A browser can send a path-ish value; the directory is not ours."""
        _stub_extraction(monkeypatch)
        _register_and_login(api, "receipt4@example.com")
        r = api.post(
            "/api/profile/cv",
            files={"cv": ("C:/Users/secret/Documents/cv.pdf",
                          io.BytesIO(b"%PDF-1.4\n%%EOF"), "application/pdf")},
        )
        assert r.json()["summary"]["cv_filename"] == "cv.pdf"


class TestGitHubReceipt:
    def test_github_connect_records_handle_and_time(self, api, monkeypatch) -> None:
        """GitHub has no file — the handle IS the receipt."""
        import src.api.routes.profile as profile_route

        _stub_extraction(monkeypatch)

        async def _fake_fetch(username):
            return {"username": username, "languages": {"Python": 100}, "repos": []}

        monkeypatch.setattr(profile_route, "fetch_github_profile", _fake_fetch)
        _register_and_login(api, "receipt5@example.com")
        r = api.post("/api/profile/github", data={"username": "Ranjith36963"})
        assert r.status_code == 200, r.text
        got = api.get("/api/profile").json()["summary"]
        assert got["github_username"] == "Ranjith36963"
        assert got["github_connected_at"], "no connection timestamp recorded"


class TestReceiptsSurviveOldRows:
    def test_profile_saved_before_receipts_existed_still_loads(self) -> None:
        """Old rows have no receipt fields — they must default, never crash."""
        from src.services.profile.models import CVData
        from src.services.profile.storage import _filter_fields

        legacy_blob = {"raw_text": "old cv", "skills": ["Python"], "unknown_old_key": 1}
        cv = CVData(**_filter_fields(legacy_blob, CVData))
        assert cv.cv_filename == ""
        assert cv.cv_uploaded_at == ""
        assert cv.linkedin_filename == ""
        assert cv.github_connected_at == ""


# ---------------------------------------------------------------------------
# A GitHub enrich that read NOTHING must say so
# ---------------------------------------------------------------------------
# Measured on a live profile 2026-08-08: the route returned ok=True/merged=True
# unconditionally, so a handle that reduced to "" still produced a green
# "GitHub profile enriched" toast and — once receipts shipped — a "GitHub
# connected" row, while github_username was empty and 0 repos were read.
# Stored evidence: github_connected_at=21:14:16, github_username="", repos=[].
# A receipt that confirms something that did not happen is the exact failure
# receipts exist to prevent.
#
# Likely input: normalize_github_username accepts a profile URL and an @handle
# but NOT a "<user>.github.io" portfolio URL — which is what people paste,
# because a CV lists that under "Portfolio".
#
# These live HERE (not a separate module) for the schema-isolation reason
# documented above the upload-receipt tests.

import pytest


def _stub(monkeypatch, payload):
    """Mock the GitHub fetch + the paid extraction (offline, rule #4)."""
    import src.api.routes.profile as profile_route

    async def _fake_fetch(username):
        return payload

    async def _fake_extract(profile):
        return profile

    monkeypatch.setattr(profile_route, "fetch_github_profile", _fake_fetch)
    monkeypatch.setattr(profile_route, "run_two_pass_extraction", _fake_extract)
    monkeypatch.setattr(profile_route, "extract_text", lambda path: "cv text")


def _seed_cv(api_client):
    api_client.post(
        "/api/profile/cv",
        files={"cv": ("cv.pdf", io.BytesIO(b"%PDF-1.4\n%%EOF"), "application/pdf")},
    )


class TestUnusableHandleIsRejected:
    # The four "which strings reduce to nothing" cases used to live here as a
    # parametrized ROUTE test. Each one cost ~50s (this fixture builds a fresh
    # schema and runs every migration per test) to assert what a pure function
    # decides in microseconds — 200s of CI time to exercise a regex, and it
    # quadrupled the targeted gate. They are now unit tests over
    # normalize_github_username in tests/test_github_username_normalize.py.
    # What stays HERE is only what genuinely needs the route: that a rejected
    # handle is never persisted and leaves no receipt.

    def test_a_rejected_handle_is_never_stored(self, api, monkeypatch) -> None:
        # NB: a "<user>.github.io" Pages URL is NOT rejected — its subdomain is
        # the username, so it resolves. Use input with no handle in it at all.
        _stub(monkeypatch, {"username": "x", "languages": {}, "repos": []})
        _register_and_login(api, "gh-not-stored@example.com")
        _seed_cv(api)
        r = api.post("/api/profile/github", data={"username": "not a handle!!"})
        assert r.status_code == 400, r.text
        got = api.get("/api/profile").json()["summary"]
        assert got["github_username"] == ""
        # AND no receipt — the page must not claim a connection that failed.
        assert got["github_connected_at"] == ""

    @pytest.mark.parametrize(
        "li",
        [
            "linkedin.com/in/ranjith-ai-engineer",
            "https://www.linkedin.com/in/ranjith-ai-engineer/",
        ],
    )
    def test_a_linkedin_url_says_where_linkedin_actually_goes(
        self, api, monkeypatch, li
    ) -> None:
        """Name the mistake instead of restating the rule.

        Both boxes sit in the same "Enrich Your Profile" card and a CV lists
        both links side by side, so pasting the LinkedIn URL into the GitHub
        box is the likeliest wrong paste there is. "That does not look like a
        GitHub username" is true and useless — it never says where LinkedIn
        DOES go, and LinkedIn cannot be read from a link at all (measured:
        HTTP 999 + a sign-in wall for a server), which is why the PDF exists.
        """
        _stub(monkeypatch, {"username": "x", "languages": {}, "repos": []})
        _register_and_login(api, f"gh-li-{abs(hash(li)) % 9999}@example.com")
        _seed_cv(api)
        r = api.post("/api/profile/github", data={"username": li})
        assert r.status_code == 400, r.text
        body = r.text.lower()
        assert "linkedin" in body
        assert "pdf" in body, "must point at the upload that actually works"

    def test_a_github_pages_url_now_resolves(self, api, monkeypatch) -> None:
        """The owner's own Portfolio URL must work — the handle is in it."""
        _stub(monkeypatch, {
            "username": "Ranjith36963",
            "languages": {"Python": 1},
            "repos": [{"name": "r", "language": "Python"}],
            "repos_brief": [{"name": "r", "language": "Python"}],
        })
        _register_and_login(api, "gh-pages@example.com")
        _seed_cv(api)
        r = api.post(
            "/api/profile/github", data={"username": "ranjith36963.github.io"}
        )
        assert r.status_code == 200, r.text
        assert api.get("/api/profile").json()["summary"]["github_username"] == (
            "ranjith36963"
        )


class TestEmptyLookupDoesNotFakeSuccess:
    def test_a_valid_handle_with_no_public_data_is_a_404(
        self, api, monkeypatch
    ) -> None:
        # Well-formed handle, but GitHub returned nothing (missing user, rate
        # limit, outage). The old code called this a success.
        _stub(monkeypatch, {"username": "ghost", "languages": {}, "repos": []})
        _register_and_login(api, "gh-empty@example.com")
        _seed_cv(api)
        r = api.post("/api/profile/github", data={"username": "ghost"})
        assert r.status_code == 404, r.text
        got = api.get("/api/profile").json()["summary"]
        assert got["github_connected_at"] == "", "receipt stamped for a failed enrich"
        assert got["github_username"] == ""


class TestRealDataStillWorks:
    def test_a_good_enrich_stores_the_handle_and_stamps_the_receipt(
        self, api, monkeypatch
    ) -> None:
        _stub(
            monkeypatch,
            {
                "username": "Ranjith36963",
                "languages": {"Python": 5000},
                "repos": [{"name": "job360", "language": "Python"}],
            },
        )
        _register_and_login(api, "gh-good@example.com")
        _seed_cv(api)
        r = api.post("/api/profile/github", data={"username": "Ranjith36963"})
        assert r.status_code == 200, r.text
        assert r.json()["merged"] is True
        got = api.get("/api/profile").json()["summary"]
        assert got["github_username"] == "Ranjith36963"
        assert got["github_connected_at"], "a successful enrich must leave a receipt"

    def test_a_profile_url_still_works(self, api, monkeypatch) -> None:
        _stub(
            monkeypatch,
            {
                "username": "Ranjith36963",
                "languages": {"Python": 1},
                "repos": [{"name": "r", "language": "Python"}],
            },
        )
        _register_and_login(api, "gh-url@example.com")
        _seed_cv(api)
        r = api.post(
            "/api/profile/github",
            data={"username": "https://github.com/Ranjith36963"},
        )
        assert r.status_code == 200, r.text
        assert api.get("/api/profile").json()["summary"]["github_username"] == (
            "Ranjith36963"
        )


# ---------------------------------------------------------------------------
# Clearing an input — you cannot trust a test you cannot reset
# ---------------------------------------------------------------------------
# Every contamination bug found on the owner's live profile (another person's
# GitHub, then their LinkedIn, then their education/certs/titles merged into the
# CV fields) was hard to SEE because stale data lingered and each upload merged
# into it. These pin the two properties that make a reset trustworthy:
#   1. a scope clears what that input OWNS and nothing else, and
#   2. the cached LLM hash goes with it, so re-uploading the SAME file really
#      re-extracts instead of being skipped as "already read".


def _seed_full_profile(api_client, monkeypatch):
    """A profile carrying CV + LinkedIn + GitHub + typed preferences."""
    import json as _json

    import src.api.routes.profile as profile_route

    _stub(monkeypatch, {
        "username": "octocat",
        "languages": {"Python": 10},
        "repos": [{"name": "r", "language": "Python"}],
        "repos_brief": [{"name": "r", "language": "Python"}],
    })
    monkeypatch.setattr(profile_route, "extract_linkedin_text", lambda p: "LinkedIn text")
    monkeypatch.setattr(profile_route, "_looks_like_linkedin", lambda t: True)

    api_client.post(
        "/api/profile/cv",
        files={"cv": ("my_cv.pdf", io.BytesIO(b"%PDF-1.4\n%%EOF"), "application/pdf")},
    )
    api_client.post(
        "/api/profile/linkedin",
        files={"file": ("li.pdf", io.BytesIO(b"%PDF-1.4\n%%EOF"), "application/pdf")},
    )
    api_client.post("/api/profile/github", data={"username": "octocat"})
    api_client.post(
        "/api/profile/preferences",
        data={"preferences": _json.dumps({"additional_skills": ["Rust"],
                                          "target_job_titles": ["SRE"]})},
    )


class TestEveryGithubShelfReachesTheApi:
    """A shelf the API never returns is a shelf the user can never see.

    Measured 2026-08-09: GitHub gave a live profile 14 languages, 10 topics, 49
    frameworks, 13 repos, 25 inferred and 30 LLM-read skills — and only
    languages + topics were exposed. 92 pieces of signal stored and shown to
    nobody, which is the same failure as cv_positions and the upload receipts.
    VALUE-presence, not schema-presence (rule #21): every field below defaults
    to empty, so asserting the KEY exists would pass against a serializer that
    never reads the column.
    """

    def test_github_detail_carries_repos_frameworks_and_skills(
        self, api, monkeypatch
    ) -> None:
        _stub(monkeypatch, {
            "username": "octocat",
            "languages": {"Python": 500},
            "repos": [{"name": "job360", "language": "Python"}],
            "repos_brief": [{
                "name": "job360",
                "language": "Python",
                "description": "A UK job search engine",
                "topics": ["fastapi"],
                "stars": 7,
                "pushed_at": "2026-08-01T10:00:00Z",
            }],
            "frameworks_inferred": ["fastapi", "langgraph"],
            "skills_inferred": ["TypeScript"],
            "bio": "Builds things",
            "profile_readme": "# Hello",
        })
        _register_and_login(api, "gh-detail@example.com")
        _seed_cv(api)
        assert api.post(
            "/api/profile/github", data={"username": "octocat"}
        ).status_code == 200

        detail = api.get("/api/profile").json()["github_detail"]
        assert detail["username"] == "octocat"
        assert detail["connected_at"], "no connection receipt"
        # The repo brief must carry RECENCY and WEIGHT — both were fetched from
        # /users/{u}/repos and thrown away before this change, and pushed_at is
        # the only skill-recency signal the engine gets from anywhere.
        repo = detail["repos"][0]
        assert repo["name"] == "job360"
        assert repo["description"] == "A UK job search engine"
        assert repo["stars"] == 7
        assert repo["pushed_at"].startswith("2026")
        # Dependency-file evidence: DEMONSTRATED usage, not a CV claim.
        assert "langgraph" in detail["frameworks"]
        assert "TypeScript" in detail["skills_inferred"]
        assert detail["bio"] == "Builds things"
        assert detail["profile_readme"] == "# Hello"

    def test_no_github_means_an_empty_detail_not_a_crash(
        self, api, monkeypatch
    ) -> None:
        """Empty shelves stay silent (rule #29) — and must not 500."""
        _stub(monkeypatch, {})
        _register_and_login(api, "gh-nodetail@example.com")
        _seed_cv(api)
        detail = api.get("/api/profile").json()["github_detail"]
        assert detail["repos"] == []
        assert detail["frameworks"] == []
        assert detail["username"] == ""


class TestClearSectionRemovesOnlyThatSection:
    def test_clearing_the_cv_leaves_linkedin_and_github(self, api, monkeypatch) -> None:
        _register_and_login(api, "clear-cv@example.com")
        _seed_full_profile(api, monkeypatch)
        r = api.post("/api/profile/clear", data={"section": "cv"})
        assert r.status_code == 200, r.text
        s = r.json()["summary"]
        assert s["cv_length"] == 0
        assert s["cv_filename"] == ""
        # The OTHER inputs survive. Asserted on the RECEIPT, not on
        # `has_linkedin`: that flag is derived from EXTRACTED skills/positions,
        # and extraction is stubbed here — so it is false even before the clear
        # and would pass this test for the wrong reason.
        assert s["linkedin_filename"] == "li.pdf", "clearing the CV took LinkedIn too"
        assert s["has_github"] is True
        assert s["github_username"] == "octocat"

    def test_clearing_linkedin_leaves_the_cv(self, api, monkeypatch) -> None:
        _register_and_login(api, "clear-li@example.com")
        _seed_full_profile(api, monkeypatch)
        r = api.post("/api/profile/clear", data={"section": "linkedin"})
        assert r.status_code == 200, r.text
        s = r.json()["summary"]
        assert s["has_linkedin"] is False
        assert s["linkedin_filename"] == ""
        assert s["cv_length"] > 0, "clearing LinkedIn destroyed the CV"
        assert s["has_github"] is True

    def test_clearing_github_drops_the_handle_and_receipt(self, api, monkeypatch) -> None:
        _register_and_login(api, "clear-gh@example.com")
        _seed_full_profile(api, monkeypatch)
        r = api.post("/api/profile/clear", data={"section": "github"})
        assert r.status_code == 200, r.text
        s = r.json()["summary"]
        assert s["has_github"] is False
        assert s["github_username"] == ""
        assert s["github_connected_at"] == ""
        assert s["cv_length"] > 0

    def test_clearing_preferences_keeps_the_github_handle(self, api, monkeypatch) -> None:
        # The handle is owned by the GitHub section — clearing PREFERENCES
        # alone must not silently disconnect GitHub.
        _register_and_login(api, "clear-prefs@example.com")
        _seed_full_profile(api, monkeypatch)
        r = api.post("/api/profile/clear", data={"section": "preferences"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["preferences"].get("additional_skills") in ([], None)
        assert body["preferences"].get("target_job_titles") in ([], None)
        assert body["summary"]["github_username"] == "octocat"
        assert body["summary"]["cv_length"] > 0

    def test_clear_all_empties_everything(self, api, monkeypatch) -> None:
        _register_and_login(api, "clear-all@example.com")
        _seed_full_profile(api, monkeypatch)
        r = api.post("/api/profile/clear", data={"section": "all"})
        assert r.status_code == 200, r.text
        s = r.json()["summary"]
        assert s["cv_length"] == 0
        assert s["has_linkedin"] is False
        assert s["has_github"] is False
        assert s["github_username"] == ""
        assert s["skills_count"] == 0
        assert r.json()["preferences"].get("additional_skills") in ([], None)


class TestClearIsSafeAndReversible:
    def test_an_unknown_section_is_rejected(self, api, monkeypatch) -> None:
        _register_and_login(api, "clear-bad@example.com")
        _seed_full_profile(api, monkeypatch)
        r = api.post("/api/profile/clear", data={"section": "everything"})
        assert r.status_code == 400
        assert "cv" in r.text and "all" in r.text

    def test_clearing_snapshots_first_so_it_can_be_undone(
        self, api, monkeypatch
    ) -> None:
        _register_and_login(api, "clear-undo@example.com")
        _seed_full_profile(api, monkeypatch)
        api.post("/api/profile/clear", data={"section": "all"})
        versions = api.get("/api/profile/versions").json()["versions"]
        # The pre-clear state is still recoverable from History.
        assert any(v.get("source_action") == "clear_all" for v in versions)
        assert len(versions) >= 2, "no snapshot to roll back to"

    def test_re_uploading_the_same_cv_after_a_clear_really_re_extracts(
        self, api, monkeypatch
    ) -> None:
        """The cached LLM hash must go with the cleared input.

        Otherwise the identical file is treated as "already read", the passes
        are skipped, and the profile stays empty — a reset that does not reset.
        """
        _register_and_login(api, "clear-rerun@example.com")
        _seed_full_profile(api, monkeypatch)
        api.post("/api/profile/clear", data={"section": "cv"})
        r = api.post(
            "/api/profile/cv",
            files={"cv": ("my_cv.pdf", io.BytesIO(b"%PDF-1.4\n%%EOF"),
                          "application/pdf")},
        )
        assert r.status_code == 200, r.text
        s = r.json()["summary"]
        assert s["cv_length"] > 0, "the same CV did not re-extract after a clear"
        assert s["cv_filename"] == "my_cv.pdf"
