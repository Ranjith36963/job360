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
    # Slice 5 (#483): `run_log` is not in the boot DDL any more, and migration
    # 0038 drops it. Its absence is the assertion — a re-added CREATE would
    # resurrect a table nothing reads.
    assert "run_log" not in tables




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






def test_migrate_no_op_on_fresh_db(db):
    """Migration on a fresh database should be a no-op (all columns already exist)."""
    # _migrate() is called during init_db(), so just verify it didn't break anything
    tables = asyncio.run(db.get_tables())
    assert "jobs" in tables
    assert "applications" in tables


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
    job_id = asyncio.run(db.get_job_id_by_key(job.normalized_key()))
    row = asyncio.run(db.get_job_by_id(job_id))
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
    job_id = asyncio.run(db.get_job_id_by_key(job.normalized_key()))
    row = asyncio.run(db.get_job_by_id(job_id))
    assert row["first_seen_at"] is not None
    assert row["last_seen_at"] is not None
    # first_seen_at should be >= `before` (i.e. set during the insert, not 2020)
    got = datetime.fromisoformat(row["first_seen_at"])
    assert got >= before - timedelta(seconds=5), f"first_seen_at not defaulted to now: got {row['first_seen_at']}"








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


@pytest.mark.asyncio
async def test_refetch_backfills_empty_description():
    """Pillar-2 sim finding (2026-08-06): INSERT OR IGNORE kept old EMPTY
    descriptions forever, so the 0%-desc source fixes only helped brand-new
    postings. A re-fetch carrying text must fill an empty stored description —
    and never overwrite existing text (empty -> non-empty only)."""
    from src.models import Job as _Job

    db = JobDatabase(":memory:")
    await db.init_db()
    try:
        j1 = _Job(title="ML Engineer", company="Acme", apply_url="https://x/1",
                  source="greenhouse", date_found="2026-08-06T00:00:00+00:00",
                  location="London, UK", description="")
        assert await db.insert_job(j1) is True

        j2 = _Job(title="ML Engineer", company="Acme", apply_url="https://x/1",
                  source="greenhouse", date_found="2026-08-06T01:00:00+00:00",
                  location="London, UK", description="Full posting text now available.")
        assert await db.insert_job(j2) is False  # duplicate, not a new insert

        cur = await db._db.execute("SELECT description FROM jobs")
        desc = (await cur.fetchone())[0]
        assert desc == "Full posting text now available.", "backfill did not land"

        # Value-presence guard (rule #21): a later EMPTY re-fetch must not wipe it.
        j3 = _Job(title="ML Engineer", company="Acme", apply_url="https://x/1",
                  source="greenhouse", date_found="2026-08-06T02:00:00+00:00",
                  location="London, UK", description="")
        await db.insert_job(j3)
        cur = await db._db.execute("SELECT description FROM jobs")
        assert (await cur.fetchone())[0] == "Full posting text now available."
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_refetch_upgrades_a_teaser_to_the_full_advert():
    """A TEASER IS NOT EMPTY — the whole reason the source-side text recovery
    needed an ingest change too.

    reed's list endpoint ships a ~453-char teaser and its detail endpoint the
    full ~4,700-char ad. The old guard only filled a description that was
    EMPTY, so on the 10,579 jobs already stored the recovered full text could
    never land: the row kept its teaser until it aged out through the 30-day
    purge. Now a MATERIALLY longer description replaces a shorter one, and the
    three guards below stop that from becoming churn.
    """
    from src.models import Job as _Job

    def _job(desc: str, when: str) -> _Job:
        return _Job(title="AI Architect", company="Reed Client", apply_url="https://x/1",
                    source="reed", date_found=when, location="London, UK", description=desc)

    teaser = "A" * 453
    full_ad = "B" * 4700

    db = JobDatabase(":memory:")
    await db.init_db()
    try:
        assert await db.insert_job(_job(teaser, "2026-08-16T00:00:00+00:00")) is True

        # 1. The full advert REPLACES the teaser.
        assert await db.insert_job(_job(full_ad, "2026-08-16T01:00:00+00:00")) is False
        cur = await db._db.execute("SELECT description FROM jobs")
        assert (await cur.fetchone())[0] == full_ad, "the full advert did not replace the teaser"

        # 2. It can never go BACKWARDS. Next run the detail-fetch budget is
        #    spent and only the teaser comes back — the stored text must hold.
        await db.insert_job(_job(teaser, "2026-08-16T02:00:00+00:00"))
        cur = await db._db.execute("SELECT description FROM jobs")
        assert (await cur.fetchone())[0] == full_ad, "a shorter re-fetch overwrote the full advert"

        # 3. NO THRASH. A version that is merely a bit longer (a footer, a
        #    cookie banner, a re-rendered 'Apply now') does not rewrite the row:
        #    it clears neither the +200-char floor nor the 1.2x ratio.
        assert await db.insert_job(_job(full_ad + "C" * 150, "2026-08-16T03:00:00+00:00")) is False
        cur = await db._db.execute("SELECT description FROM jobs")
        assert (await cur.fetchone())[0] == full_ad, "a trivial length gain rewrote the row"

        # 4. And an identical re-fetch is a no-op for the same reason.
        await db.insert_job(_job(full_ad, "2026-08-16T04:00:00+00:00"))
        cur = await db._db.execute("SELECT description FROM jobs")
        assert (await cur.fetchone())[0] == full_ad
    finally:
        await db.close()
