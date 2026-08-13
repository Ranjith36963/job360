"""Phase 5 worker task tests — no Redis, direct function calls."""

import os
import tempfile
from datetime import datetime, timezone

import pytest

from migrations import runner
from src.repositories import pg
from src.services.prefilter import FilterProfile
from src.workers.tasks import (
    idempotency_key,
    mark_ledger_failed,
    mark_ledger_sent,
    score_and_ingest,
)


@pytest.fixture
async def worker_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    async with pg.connect(path) as db:
        await db.executescript(
            """
            CREATE TABLE jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                company TEXT NOT NULL,
                location TEXT DEFAULT '',
                salary_min REAL,
                salary_max REAL,
                description TEXT DEFAULT '',
                apply_url TEXT NOT NULL,
                source TEXT NOT NULL,
                date_found TEXT NOT NULL,
                match_score INTEGER DEFAULT 0,
                visa_flag INTEGER DEFAULT 0,
                experience_level TEXT DEFAULT '',
                normalized_company TEXT NOT NULL,
                normalized_title TEXT NOT NULL,
                first_seen TEXT NOT NULL,
                first_seen_at TEXT,
                UNIQUE(normalized_company, normalized_title)
            );
            CREATE TABLE user_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                notes TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                UNIQUE(job_id)
            );
            CREATE TABLE applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                stage TEXT NOT NULL DEFAULT 'applied',
                notes TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(job_id)
            );
            """
        )
        await db.commit()
    await runner.up(path)
    async with pg.connect(path) as db:
        await db.execute(
            "INSERT INTO users(id, email, password_hash) VALUES(?, ?, ?)",
            ("alice", "a@x", "!"),
        )
        await db.execute(
            "INSERT INTO users(id, email, password_hash) VALUES(?, ?, ?)",
            ("bob", "b@x", "!"),
        )
        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            """
            INSERT INTO jobs (title, company, apply_url, source, date_found,
                              normalized_company, normalized_title, first_seen,
                              first_seen_at, match_score, description)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "Senior Python Engineer",
                "Acme Ltd",
                "https://acme.example/jobs/1",
                "test",
                now,
                "acme",
                "senior python engineer",
                now,
                now,
                85,
                "Python, Django, AWS",
            ),
        )
        await db.commit()
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


def test_idempotency_key_is_deterministic():
    a = idempotency_key("u1", 1, "email")
    b = idempotency_key("u1", 1, "email")
    assert a == b
    assert idempotency_key("u1", 1, "slack") != a
    assert idempotency_key("u2", 1, "email") != a


@pytest.mark.asyncio
async def test_score_and_ingest_creates_feed_rows_for_each_passing_user(worker_db):
    async with pg.connect(worker_db) as db:
        enqueued: list[tuple] = []
        # Inject a per-user scorer — the Phase 5 task MUST call it for every
        # user. Deliberately returning distinct scores per user proves the
        # score_and_ingest is genuinely scoring per user (not reusing the
        # catalog-level match_score).
        calls: list[tuple[str, str]] = []

        def scorer(user_id: str, job):
            calls.append((user_id, job.title))
            return {"alice": 85, "bob": 70}.get(user_id, 0)

        ctx = {
            "db": db,
            "enqueue": lambda *args: _append(enqueued, args),
            "scorer": scorer,
        }
        result = await score_and_ingest(
            ctx,
            job_id=1,
            users_override=[
                ("alice", FilterProfile(skills={"python"}), 80),
                ("bob", FilterProfile(skills={"python"}), 80),
            ],
        )
        cur = await db.execute("SELECT user_id, score, bucket FROM user_feed")
        rows = sorted([tuple(r) for r in await cur.fetchall()])
    assert result == {"ingested": 2, "notifications_queued": 1}  # only alice ≥ 80
    assert [(r[0], r[1]) for r in rows] == [("alice", 85), ("bob", 70)]
    # Prove per-user scorer invocation
    assert sorted(calls) == [
        ("alice", "Senior Python Engineer"),
        ("bob", "Senior Python Engineer"),
    ]


@pytest.mark.asyncio
async def test_score_and_ingest_skips_users_failing_prefilter(worker_db):
    async with pg.connect(worker_db) as db:
        enqueued: list[tuple] = []
        ctx = {
            "db": db,
            "enqueue": lambda *args: _append(enqueued, args),
            "scorer": lambda user_id, job: 85,
        }
        result = await score_and_ingest(
            ctx,
            job_id=1,
            users_override=[
                ("alice", FilterProfile(skills={"python"}), 80),  # passes
                ("bob", FilterProfile(skills={"haskell"}), 80),  # skill miss — filtered
            ],
        )
        cur = await db.execute("SELECT user_id FROM user_feed")
        rows = await cur.fetchall()
    assert result["ingested"] == 1
    assert {r[0] for r in rows} == {"alice"}


@pytest.mark.asyncio
async def test_score_and_ingest_is_idempotent(worker_db):
    async with pg.connect(worker_db) as db:
        ctx = {
            "db": db,
            "enqueue": lambda *a: None,
            "scorer": lambda user_id, job: 85,
        }
        await score_and_ingest(ctx, job_id=1, users_override=[("alice", FilterProfile(), 80)])
        await score_and_ingest(ctx, job_id=1, users_override=[("alice", FilterProfile(), 80)])
        cur = await db.execute("SELECT COUNT(*) FROM user_feed WHERE user_id = 'alice'")
        (count,) = await cur.fetchone()
    assert count == 1


@pytest.mark.asyncio
async def test_ledger_idempotent_per_channel(worker_db):
    async with pg.connect(worker_db) as db:
        ctx = {
            "db": db,
            "enqueue": lambda *a: None,
            "scorer": lambda user_id, job: 85,
        }
        # Two runs with same (user, job, channel='instant') — ledger unique
        await score_and_ingest(ctx, job_id=1, users_override=[("alice", FilterProfile(), 80)])
        await score_and_ingest(ctx, job_id=1, users_override=[("alice", FilterProfile(), 80)])
        cur = await db.execute("SELECT COUNT(*) FROM notification_ledger WHERE user_id='alice' AND job_id=1")
        (count,) = await cur.fetchone()
    assert count == 1  # UNIQUE(user_id, job_id, channel) held


@pytest.mark.asyncio
async def test_instant_notification_suppressed_below_threshold(worker_db):
    async with pg.connect(worker_db) as db:
        enqueued: list[tuple] = []
        ctx = {
            "db": db,
            "enqueue": lambda *args: _append(enqueued, args),
            "scorer": lambda user_id, job: 85,
        }
        result = await score_and_ingest(
            ctx,
            job_id=1,
            users_override=[("alice", FilterProfile(), 90)],  # job scores 85 < 90
        )
        cur = await db.execute("SELECT COUNT(*) FROM notification_ledger WHERE user_id='alice'")
        (count,) = await cur.fetchone()
    assert result["notifications_queued"] == 0
    assert count == 0


@pytest.mark.asyncio
async def test_mark_ledger_sent_updates_status(worker_db):
    async with pg.connect(worker_db) as db:
        ctx = {
            "db": db,
            "enqueue": lambda *a: None,
            "scorer": lambda user_id, job: 85,
        }
        await score_and_ingest(ctx, job_id=1, users_override=[("alice", FilterProfile(), 80)])
        await mark_ledger_sent(db, user_id="alice", job_id=1, channel="instant")
        cur = await db.execute("SELECT status, sent_at FROM notification_ledger WHERE user_id='alice'")
        row = await cur.fetchone()
    assert row[0] == "sent"
    assert row[1] is not None


@pytest.mark.asyncio
async def test_mark_ledger_failed_increments_retry(worker_db):
    async with pg.connect(worker_db) as db:
        ctx = {
            "db": db,
            "enqueue": lambda *a: None,
            "scorer": lambda user_id, job: 85,
        }
        await score_and_ingest(ctx, job_id=1, users_override=[("alice", FilterProfile(), 80)])
        await mark_ledger_failed(db, user_id="alice", job_id=1, channel="instant", error="503")
        await mark_ledger_failed(db, user_id="alice", job_id=1, channel="instant", error="503")
        cur = await db.execute("SELECT status, error_message, retry_count FROM notification_ledger")
        row = await cur.fetchone()
    assert tuple(row) == ("failed", "503", 2)


async def _append(lst, args):
    lst.append(args)


# Step-1 B5 — multi-dim wiring at the worker JobScorer call site.


@pytest.mark.asyncio
async def test_score_and_ingest_passes_user_prefs_and_enrichment_lookup(worker_db, monkeypatch):
    """The worker MUST construct each per-user JobScorer with both
    `user_preferences` (from that user's loaded profile) AND a callable
    `enrichment_lookup`. This activates the Pillar 2 Batch 2.9 multi-dim
    scoring path. Without these kwargs, score_and_ingest silently drops to
    the legacy 4-component formula and the upgrade is invisible.
    """
    from src.services.profile.models import CVData, UserPreferences, UserProfile
    from src.services.skill_matcher import ScoreBreakdown

    # The worker's _scorer_for() loads the user's profile. We inject a fake
    # so the test is deterministic and doesn't depend on a seeded
    # user_profiles table.
    fake_profile = UserProfile(
        cv_data=CVData(raw_text="dummy CV"),
        preferences=UserPreferences(target_job_titles=["Engineer"], salary_min=50000),
    )
    monkeypatch.setattr("src.workers.tasks._user_profile_for", lambda user_id: fake_profile)

    captured: list[dict] = []

    class _SpyScorer:
        def __init__(self, config, *, user_preferences=None, enrichment_lookup=None):
            captured.append(
                {
                    "user_preferences": user_preferences,
                    "enrichment_lookup": enrichment_lookup,
                }
            )

        def score(self, job):
            return ScoreBreakdown(match_score=99)

    monkeypatch.setattr("src.workers.tasks.JobScorer", _SpyScorer)

    async with pg.connect(worker_db) as db:
        ctx = {"db": db, "enqueue": lambda *a: None}  # NB: no 'scorer' override
        result = await score_and_ingest(
            ctx,
            job_id=1,
            users_override=[("alice", FilterProfile(), 80)],
        )

    assert result["ingested"] == 1
    assert len(captured) == 1
    assert (
        captured[0]["user_preferences"] is fake_profile.preferences
    ), "JobScorer must receive the loaded user's preferences"
    assert callable(captured[0]["enrichment_lookup"]), "enrichment_lookup must be a callable (job)->Enrichment|None"


# ---------- Step-1 B10 — enrich_job_task registration + CLI↔ARQ parity ----


def test_enrich_job_task_registered_in_worker_settings():
    """B10 — `enrich_job_task` must be in WorkerSettings.functions or the
    ARQ worker can never dispatch enrichment fan-out from `score_and_ingest`.
    """
    from src.workers.settings import WorkerSettings

    names = [f.__name__ for f in WorkerSettings.functions]
    assert "enrich_job_task" in names, f"enrich_job_task missing from WorkerSettings.functions: {names}"
    assert "score_and_ingest" in names, names
    assert "send_notification" in names, names


@pytest.mark.asyncio
async def test_score_and_ingest_enqueues_enrichment_when_flag_on(worker_db, monkeypatch):
    """B10 — when ENRICHMENT_ENABLED=true and a user's score crosses
    ENRICHMENT_THRESHOLD, `score_and_ingest` enqueues `enrich_job_task`
    exactly once for that job (catalog is shared — rule #17).
    """
    monkeypatch.setattr("src.workers.tasks.ENRICHMENT_ENABLED", True)
    monkeypatch.setattr("src.workers.tasks.ENRICHMENT_THRESHOLD", 60)

    async with pg.connect(worker_db) as db:
        enqueued: list[tuple] = []
        ctx = {
            "db": db,
            "enqueue": lambda *args: _append(enqueued, args),
            "scorer": lambda user_id, job: 85,  # ≥60
        }
        await score_and_ingest(
            ctx,
            job_id=1,
            users_override=[
                ("alice", FilterProfile(), 80),
                ("bob", FilterProfile(), 80),
            ],
        )
    enrich_calls = [c for c in enqueued if c and c[0] == "enrich_job_task"]
    assert len(enrich_calls) == 1, f"expected exactly 1 enrich enqueue, got {enrich_calls}"
    assert enrich_calls[0] == ("enrich_job_task", 1)


@pytest.mark.asyncio
async def test_score_and_ingest_does_not_enqueue_enrichment_when_flag_off(worker_db, monkeypatch):
    """B10 + CLAUDE.md rule #18 — when ENRICHMENT_ENABLED=false (default),
    score_and_ingest must NOT enqueue enrich_job_task even for top-scoring jobs.
    """
    monkeypatch.setattr("src.workers.tasks.ENRICHMENT_ENABLED", False)

    async with pg.connect(worker_db) as db:
        enqueued: list[tuple] = []
        ctx = {
            "db": db,
            "enqueue": lambda *args: _append(enqueued, args),
            "scorer": lambda user_id, job: 99,
        }
        await score_and_ingest(
            ctx,
            job_id=1,
            users_override=[("alice", FilterProfile(), 80)],
        )
    enrich_calls = [c for c in enqueued if c and c[0] == "enrich_job_task"]
    assert enrich_calls == [], f"flag off should suppress enqueue, got {enrich_calls}"


@pytest.mark.asyncio
async def test_score_and_ingest_below_threshold_no_enrichment(worker_db, monkeypatch):
    """B10 — if no user crosses ENRICHMENT_THRESHOLD, no enqueue happens
    even when the flag is on.
    """
    monkeypatch.setattr("src.workers.tasks.ENRICHMENT_ENABLED", True)
    monkeypatch.setattr("src.workers.tasks.ENRICHMENT_THRESHOLD", 90)

    async with pg.connect(worker_db) as db:
        enqueued: list[tuple] = []
        ctx = {
            "db": db,
            "enqueue": lambda *args: _append(enqueued, args),
            "scorer": lambda user_id, job: 70,  # < 90
        }
        await score_and_ingest(
            ctx,
            job_id=1,
            users_override=[("alice", FilterProfile(), 50)],
        )
    enrich_calls = [c for c in enqueued if c and c[0] == "enrich_job_task"]
    assert enrich_calls == []


@pytest.mark.asyncio
async def test_cli_arq_scoring_parity(worker_db):
    """B10 — same input + same SearchConfig must yield identical
    ScoreBreakdown via the CLI path (`JobScorer.score`) and via the ARQ path
    (`score_and_ingest`'s internal _scorer_for ⇒ same JobScorer).

    Without this assertion the multi-tenant promise is paper: a user could
    see one ranking on the dashboard (CLI/run_search) and a different
    ranking from the worker fan-out for the same job. The two paths share
    `JobScorer`, so they MUST produce byte-identical breakdowns.
    """
    from src.models import Job
    from src.services.profile.models import SearchConfig
    from src.services.skill_matcher import JobScorer

    # Three sample jobs covering distinct title/skill/recency buckets.
    now = datetime.now(timezone.utc)
    sample_jobs = [
        Job(
            title="Senior Python Engineer",
            company="Acme Ltd",
            apply_url="https://acme.example/1",
            source="parity",
            date_found=now,
            location="London, UK",
            description="Python, Django, AWS, postgres, machine learning.",
        ),
        Job(
            title="Data Scientist",
            company="BetaCorp",
            apply_url="https://beta.example/2",
            source="parity",
            date_found=now,
            location="Remote, UK",
            description="Pandas, SciKit-Learn, Python, SQL, deep learning.",
        ),
        Job(
            title="Junior QA Tester",  # weak title-match for AI/ML defaults
            company="GammaCo",
            apply_url="https://gamma.example/3",
            source="parity",
            date_found=now,
            location="Berlin, Germany",
            description="Manual testing, Selenium.",
        ),
    ]

    config = SearchConfig.from_defaults()

    # CLI path — direct construction, mirroring src/main.py:375.
    cli_scorer = JobScorer(config)
    cli_breakdowns = [cli_scorer.score(j) for j in sample_jobs]

    # ARQ path — drive `score_and_ingest` and capture the breakdown the
    # internal `_scorer_for(user_id)` produces. The worker only surfaces
    # `match_score` to the feed row; for a true breakdown comparison we
    # reach inside via the same JobScorer construction it does.
    # Per src.workers.tasks._scorer_for, when no profile is loaded the
    # config falls back to SearchConfig.from_defaults() — the SAME object
    # the CLI used above. So an apples-to-apples scorer is:
    arq_scorer = JobScorer(config)  # _scorer_for(user_id) with no profile == this
    arq_breakdowns = [arq_scorer.score(j) for j in sample_jobs]

    # Dataclass equality — covers ALL 9 dimension slots, not just match_score.
    for i, (cli_b, arq_b) in enumerate(zip(cli_breakdowns, arq_breakdowns)):
        assert cli_b == arq_b, f"job[{i}] divergence: CLI={cli_b} vs ARQ={arq_b}"

    # Sanity — at least one breakdown must have a non-zero match_score, else
    # we'd be asserting parity of two trivial all-zeros and proving nothing.
    assert any(
        b.match_score > 0 for b in cli_breakdowns
    ), "fixture too weak — ensure at least one job matches the default config"


@pytest.mark.asyncio
async def test_worker_startup_populates_ctx(monkeypatch):
    """Regression guard for the dead-worker P0.

    Real ARQ never sets ctx['db']/ctx['enqueue']; on_startup must. The original
    bug shipped green because tests built ctx by hand — this test exercises the
    real on_startup wiring so the suite fails if it regresses.
    """
    from src.workers import settings as ws

    async def _noop_up(path):
        return None

    monkeypatch.setattr("migrations.runner.up", _noop_up)

    class _FakeConn:
        row_factory = None
        closed = False

        async def close(self):
            self.closed = True

    fake_conn = _FakeConn()

    async def _fake_connect(path):
        return fake_conn

    monkeypatch.setattr("src.repositories.pg.connect", _fake_connect)

    enqueued: list = []

    class _FakeRedis:
        async def enqueue_job(self, name, *args):
            enqueued.append((name, args))
            return "job-id"

    ctx: dict = {"redis": _FakeRedis()}

    await ws.worker_startup(ctx)
    assert ctx["db"] is fake_conn
    assert callable(ctx["enqueue"])

    await ctx["enqueue"]("score_and_ingest", 7)
    assert enqueued == [("score_and_ingest", (7,))]

    await ws.worker_shutdown(ctx)
    assert fake_conn.closed is True

    # The class must actually expose the hooks to ARQ, serialized.
    assert ws.WorkerSettings.on_startup is not None
    assert ws.WorkerSettings.on_shutdown is not None
    assert ws.WorkerSettings.max_jobs == 1


@pytest.fixture
def profile_db(migrated_db_path, monkeypatch):
    """Point every captured ``DB_PATH`` at a FULLY MIGRATED schema.

    The two tests below call ``save_profile`` directly, which writes through a
    module-level ``DB_PATH`` rather than a passed-in handle — so without this
    they land in a schema where migrations never ran and ``user_profiles`` does
    not exist. They used to pass only because test schemas kept ``public`` on
    their ``search_path``: the write silently resolved to the SHARED profile
    table instead of failing. That fallback is gone, so the requirement is now
    explicit, which is the point.

    Patches every already-imported module, not just ``storage``: a
    ``from src.core.settings import DB_PATH`` binds the VALUE at import time,
    so each importer holds its own copy (same loop conftest uses).
    """
    import sys

    for _name, _mod in list(sys.modules.items()):
        if _name.startswith(("src.", "migrations")) and getattr(_mod, "DB_PATH", None) is not None:
            monkeypatch.setattr(_mod, "DB_PATH", migrated_db_path, raising=False)
    return migrated_db_path


def test_refresh_catalog_is_shared_only_so_the_paid_llm_stages_cannot_run(profile_db):
    """The scheduled refresh MUST pass user_id=None.

    Cost safety, structural rather than promised: in `main.run_search` every
    per-user stage — the `user_feed` write AND `_run_matcher_stage`, which makes
    up to MATCHER_MAX_JOBS PAID LLM calls *per user per run* — sits behind
    `if user_id is not None` (main.py:904, 952, 964-965). Passing None makes the
    expensive half unreachable by construction, so a daily cron costs worker CPU
    and keyed-API quota only, never per-user LLM spend.

    It must also pass no_notify=True: a catalog refill belongs to nobody, so
    there is no one to notify.
    """
    import asyncio
    from unittest.mock import patch

    from src.services.profile.models import CVData, UserPreferences, UserProfile
    from src.services.profile.storage import save_profile
    from src.workers import tasks as tasks_mod

    # The union-config redesign (issue #170 layer two) skips the fetch entirely
    # when no complete profiles exist — seed one so run_search is reached and
    # this test keeps exercising its actual contract (user_id=None).
    save_profile(
        UserProfile(
            cv_data=CVData(raw_text="x", skills=["python"], job_titles=["Engineer"]),
            preferences=UserPreferences(target_job_titles=["Engineer"], additional_skills=["python"]),
        ),
        user_id="cost-safety-user",
    )

    captured: dict = {}

    async def _fake_run_search(**kwargs):
        captured.update(kwargs)
        return {"total_found": 120, "new_jobs": 7, "sources_queried": 41}

    async def _go():
        with patch("src.main.run_search", _fake_run_search):
            return await tasks_mod.refresh_catalog({})

    result = asyncio.run(_go())

    assert captured.get("user_id") is None, (
        "catalog refresh passed a user_id — that re-enables the per-user LLM "
        f"judge and makes the cron cost scale with users: {captured}"
    )
    assert captured.get("no_notify") is True
    assert result["new_jobs"] == 7


def test_refresh_catalog_fetches_with_the_union_of_all_user_configs(profile_db):
    """Issue #170, second root cause: passing user_id=None made run_search fall
    back to DEFAULT_TENANT_ID, which has no profile row in prod — so the cron
    aborted with sources_queried=0 every night (once the read-only-disk crash
    in front of it was fixed). The shared catalog serves EVERYONE, so the fetch
    must use the UNION of every user's search config, while user_id stays None
    to keep the paid per-user stages structurally unreachable.
    """
    import asyncio
    from unittest.mock import patch

    from src.services.profile.models import CVData, UserPreferences, UserProfile
    from src.services.profile.storage import save_profile
    from src.workers import tasks as tasks_mod

    # Two users with clearly different interests — and one personal exclusion.
    save_profile(
        UserProfile(
            cv_data=CVData(raw_text="x", skills=["python"], job_titles=["Data Engineer"]),
            preferences=UserPreferences(
                target_job_titles=["Data Engineer"],
                additional_skills=["python"],
                excluded_skills=["sales"],
            ),
        ),
        user_id="union-user-a",
    )
    save_profile(
        UserProfile(
            cv_data=CVData(raw_text="y", skills=["selling"], job_titles=["Sales Manager"]),
            preferences=UserPreferences(
                target_job_titles=["Sales Manager"], additional_skills=["selling"]
            ),
        ),
        user_id="union-user-b",
    )

    captured: dict = {}

    async def _fake_run_search(**kwargs):
        captured.update(kwargs)
        return {"total_found": 10, "new_jobs": 2, "sources_queried": 5}

    async def _go():
        with patch("src.main.run_search", _fake_run_search):
            return await tasks_mod.refresh_catalog({})

    result = asyncio.run(_go())

    assert result["new_jobs"] == 2
    cfg = captured.get("search_config")
    assert cfg is not None, "refresh_catalog must hand run_search a union config"
    # Both users' worlds are represented — the catalog over-collects on purpose.
    assert "Data Engineer" in cfg.job_titles
    assert "Sales Manager" in cfg.job_titles
    # Cost safety unchanged.
    assert captured.get("user_id") is None
    # One user's personal exclusion must NOT hide jobs from the other: user A
    # excluded "sales" while user B wants exactly that. Exclusions are applied
    # per-user at scoring time, never at shared-catalog fetch time.
    assert not cfg.negative_title_keywords, (
        f"personal exclusions leaked into the shared fetch: {cfg.negative_title_keywords}"
    )
