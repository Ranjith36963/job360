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
import contextlib
import sys

# --- Windows selector event loop (psycopg async requires it) ---------------
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


# --- aiohttp 3.14 <-> aioresponses compatibility shim (TEST-ONLY) -----------
# Lives in tests/aiohttp314_shim.py (full rationale there) so that
# scripts/ssrf_drill.py -- which mocks HTTP with aioresponses but runs OUTSIDE
# pytest -- can install the same shim. Keep this the first aiohttp-touching
# line: the patch must land before any ClientResponse is built.
from tests.aiohttp314_shim import install as _install_aioresponses_aiohttp314_shim

_install_aioresponses_aiohttp314_shim()

# --- Postgres test mode ------------------------------------------------------
# Everything runs on Postgres via ``src.repositories.pg`` / ``pgsync`` — tests
# import those directly (the old ``sys.modules["aiosqlite"/"sqlite3"]`` disguise
# is gone). TEST_MODE enables schema-per-path isolation: each distinct
# ``db_path`` (tmp file or ``:memory:``) gets its own fresh Postgres schema —
# the same "fresh DB per test" the old tmp ``.db`` files gave.
from src.repositories import pg as _pg  # noqa: E402

_pg.TEST_MODE = True
from contextlib import asynccontextmanager  # noqa: E402
from datetime import datetime, timezone  # noqa: E402

import pytest  # noqa: E402
from cryptography.fernet import Fernet  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from migrations import runner  # noqa: E402
from src.models import Job  # noqa: E402
from src.services.channels import crypto  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_auth_rate_limit():
    """Clear the auth rate-limiter's module-global buckets around every test.

    ``services/auth/rate_limit.py`` keeps its sliding windows in process-global
    dicts. The register throttle (``register:<ip>``) and the password-reset
    throttles accumulate across tests because the whole suite shares one
    TestClient IP (``testclient``). Without this reset, the 11th register call
    anywhere in the session would 429 and unrelated tests would fail. Reset
    before and after so each test starts from an empty limiter.
    """
    from src.services.auth import rate_limit as _rl

    _rl.reset_for_tests()
    yield
    _rl.reset_for_tests()


@pytest.fixture(autouse=True)
def _loop_guard_strict(monkeypatch):
    """The whole suite runs the event-loop guard in STRICT mode.

    ``@cpu_bound`` (src/utils/loop_guard.py) decides raise-vs-log from the
    environment. Pinning it here means strictness never depends on which env
    vars happen to be exported on the box — a CPU-bound helper called inline on
    the loop fails LOUDLY in tests, every time, on any fixture size.

    The loop-lag WATCHDOG is forced OFF for the opposite reason: the
    ``_instant_asyncio_sleep`` fixture below makes ``asyncio.sleep`` return
    immediately, which would turn the watchdog's 100 ms sampler into a
    busy-spin that starves the test it is meant to observe.
    """
    from src.utils import loop_guard as _lg

    monkeypatch.setattr(_lg, "STRICT_OVERRIDE", True, raising=False)
    monkeypatch.setenv("LOOP_WATCHDOG_ENABLED", "false")


@pytest.fixture(autouse=True)
def _search_ui_enabled_for_legacy_suite(monkeypatch):
    """R12 (spec 2026-09-04-application-spine) — SEARCH_UI_ENABLED defaults to
    OFF in production, but the legacy search suite (hundreds of tests,
    slated for deletion in slice 5) still exercises the real routes. Force
    it ON for the whole suite here; tests/test_search_flag.py pins BOTH
    positions of the flag explicitly via monkeypatch.setattr(settings, ...).

    Its own fixture (C6, split out of ``_loop_guard_strict``, which is about
    the event-loop guard and unrelated): same autouse/function scope, so this
    changes nothing about schema isolation between tests — only which fixture
    function the setattr call lives in.
    """
    from src.core import settings as _settings

    monkeypatch.setattr(_settings, "SEARCH_UI_ENABLED", True, raising=False)


@pytest.fixture(autouse=True)
def _offline_openai(monkeypatch):
    """Keep the WHOLE suite offline for LLMs (rule #4).

    The repo ``.env`` carries real keys for OpenAI + the free tiers, and
    ``settings.py`` reads them at import. Any un-mocked ``llm_extract`` /
    ``llm_extract_fast`` call would otherwise hit a live provider — slow, flaky,
    and non-deterministic. Blank ALL provider keys here; a test that genuinely
    exercises a provider patches its key back with ``patch.object``. Un-mocked
    LLM calls then raise the no-key error, which every caller handles gracefully.
    """
    try:
        import src.services.profile.llm_provider as _lp

        for _k in ("OPENAI_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY", "CEREBRAS_API_KEY"):
            monkeypatch.setattr(_lp, _k, "", raising=False)
    except Exception:
        pass


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


def cancel_pending_rescores(tasks) -> int:
    """Cancel every unfinished task in `tasks`, clear it, return how many.

    Split out of the fixture below so it can be TESTED. A teardown hook that
    only ever runs as a side effect of other tests is exactly the kind of guard
    this repo keeps finding dead — see scripts/drill_registry.py.

    Each task's loop is the request loop, not this thread's, so the cancel is
    posted with `call_soon_threadsafe` rather than called directly.
    """
    if not tasks:
        return 0
    cancelled = 0
    for task in list(tasks):
        if task.done():
            continue
        try:
            loop = task.get_loop()
            if loop.is_closed():
                continue
            loop.call_soon_threadsafe(task.cancel)
            cancelled += 1
        except (RuntimeError, AttributeError):
            # Loop already stopping or closed between the check and the call,
            # or an object that is not a task. Either way there is nothing left
            # to cancel.
            continue
    tasks.clear()
    return cancelled


@pytest.fixture(autouse=True)
def _drain_leaked_rescore_tasks():
    """Stop a re-score task from outliving the test whose schema it queries.

    THE FLAKE THIS FIXES (issue #369). Three gate runs on 2026-08-23 failed with
    ``relation "users" does not exist`` / ``relation "user_profiles" does not
    exist``, each blaming a DIFFERENT test in ``test_profile_upload.py``, each
    passing when that file ran alone. The gate log carries the whole chain:

        queue.py:67   enqueue skipped: REDIS_URL is not set, so
                      rescore_user_feed_task cannot be queued
        profile.py:94 rescore: background re-score FAILED, feed left on a stale
                      profile_version: OperationalError('relation
                      "user_profiles" does not exist')

    i.e. no Redis in tests -> `_run_rescore_in_process` -> `asyncio.create_task`,
    pinned in `_rescore_bg_tasks` and never awaited. The test ends, the per-test
    schema is dropped, and the orphan then queries a schema that is gone. The
    failure is attributed to WHATEVER TEST IS RUNNING NOW, which is why it looked
    random: the blamed test is innocent.

    It is a RACE, not a certainty — a task that finishes inside its own request
    is harmless, which is why the same batch passes and fails on different runs.

    The fallback itself is correct and deliberate (issue #271: losing the work is
    worse than doing it fragilely). What was missing is that nothing collected
    the tasks it leaves behind.

    Cancelling rather than draining: the task's loop belongs to the request and
    this synchronous teardown cannot drive it, and a full-feed re-score is not
    work a unit test needs finished. `_rescore_finished` logs the cancellation,
    so the tasks stay audible rather than vanishing.
    """
    yield

    from src.api.routes import profile as _profile

    cancel_pending_rescores(getattr(_profile, "_rescore_bg_tasks", None))


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

    # Redirect DB_PATH on EVERY module that captured it at import time. A
    # ``from src.core.settings import DB_PATH`` binds the *value*, so patching
    # ``settings`` alone leaves importers (e.g. ``services/profile/storage.py``)
    # pinned to the production DB — the root cause of cross-test
    # ``no such table: user_profiles`` / ``no such column`` failures (they pass
    # alone but fail in full-suite ordering, depending on which DB an importer
    # bound to first). Done AFTER the app import so all route/service modules
    # are loaded and patchable.
    import sys as _sys

    from src.api.main import app

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
    # M2 — register no longer auto-logs-in (no-enumeration); sign in for the cookie.
    lr = sync_client.post(
        "/api/auth/login",
        json={"email": "test@example.com", "password": "s3cretpassword"},
    )
    assert lr.status_code == 200, lr.text
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
        from src.repositories import pgsync as _sqlite3

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

    # Drop this test's Postgres schema so schemas don't accumulate.
    with contextlib.suppress(Exception):
        asyncio.run(_pg.drop_schema(str(db_path)))


@pytest.fixture
def fixture_user_id(authenticated_async_context):
    """Step-1.5 — convenience fixture exposing the user id created by
    ``authenticated_async_context``. Useful when a test needs to insert
    rows directly via aiosqlite under the same user_id the route will
    query under.
    """
    return getattr(authenticated_async_context, "fixture_user_id", None)


def _bootstrap_async_db(db_path: str) -> None:
    """Initialize the full JobDatabase schema + apply ALL migrations (0000..latest).

    Uses ``JobDatabase.init_db()`` rather than a hand-written executescript
    so the schema stays in sync with production (incl. match_score,
    visa_flag, salary_min/max, description columns, etc.), then runs the whole
    migration set so migration-created tables (users, job_enrichment, user_feed,
    audit_log, …) exist too.
    """

    async def _bootstrap():
        from src.repositories.database import JobDatabase

        db = JobDatabase(db_path)
        await db.init_db()
        await db.close()
        await runner.up(db_path)

    asyncio.run(_bootstrap())


@pytest.fixture
def migrated_db_path(tmp_path):
    """A tmp-file DB path with the FULL schema (init_db + every migration).

    Use for any test that touches a MIGRATION-created table — ``users`` (0001),
    ``job_enrichment`` (0008), ``user_feed`` (0011), ``audit_log`` (0025), … A
    bare ``JobDatabase(":memory:")`` only runs ``init_db()``, which creates the
    legacy pre-Batch-2 tables ONLY, so those tests 500 on ``relation ... does
    not exist``. And ``:memory:`` can't be migrated after the fact: it hashes to
    a fresh RANDOM schema on every connect (``schema_for_path``), so a follow-up
    ``runner.up(":memory:")`` would migrate a DIFFERENT schema than the one the
    test writes to. A stable tmp-file path keeps ``init_db`` and ``runner.up`` in
    one schema (the abspath hashes to a stable ``t_*`` name).
    """
    db_path = str(tmp_path / "migrated.db")
    _bootstrap_async_db(db_path)
    yield db_path
    # Drop the per-test schema so it doesn't accumulate in the shared Postgres
    # (the async_client fixture does the same). Harmless in fresh CI; keeps a
    # long-lived dev DB from silting up with hundreds of leftover t_* schemas.
    asyncio.run(_pg.drop_schema(db_path))


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


def pytest_sessionfinish(session, exitstatus):
    """Drop every per-test Postgres schema created during the run.

    Test-local fixtures that build their own tmp DB don't always drop their
    schema; sweep them all at session end so the shared Postgres stays clean.
    """
    import psycopg

    try:
        conn = psycopg.connect(_pg.DEFAULT_DSN, autocommit=True)
    except psycopg.Error:
        return
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT schema_name FROM information_schema.schemata "
                "WHERE schema_name LIKE 't\\_%' OR schema_name LIKE 'mem\\_%'"
            )
            schemas = [r[0] for r in cur.fetchall()]
            for s in schemas:
                cur.execute(f'DROP SCHEMA IF EXISTS "{s}" CASCADE')
    finally:
        conn.close()
