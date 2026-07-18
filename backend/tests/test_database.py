import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from src.models import Job
from src.repositories import pg
from src.repositories.database import JobDatabase


@pytest.fixture
def db():
    database = JobDatabase(":memory:")
    asyncio.run(database.init_db())
    yield database
    asyncio.run(database.close())


def _make_job(**overrides):
    defaults = dict(
        title="AI Engineer",
        company="DeepMind",
        apply_url="https://example.com/job",
        source="reed",
        date_found=datetime.now(timezone.utc).isoformat(),
        location="London",
        description="AI role",
    )
    defaults.update(overrides)
    return Job(**defaults)


@pytest.mark.fast
def test_init_creates_tables(db):
    tables = asyncio.run(db.get_tables())
    assert "jobs" in tables
    assert "run_log" in tables


def test_insert_and_check_seen(db):
    job = _make_job()
    key = job.normalized_key()
    assert asyncio.run(db.is_job_seen(key)) is False
    asyncio.run(db.insert_job(job))
    assert asyncio.run(db.is_job_seen(key)) is True


def test_duplicate_insert_ignored(db):
    job = _make_job()
    asyncio.run(db.insert_job(job))
    asyncio.run(db.insert_job(job))  # should not raise
    count = asyncio.run(db.count_jobs())
    assert count == 1


def test_insert_different_jobs(db):
    j1 = _make_job(title="AI Engineer", company="DeepMind")
    j2 = _make_job(title="ML Engineer", company="Revolut")
    asyncio.run(db.insert_job(j1))
    asyncio.run(db.insert_job(j2))
    count = asyncio.run(db.count_jobs())
    assert count == 2


def test_log_run(db):
    stats = {
        "total_found": 50,
        "new_jobs": 10,
        "per_source": {"reed": 20, "adzuna": 30},
    }
    asyncio.run(db.log_run(stats))
    runs = asyncio.run(db.get_run_logs())
    assert len(runs) == 1
    assert runs[0]["total_found"] == 50


def test_get_new_jobs_since(db):
    j1 = _make_job(title="AI Engineer", company="DeepMind")
    asyncio.run(db.insert_job(j1))
    jobs = asyncio.run(db.get_new_jobs_since(hours=1))
    assert len(jobs) == 1


def test_migrate_no_op_on_fresh_db(db):
    """Migration on a fresh database should be a no-op (all columns already exist)."""
    # _migrate() is called during init_db(), so just verify it didn't break anything
    tables = asyncio.run(db.get_tables())
    assert "jobs" in tables
    assert "run_log" in tables


def test_insert_job_preserves_caller_first_seen_at(db):
    """Step-1 B2: caller-supplied first_seen_at must NOT be overwritten with now.

    Bug-reproduction test: before the fix, insert_job silently replaced any
    caller value with datetime('now'). Post-fix, the explicit 2020 timestamp
    should round-trip unchanged.
    """
    job = _make_job(
        title="Historic Role",
        company="TimeMachine",
        first_seen_at="2020-01-01T00:00:00+00:00",
        last_seen_at="2020-06-01T00:00:00+00:00",
    )
    asyncio.run(db.insert_job(job))
    rows = asyncio.run(db.get_recent_jobs(days=9999))
    # Find our specific job (other tests may share the db fixture — but :memory: is per-test)
    row = next(r for r in rows if r["title"] == "Historic Role")
    assert row["first_seen_at"].startswith("2020-01-01"), f"first_seen_at was overwritten: got {row['first_seen_at']}"
    assert row["last_seen_at"].startswith("2020-06-01"), f"last_seen_at was overwritten: got {row['last_seen_at']}"


def test_insert_job_defaults_first_seen_at_to_now_when_none(db):
    """Step-1 B2: when caller leaves first_seen_at/last_seen_at as None, insert_job
    falls back to datetime('now'). Preserves pre-fix behaviour for default callers.
    """
    job = _make_job(title="Fresh Role", company="NowCo")
    # Sanity: the Job dataclass defaults these to None
    assert job.first_seen_at is None
    assert job.last_seen_at is None
    before = datetime.now(timezone.utc)
    asyncio.run(db.insert_job(job))
    rows = asyncio.run(db.get_recent_jobs(days=9999))
    row = next(r for r in rows if r["title"] == "Fresh Role")
    assert row["first_seen_at"] is not None
    assert row["last_seen_at"] is not None
    # first_seen_at should be >= `before` (i.e. set during the insert, not 2020)
    got = datetime.fromisoformat(row["first_seen_at"])
    assert got >= before - timedelta(seconds=5), f"first_seen_at not defaulted to now: got {row['first_seen_at']}"


def test_dim_columns_round_trip(db):
    """Step-1.5 S1.1-D — every per-dim score field on the Job dataclass must
    survive insert_job → get_recent_jobs unchanged. This is the value-presence
    test the Step-1 reviewer never wrote (CLAUDE.md rule #21 will codify it).
    """
    job = _make_job(
        title="Dim Carrier",
        company="ScoreCo",
        match_score=85,
        role=35,
        skill=30,
        location_score=8,
        recency=6,
        seniority_score=4,
        semantic=2,
    )
    asyncio.run(db.insert_job(job))
    rows = asyncio.run(db.get_recent_jobs(days=9999))
    row = next(r for r in rows if r["title"] == "Dim Carrier")
    assert row["role"] == 35
    assert row["skill"] == 30
    assert row["location_score"] == 8
    assert row["recency"] == 6
    assert row["seniority_score"] == 4
    assert row["semantic"] == 2
    # Unset dims must default to 0, not NULL — JobResponse declares int.
    assert row["experience"] == 0
    assert row["credentials"] == 0
    assert row["penalty"] == 0


def test_get_last_source_counts_empty(db):
    """get_last_source_counts should return empty dict when no runs exist."""
    result = asyncio.run(db.get_last_source_counts(5))
    assert result == {}


def test_get_last_source_counts_with_data(db):
    """get_last_source_counts should return per-source history from run_log."""
    stats1 = {"total_found": 10, "new_jobs": 5, "per_source": {"reed": 5, "adzuna": 3}}
    stats2 = {"total_found": 8, "new_jobs": 2, "per_source": {"reed": 0, "adzuna": 4}}
    asyncio.run(db.log_run(stats1))
    asyncio.run(db.log_run(stats2))
    result = asyncio.run(db.get_last_source_counts(5))
    # Most recent run first
    assert "reed" in result
    assert "adzuna" in result
    assert 0 in result["reed"]
    assert 5 in result["reed"]


async def _create_user_feed_table(db: JobDatabase) -> None:
    """Create user_feed + job_enrichment with all columns needed for
    get_user_feed_jobs (incl. migration 0017 llm columns).

    The :memory: db fixture only runs init_db() which does not include
    the external SQL migration files.  user_feed comes from migrations
    0003 + 0017; job_enrichment from 0008.  get_user_feed_jobs does a
    LEFT JOIN to job_enrichment, which raises OperationalError (caught
    silently to []) if the table is missing, so both are created here.
    """
    await db._conn.executescript("""
        CREATE TABLE IF NOT EXISTS user_feed (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            job_id INTEGER NOT NULL,
            score INTEGER NOT NULL CHECK (score BETWEEN 0 AND 100),
            bucket TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            notified_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            llm_fit_score INTEGER,
            llm_verdict TEXT,
            llm_reason TEXT,
            llm_matched_at TEXT,
            UNIQUE(user_id, job_id)
        );
        CREATE TABLE IF NOT EXISTS job_enrichment (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL UNIQUE,
            title_canonical TEXT,
            category TEXT,
            employment_type TEXT,
            workplace_type TEXT,
            salary TEXT,
            required_skills TEXT,
            preferred_skills TEXT,
            experience_min_years INTEGER,
            experience_level TEXT,
            visa_sponsorship TEXT,
            seniority TEXT
        );
    """)
    await db._conn.commit()


@pytest.mark.asyncio
async def test_feed_jobs_surface_and_rank_by_llm_verdict(db):
    """COALESCE(llm_fit_score, score) ranking: a judged job with a LOW keyword
    score but HIGH LLM fit outranks an unjudged higher-keyword job, and the
    llm_* columns round-trip with real values (CLAUDE.md rule #21).
    """
    await _create_user_feed_table(db)

    uid = "test-user-llm"
    # Insert two catalog jobs
    job_a = _make_job(title="AI Engineer A", company="CompanyA", match_score=80)
    job_b = _make_job(title="ML Engineer B", company="CompanyB", match_score=40)
    inserted_a = await db.insert_job(job_a)
    inserted_b = await db.insert_job(job_b)
    assert inserted_a and inserted_b, "Both jobs must insert cleanly"

    # Retrieve their IDs
    cur = await db._conn.execute("SELECT id FROM jobs WHERE normalized_title = ?", ("ai engineer a",))
    row = await cur.fetchone()
    job_a_id = row[0]

    cur = await db._conn.execute("SELECT id FROM jobs WHERE normalized_title = ?", ("ml engineer b",))
    row = await cur.fetchone()
    job_b_id = row[0]

    # Insert user_feed rows: A has score=80 (unjudged), B has score=40
    now = datetime.now(timezone.utc).isoformat()
    await db._conn.execute(
        "INSERT INTO user_feed(user_id, job_id, score, bucket, status, created_at, updated_at) "
        "VALUES (?, ?, ?, 'top', 'active', ?, ?)",
        (uid, job_a_id, 80, now, now),
    )
    await db._conn.execute(
        "INSERT INTO user_feed(user_id, job_id, score, bucket, status, created_at, updated_at) "
        "VALUES (?, ?, ?, 'top', 'active', ?, ?)",
        (uid, job_b_id, 40, now, now),
    )
    await db._conn.commit()

    # Set B's LLM verdict to 95 — should now outrank A's keyword score of 80
    await db._conn.execute(
        "UPDATE user_feed SET llm_fit_score=95, llm_verdict='strong fit', "
        "llm_reason='domain match', llm_matched_at=datetime('now') "
        "WHERE user_id=? AND job_id=?",
        (uid, job_b_id),
    )
    await db._conn.commit()

    rows = await db.get_user_feed_jobs(uid, days=9999, min_score=0)

    assert len(rows) == 2, f"Expected 2 feed rows, got {len(rows)}"
    assert rows[0]["id"] == job_b_id, (
        f"Job B (llm_fit_score=95) should outrank Job A (score=80), "
        f"but got id={rows[0]['id']} first"
    )
    # Value-presence checks (rule #21): real values, not schema defaults
    assert rows[0]["llm_fit_score"] == 95, "llm_fit_score must round-trip"
    assert rows[0]["llm_verdict"] == "strong fit", "llm_verdict must round-trip"
    assert rows[0]["llm_reason"] == "domain match", "llm_reason must round-trip"
    # Unjudged row -> NULL, not 0 (rule: schema default is NULL not 0)
    assert rows[1]["llm_fit_score"] is None, (
        f"Unjudged job A must have llm_fit_score=NULL, got {rows[1]['llm_fit_score']}"
    )


# ── Channels & Notifications overhaul — Task 1: single-rule schema ──────────


def test_notification_rules_single_per_user_schema(db):
    """After migration 0020 the table is one-row-per-user with the new columns."""

    async def _cols():
        cur = await db._conn.execute("PRAGMA table_info(notification_rules)")
        return {r[1] for r in await cur.fetchall()}

    cols = asyncio.run(_cols())
    assert "interval_hours" in cols
    assert "daily_send_time" in cols
    assert "last_sent_at" in cols
    assert "channel" not in cols          # per-channel column removed
    assert "digest_send_time" not in cols  # renamed to daily_send_time


def test_notification_rules_unique_user(db):
    """A second insert for the same user must conflict (UNIQUE(user_id))."""

    async def _insert_first():
        await db._conn.execute(
            "INSERT INTO notification_rules(user_id, notify_mode) VALUES('u1','instant')"
        )
        await db._conn.commit()

    async def _insert_duplicate():
        await db._conn.execute(
            "INSERT INTO notification_rules(user_id, notify_mode) VALUES('u1','daily')"
        )
        await db._conn.commit()

    asyncio.run(_insert_first())

    with pytest.raises(pg.IntegrityError):
        asyncio.run(_insert_duplicate())
