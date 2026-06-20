import os

# --- Rule #18 test hermeticity (Pillar-2 feature flags) --------------------
# The running app enables ENRICHMENT_ENABLED / SEMANTIC_ENABLED via the repo
# .env so the dashboard uses all three engines. The test suite must NOT inherit
# that — rule #18 says behaviour with the flags OFF must be the verified
# baseline. settings.py calls load_dotenv(override=False), which will not
# clobber a value already present in os.environ, so seeding "false" here (before
# any `src.*`/`migrations` import binds the module-level constants) blocks the
# .env value from leaking into tests. setdefault keeps an explicit shell export
# (SEMANTIC_ENABLED=true pytest ...) working for ad-hoc debugging, and any test
# that needs a flag ON opts in via its own monkeypatch.
os.environ.setdefault("SEMANTIC_ENABLED", "false")
os.environ.setdefault("ENRICHMENT_ENABLED", "false")
os.environ.setdefault("MATCHER_ENABLED", "false")

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from migrations import runner
from src.models import Job
from src.services.channels import crypto


@pytest.fixture(autouse=True)
def _instant_asyncio_sleep(monkeypatch, request):
    """Make ``asyncio.sleep`` instant for the whole suite.

    Source retry backoff (``BaseJobSource`` 1s/2s/4s) and scraper pacing sleeps
    (e.g. LinkedIn 3s/query) otherwise sum to ~37 minutes of real wall-clock
    across the suite — the long-documented "suite hangs" symptom (it never
    deadlocked; it was just agonizingly slow). Mocking sleep is safe: tests
    assert on results, not timing. ``delay=0`` is preserved so any test that
    deliberately yields control still does.
    """
    # Opt-out for the few tests that assert on real elapsed time (e.g. the
    # rate-limiter delay). Mark them ``@pytest.mark.real_sleep``.
    if request.node.get_closest_marker("real_sleep"):
        return

    real_sleep = asyncio.sleep

    async def _instant(delay, *args, **kwargs):
        return await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", _instant)


@pytest.fixture(autouse=True)
def _close_leaked_app_db():
    """Backstop: close any app DB singleton a test leaves open.

    Complements ``authenticated_async_context``'s own teardown — covers tests
    that lazily create ``dependencies._db`` without that fixture. aiosqlite's
    non-daemon worker thread otherwise lingers and blocks interpreter exit.
    """
    yield
    from src.api import dependencies

    if getattr(dependencies, "_db", None) is not None:
        try:
            asyncio.run(dependencies.close_db())
        except Exception:
            dependencies._db = None


# Pinned test timestamp — avoid non-determinism from datetime.now() leaking
# into fixture-built Job objects. Tier-A #7.
_TEST_NOW = datetime(2026, 4, 23, 12, 0, 0, tzinfo=timezone.utc)
_TEST_NOW_ISO = _TEST_NOW.isoformat()


@asynccontextmanager
async def _noop_lifespan(app):
    yield


@pytest.fixture
def authenticated_async_context(monkeypatch, tmp_path):
    """Batch 3.5.4 — fixture for async API tests that need auth.

    Returns a factory callable. Inside an async test::

        async def test_foo(authenticated_async_context):
            async with authenticated_async_context() as client:
                resp = await client.get("/api/profile")

    Under the hood the fixture:
      * creates a tmp sqlite DB + runs all migrations (0000..0006)
      * patches DB_PATH on every known settings/routes/auth_deps capture
      * resets the ``dependencies._db`` singleton so it lazy-binds to the
        tmp DB
      * sets SESSION_SECRET + CHANNEL_ENCRYPTION_KEY envs, fresh per test
      * registers a throwaway user via sync TestClient (simplest cookie
        capture) and stashes the session cookie on the factory
      * replaces ``app.router.lifespan_context`` with a no-op so
        ASGITransport(app=app) doesn't fire the real lifespan
      * yields a single-use AsyncClient with the session cookie set
    """
    db_path = tmp_path / "test.db"
    _bootstrap_async_db(str(db_path))

    from src.api import auth_deps, dependencies
    from src.api.routes import auth as auth_route
    from src.api.routes import channels as channels_route
    from src.core import settings

    monkeypatch.setattr(settings, "DB_PATH", db_path, raising=True)
    monkeypatch.setattr(dependencies, "DB_PATH", db_path, raising=True)
    monkeypatch.setattr(auth_deps, "DB_PATH", db_path, raising=True)
    monkeypatch.setattr(auth_route, "DB_PATH", db_path, raising=True)
    monkeypatch.setattr(channels_route, "DB_PATH", db_path, raising=True)
    monkeypatch.setattr(dependencies, "_db", None, raising=False)

    crypto.set_test_key(Fernet.generate_key().decode("ascii"))
    monkeypatch.setenv("SESSION_SECRET", "test-secret-" + "z" * 40)

    from src.api.main import app

    # Redirect DB_PATH on EVERY module that captured it at import time. A
    # ``from src.core.settings import DB_PATH`` binds the *value*, so patching
    # ``settings`` alone leaves importers (e.g. ``services/profile/storage.py``)
    # pinned to the production DB — the root cause of cross-test
    # ``no such table: user_profiles`` / ``no such column`` failures (they pass
    # alone but fail in full-suite ordering, depending on which DB an importer
    # bound to first). Done AFTER the app import so all route/service modules
    # are loaded and patchable.
    import sys as _sys

    for _mod in list(_sys.modules.values()):
        _name = getattr(_mod, "__name__", "")
        if _name.startswith(("src.", "migrations")) and getattr(_mod, "DB_PATH", None) is not None:
            monkeypatch.setattr(_mod, "DB_PATH", db_path, raising=False)

    app.router.lifespan_context = _noop_lifespan  # type: ignore[assignment]

    # Register a user synchronously to capture the session cookie, then
    # hand it to the async client (which can't easily register because
    # the auth route currently expects sync cookie jar semantics).
    sync_client = TestClient(app)
    r = sync_client.post(
        "/api/auth/register",
        json={"email": "test@example.com", "password": "s3cretpassword"},
    )
    assert r.status_code == 201, r.text
    session_cookie = sync_client.cookies.get("job360_session")
    assert session_cookie, "authenticated_async_context: failed to capture session cookie"
    # Step-1.5 — capture the registered user's id BEFORE closing the
    # sync client, so tests that insert per-user rows directly via
    # aiosqlite (notification ledger, actions) scope their inserts to
    # the same id the route queries under.
    me = sync_client.get("/api/auth/me")
    captured_user_id = me.json().get("id") if me.status_code == 200 else None
    sync_client.close()

    # Email verification is enforced on app routes (Finding #15). New users
    # register UNVERIFIED, so mark this fixture user verified — otherwise every
    # authed test hitting a gated route would 403. (Unverified behaviour is
    # covered explicitly in test_email_enforcement.py.)
    if captured_user_id:
        import sqlite3 as _sqlite3

        _vc = _sqlite3.connect(str(db_path))
        _vc.execute(
            "UPDATE users SET email_verified_at = ? WHERE id = ?",
            ("2026-01-01T00:00:00Z", captured_user_id),
        )
        _vc.commit()
        _vc.close()

    @asynccontextmanager
    async def _make():
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            cookies={"job360_session": session_cookie},
        ) as client:
            yield client

    _make.fixture_user_id = captured_user_id  # type: ignore[attr-defined]
    yield _make

    # Teardown: close the lazily-created app DB singleton. aiosqlite leaves a
    # non-daemon `_connection_worker_thread` per open connection; not closing
    # them accumulates threads that block interpreter shutdown (the long-
    # observed test_api.py "exit-hang"). This runs while `monkeypatch` is still
    # active — i.e. BEFORE it restores `_db` and discards this test's
    # connection reference — so `dependencies._db` still points at it. Cross-
    # loop close is safe even though the request loop is already gone.
    if dependencies._db is not None:
        try:
            asyncio.run(dependencies.close_db())
        except Exception:
            dependencies._db = None


@pytest.fixture
def fixture_user_id(authenticated_async_context):
    """Step-1.5 — convenience fixture exposing the user id created by
    ``authenticated_async_context``. Useful when a test needs to insert
    rows directly via aiosqlite under the same user_id the route will
    query under.
    """
    return getattr(authenticated_async_context, "fixture_user_id", None)


def _bootstrap_async_db(db_path: str) -> None:
    """Initialize the full JobDatabase schema + apply migrations 0000..0006.

    Uses ``JobDatabase.init_db()`` rather than a hand-written executescript
    so the schema stays in sync with production (incl. match_score,
    visa_flag, salary_min/max, description columns, etc.).
    """

    async def _bootstrap():
        from src.repositories.database import JobDatabase

        db = JobDatabase(db_path)
        await db.init_db()
        await db.close()
        await runner.up(db_path)

    asyncio.run(_bootstrap())


@pytest.fixture
def sample_ai_job():
    return Job(
        title="AI Engineer",
        company="DeepMind",
        location="London, UK",
        salary_min=70000,
        salary_max=100000,
        description=(
            "We are looking for an AI Engineer with experience in Python, PyTorch, "
            "TensorFlow, and LangChain. You will work on RAG pipelines, LLM fine-tuning, "
            "and NLP tasks. Experience with AWS SageMaker, Docker, and Kubernetes preferred. "
            "This role involves Deep Learning and Neural Networks research. "
            "Visa sponsorship available."
        ),
        apply_url="https://deepmind.com/careers/ai-engineer",
        source="greenhouse",
        date_found=_TEST_NOW_ISO,
    )


@pytest.fixture
def sample_unrelated_job():
    return Job(
        title="Marketing Manager",
        company="Acme Corp",
        location="New York, US",
        description="Looking for a marketing manager with SEO and social media experience.",
        apply_url="https://acme.com/careers/marketing",
        source="reed",
        date_found=_TEST_NOW_ISO,
    )


@pytest.fixture
def sample_duplicate_jobs():
    base = dict(
        title="ML Engineer",
        company="Revolut",
        location="London",
        description="ML Engineer role requiring Python and PyTorch experience.",
        date_found=_TEST_NOW_ISO,
    )
    return [
        Job(**base, apply_url="https://reed.co.uk/jobs/123", source="reed", salary_min=60000, salary_max=80000),
        Job(**base, apply_url="https://adzuna.co.uk/jobs/456", source="adzuna"),
    ]


@pytest.fixture
def sample_visa_job():
    return Job(
        title="Data Scientist",
        company="Faculty AI",
        location="London, UK",
        description="Data Scientist role. We offer visa sponsorship for the right candidate.",
        apply_url="https://faculty.ai/careers/ds",
        source="lever",
        date_found=_TEST_NOW_ISO,
    )


@pytest.fixture
def sample_non_uk_job():
    return Job(
        title="Software Engineer",
        company="Bay Area Corp",
        location="San Francisco, CA",
        description="Backend development role.",
        apply_url="https://example.com/sf-job",
        source="linkedin",
        date_found=_TEST_NOW_ISO,
    )


@pytest.fixture
def sample_empty_description_job():
    return Job(
        title="AI Engineer",
        company="Mystery Co",
        location="London",
        description="",
        apply_url="https://example.com/mystery",
        source="greenhouse",
        date_found=_TEST_NOW_ISO,
    )
