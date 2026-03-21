"""Tests for user actions (Liked / Applied / Not Interested)."""

import pytest
import pytest_asyncio
import aiosqlite

from src.storage.user_actions import UserActionsDB, ActionType


@pytest_asyncio.fixture
async def actions_db(tmp_path):
    db_path = str(tmp_path / "test.db")
    conn = await aiosqlite.connect(db_path)
    conn.row_factory = aiosqlite.Row
    # Create the jobs table first (foreign key target)
    await conn.executescript("""
        CREATE TABLE IF NOT EXISTS jobs (
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
            UNIQUE(normalized_company, normalized_title)
        );
    """)
    await conn.commit()

    # Insert a few test jobs
    for i in range(1, 6):
        await conn.execute(
            "INSERT INTO jobs (title, company, apply_url, source, date_found, normalized_company, normalized_title, first_seen) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (f"Job {i}", f"Company {i}", f"https://example.com/{i}", "test", "2026-01-01T00:00:00", f"company {i}", f"job {i}", "2026-01-01T00:00:00"),
        )
    await conn.commit()

    db = UserActionsDB(conn)
    await db.init_table()
    yield db
    await conn.close()


@pytest.mark.asyncio
async def test_init_table(actions_db):
    """Table creation succeeds and is idempotent."""
    await actions_db.init_table()  # second call should not fail


@pytest.mark.asyncio
async def test_set_action(actions_db):
    await actions_db.set_action(1, ActionType.liked)
    result = await actions_db.get_action(1)
    assert result is not None
    assert result["action"] == "liked"
    assert result["job_id"] == 1


@pytest.mark.asyncio
async def test_set_action_with_notes(actions_db):
    await actions_db.set_action(1, ActionType.applied, notes="Great company!")
    result = await actions_db.get_action(1)
    assert result["notes"] == "Great company!"


@pytest.mark.asyncio
async def test_set_action_replaces_previous(actions_db):
    """UNIQUE(job_id) means setting a new action replaces the old one."""
    await actions_db.set_action(1, ActionType.liked)
    await actions_db.set_action(1, ActionType.applied)
    result = await actions_db.get_action(1)
    assert result["action"] == "applied"


@pytest.mark.asyncio
async def test_toggle_action_set(actions_db):
    """Toggle sets action when none exists."""
    was_set = await actions_db.toggle_action(1, ActionType.liked)
    assert was_set is True
    result = await actions_db.get_action(1)
    assert result["action"] == "liked"


@pytest.mark.asyncio
async def test_toggle_action_remove(actions_db):
    """Toggle removes action when same action exists."""
    await actions_db.set_action(1, ActionType.liked)
    was_set = await actions_db.toggle_action(1, ActionType.liked)
    assert was_set is False
    result = await actions_db.get_action(1)
    assert result is None


@pytest.mark.asyncio
async def test_toggle_action_replace(actions_db):
    """Toggle replaces action when a different action exists."""
    await actions_db.set_action(1, ActionType.liked)
    was_set = await actions_db.toggle_action(1, ActionType.applied)
    assert was_set is True
    result = await actions_db.get_action(1)
    assert result["action"] == "applied"


@pytest.mark.asyncio
async def test_remove_action(actions_db):
    await actions_db.set_action(1, ActionType.liked)
    await actions_db.remove_action(1)
    result = await actions_db.get_action(1)
    assert result is None


@pytest.mark.asyncio
async def test_get_action_none(actions_db):
    result = await actions_db.get_action(999)
    assert result is None


@pytest.mark.asyncio
async def test_get_jobs_by_action(actions_db):
    await actions_db.set_action(1, ActionType.liked)
    await actions_db.set_action(2, ActionType.liked)
    await actions_db.set_action(3, ActionType.applied)

    liked = await actions_db.get_jobs_by_action(ActionType.liked)
    assert len(liked) == 2

    applied = await actions_db.get_jobs_by_action(ActionType.applied)
    assert len(applied) == 1


@pytest.mark.asyncio
async def test_get_all_actions(actions_db):
    await actions_db.set_action(1, ActionType.liked)
    await actions_db.set_action(2, ActionType.applied)
    await actions_db.set_action(3, ActionType.not_interested)

    all_actions = await actions_db.get_all_actions()
    assert len(all_actions) == 3


@pytest.mark.asyncio
async def test_count_by_action(actions_db):
    await actions_db.set_action(1, ActionType.liked)
    await actions_db.set_action(2, ActionType.liked)
    await actions_db.set_action(3, ActionType.applied)

    counts = await actions_db.count_by_action()
    assert counts.get("liked") == 2
    assert counts.get("applied") == 1
    assert counts.get("not_interested") is None


@pytest.mark.asyncio
async def test_unique_per_job(actions_db):
    """Only one action per job at a time."""
    await actions_db.set_action(1, ActionType.liked)
    await actions_db.set_action(1, ActionType.not_interested)
    all_actions = await actions_db.get_all_actions()
    job1_actions = [a for a in all_actions if a["job_id"] == 1]
    assert len(job1_actions) == 1
    assert job1_actions[0]["action"] == "not_interested"


@pytest.mark.asyncio
async def test_timestamp_is_set(actions_db):
    await actions_db.set_action(1, ActionType.liked)
    result = await actions_db.get_action(1)
    assert result["timestamp"]
    assert "T" in result["timestamp"]  # ISO format
