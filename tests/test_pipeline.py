"""Tests for the application tracking pipeline."""

import pytest
import pytest_asyncio
import aiosqlite
from datetime import datetime, timezone, timedelta

from src.pipeline.tracker import ApplicationTracker, PipelineStage, STAGE_ORDER, TERMINAL_STAGES
from src.pipeline.reminders import compute_next_reminder, get_pending_reminders, format_reminder_message
from src.storage.user_actions import UserActionsDB, ActionType


@pytest_asyncio.fixture
async def tracker_db(tmp_path):
    db_path = str(tmp_path / "test.db")
    conn = await aiosqlite.connect(db_path)
    conn.row_factory = aiosqlite.Row
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
        CREATE TABLE IF NOT EXISTS user_actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL,
            action TEXT NOT NULL CHECK(action IN ('liked', 'applied', 'not_interested')),
            timestamp TEXT NOT NULL,
            notes TEXT DEFAULT '',
            UNIQUE(job_id),
            FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
        );
    """)
    await conn.commit()

    for i in range(1, 6):
        await conn.execute(
            "INSERT INTO jobs (title, company, apply_url, source, date_found, normalized_company, normalized_title, first_seen) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (f"Job {i}", f"Company {i}", f"https://example.com/{i}", "test", "2026-01-01T00:00:00", f"company {i}", f"job {i}", "2026-01-01T00:00:00"),
        )
    await conn.commit()

    tracker = ApplicationTracker(conn)
    await tracker.init_table()
    yield tracker, conn
    await conn.close()


@pytest.mark.asyncio
async def test_init_table(tracker_db):
    tracker, _ = tracker_db
    await tracker.init_table()  # idempotent


@pytest.mark.asyncio
async def test_create_application(tracker_db):
    tracker, _ = tracker_db
    app = await tracker.create_application(1)
    assert app is not None
    assert app["job_id"] == 1
    assert app["status"] == "applied"
    assert app["next_reminder"] is not None


@pytest.mark.asyncio
async def test_create_application_with_notes(tracker_db):
    tracker, _ = tracker_db
    app = await tracker.create_application(1, notes="Exciting role")
    assert app["notes"] == "Exciting role"


@pytest.mark.asyncio
async def test_advance_stage(tracker_db):
    tracker, _ = tracker_db
    await tracker.create_application(1)
    app = await tracker.advance_stage(1, PipelineStage.outreach_week1)
    assert app["status"] == "outreach_week1"
    assert app["next_reminder"] is not None


@pytest.mark.asyncio
async def test_advance_to_interview(tracker_db):
    tracker, _ = tracker_db
    await tracker.create_application(1)
    app = await tracker.advance_stage(1, PipelineStage.interview)
    assert app["status"] == "interview"
    assert app["next_reminder"] is None  # no reminder for interview


@pytest.mark.asyncio
async def test_advance_to_terminal_stage(tracker_db):
    tracker, _ = tracker_db
    await tracker.create_application(1)
    app = await tracker.advance_stage(1, PipelineStage.offer)
    assert app["status"] == "offer"
    assert app["next_reminder"] is None


@pytest.mark.asyncio
async def test_get_application(tracker_db):
    tracker, _ = tracker_db
    await tracker.create_application(1)
    app = await tracker.get_application(1)
    assert app["job_id"] == 1


@pytest.mark.asyncio
async def test_get_application_not_found(tracker_db):
    tracker, _ = tracker_db
    assert await tracker.get_application(999) is None


@pytest.mark.asyncio
async def test_get_all_applications(tracker_db):
    tracker, _ = tracker_db
    await tracker.create_application(1)
    await tracker.create_application(2)
    apps = await tracker.get_all_applications()
    assert len(apps) == 2


@pytest.mark.asyncio
async def test_get_applications_by_stage(tracker_db):
    tracker, _ = tracker_db
    await tracker.create_application(1)
    await tracker.create_application(2)
    await tracker.advance_stage(2, PipelineStage.interview)

    applied = await tracker.get_applications_by_stage(PipelineStage.applied)
    assert len(applied) == 1

    interviews = await tracker.get_applications_by_stage(PipelineStage.interview)
    assert len(interviews) == 1


@pytest.mark.asyncio
async def test_count_by_stage(tracker_db):
    tracker, _ = tracker_db
    await tracker.create_application(1)
    await tracker.create_application(2)
    await tracker.advance_stage(2, PipelineStage.interview)

    counts = await tracker.count_by_stage()
    assert counts.get("applied") == 1
    assert counts.get("interview") == 1


@pytest.mark.asyncio
async def test_delete_application(tracker_db):
    tracker, _ = tracker_db
    await tracker.create_application(1)
    await tracker.delete_application(1)
    assert await tracker.get_application(1) is None


@pytest.mark.asyncio
async def test_update_notes(tracker_db):
    tracker, _ = tracker_db
    await tracker.create_application(1)
    await tracker.update_notes(1, "Updated notes")
    app = await tracker.get_application(1)
    assert app["notes"] == "Updated notes"


@pytest.mark.asyncio
async def test_update_contact(tracker_db):
    tracker, _ = tracker_db
    await tracker.create_application(1)
    await tracker.update_contact(1, name="John Doe", email="john@example.com")
    app = await tracker.get_application(1)
    assert app["contact_name"] == "John Doe"
    assert app["contact_email"] == "john@example.com"


# Reminder tests

def test_compute_next_reminder_applied():
    now = "2026-03-01T12:00:00+00:00"
    result = compute_next_reminder(PipelineStage.applied, now)
    assert result is not None
    expected = (datetime(2026, 3, 1, 12, tzinfo=timezone.utc) + timedelta(days=7)).isoformat()
    assert result == expected


def test_compute_next_reminder_terminal():
    now = "2026-03-01T12:00:00+00:00"
    assert compute_next_reminder(PipelineStage.offer, now) is None
    assert compute_next_reminder(PipelineStage.rejected, now) is None
    assert compute_next_reminder(PipelineStage.withdrawn, now) is None


def test_compute_next_reminder_interview():
    now = "2026-03-01T12:00:00+00:00"
    assert compute_next_reminder(PipelineStage.interview, now) is None


def test_get_pending_reminders():
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    future = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    apps = [
        {"job_id": 1, "next_reminder": past},
        {"job_id": 2, "next_reminder": future},
        {"job_id": 3, "next_reminder": None},
    ]
    pending = get_pending_reminders(apps)
    assert len(pending) == 1
    assert pending[0]["job_id"] == 1


def test_format_reminder_message():
    app = {"job_id": 42, "status": "outreach_week1", "contact_name": "Jane", "notes": "Follow up"}
    msg = format_reminder_message(app)
    assert "42" in msg
    assert "outreach_week1" in msg
    assert "Jane" in msg
    assert "Follow up" in msg


def test_format_reminder_message_minimal():
    app = {"job_id": 1, "status": "applied", "contact_name": "", "notes": ""}
    msg = format_reminder_message(app)
    assert "1" in msg
    assert "Jane" not in msg


# Constants tests

def test_stage_order():
    assert len(STAGE_ORDER) == 6
    assert STAGE_ORDER[0] == PipelineStage.applied


def test_terminal_stages():
    assert PipelineStage.offer in TERMINAL_STAGES
    assert PipelineStage.rejected in TERMINAL_STAGES
    assert PipelineStage.withdrawn in TERMINAL_STAGES
    assert PipelineStage.applied not in TERMINAL_STAGES


# Integration: Feature 4 → Feature 5

@pytest.mark.asyncio
async def test_applied_action_auto_creates_application(tracker_db):
    """When user marks a job as 'applied' via user_actions, an application entry is auto-created."""
    tracker, conn = tracker_db
    # Create user_actions table too
    actions = UserActionsDB(conn)
    await actions.init_table()

    # No application yet
    assert await tracker.get_application(1) is None

    # Set action to "applied" — should auto-create application
    await actions.set_action(1, ActionType.applied)

    # Verify application was created
    app = await tracker.get_application(1)
    assert app is not None
    assert app["job_id"] == 1
    assert app["status"] == "applied"
    assert app["next_reminder"] is not None


@pytest.mark.asyncio
async def test_liked_action_does_not_create_application(tracker_db):
    """'Liked' action should NOT auto-create an application entry."""
    tracker, conn = tracker_db
    actions = UserActionsDB(conn)
    await actions.init_table()

    await actions.set_action(1, ActionType.liked)
    assert await tracker.get_application(1) is None
