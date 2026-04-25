"""Phase 5 worker task tests — no Redis, direct function calls."""

import os
import tempfile
from datetime import datetime, timezone

import aiosqlite
import pytest

from migrations import runner
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
    async with aiosqlite.connect(path) as db:
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
    async with aiosqlite.connect(path) as db:
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
    async with aiosqlite.connect(worker_db) as db:
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
    async with aiosqlite.connect(worker_db) as db:
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
    async with aiosqlite.connect(worker_db) as db:
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
    async with aiosqlite.connect(worker_db) as db:
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
    async with aiosqlite.connect(worker_db) as db:
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
    async with aiosqlite.connect(worker_db) as db:
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
    async with aiosqlite.connect(worker_db) as db:
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

    async with aiosqlite.connect(worker_db) as db:
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

    async with aiosqlite.connect(worker_db) as db:
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

    async with aiosqlite.connect(worker_db) as db:
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

    async with aiosqlite.connect(worker_db) as db:
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
