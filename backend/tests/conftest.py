import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from migrations import runner
from src.models import Job
from src.services.channels import crypto


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
    sync_client.close()

    @asynccontextmanager
    async def _make():
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            cookies={"job360_session": session_cookie},
        ) as client:
            yield client

    return _make


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
        date_found=datetime.now(timezone.utc).isoformat(),
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
        date_found=datetime.now(timezone.utc).isoformat(),
    )


@pytest.fixture
def sample_duplicate_jobs():
    base = dict(
        title="ML Engineer",
        company="Revolut",
        location="London",
        description="ML Engineer role requiring Python and PyTorch experience.",
        date_found=datetime.now(timezone.utc).isoformat(),
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
        date_found=datetime.now(timezone.utc).isoformat(),
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
        date_found=datetime.now(timezone.utc).isoformat(),
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
        date_found=datetime.now(timezone.utc).isoformat(),
    )
