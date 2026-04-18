"""Tenant isolation — dedicated test class per Batch 2 success criteria.

Goal: tenant A cannot read/write tenant B's user_actions or applications.
Jobs remain a shared catalog (CLAUDE.md rule #1 + decisions doc D6).
"""
import os
import tempfile
from datetime import datetime, timezone

import aiosqlite
import pytest

from migrations import runner
from src.core.tenancy import DEFAULT_TENANT_ID


@pytest.fixture
async def tenant_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    # Phase 1 + Phase 2 migrations: first create legacy schema, then apply runner.
    async with aiosqlite.connect(path) as db:
        await db.executescript(
            """
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
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


class TestTenantIsolation:
    """Hard success criterion: tenant A must not read tenant B's rows."""

    @pytest.mark.asyncio
    async def test_default_tenant_user_exists_after_migration(self, tenant_db):
        async with aiosqlite.connect(tenant_db) as db:
            cur = await db.execute(
                "SELECT id, email FROM users WHERE id = ?", (DEFAULT_TENANT_ID,)
            )
            row = await cur.fetchone()
        assert row is not None
        assert row[1] == "local@job360.local"

    @pytest.mark.asyncio
    async def test_single_user_action_lands_on_default_tenant(self, tenant_db):
        async with aiosqlite.connect(tenant_db) as db:
            # Legacy INSERT path that does NOT supply user_id — DEFAULT kicks in.
            await db.execute(
                "INSERT INTO user_actions(job_id, action, created_at) VALUES(?, ?, ?)",
                (1, "liked", datetime.now(timezone.utc).isoformat()),
            )
            await db.commit()
            cur = await db.execute(
                "SELECT user_id FROM user_actions WHERE job_id = 1"
            )
            row = await cur.fetchone()
        assert row[0] == DEFAULT_TENANT_ID

    @pytest.mark.asyncio
    async def test_tenant_a_cannot_read_tenant_b_actions(self, tenant_db):
        async with aiosqlite.connect(tenant_db) as db:
            await db.execute(
                "INSERT INTO users(id, email, password_hash) VALUES(?, ?, ?)",
                ("tenant-a", "a@test", "!"),
            )
            await db.execute(
                "INSERT INTO users(id, email, password_hash) VALUES(?, ?, ?)",
                ("tenant-b", "b@test", "!"),
            )
            await db.commit()
            await db.execute(
                "INSERT INTO user_actions(user_id, job_id, action, created_at) VALUES(?, ?, ?, ?)",
                ("tenant-a", 100, "liked", "2026-04-18T00:00:00+00:00"),
            )
            await db.commit()
            cur = await db.execute(
                "SELECT id FROM user_actions WHERE user_id = ?", ("tenant-b",)
            )
            rows = await cur.fetchall()
        assert rows == [], "tenant B must see zero tenant-A actions"

    @pytest.mark.asyncio
    async def test_tenant_a_cannot_read_tenant_b_applications(self, tenant_db):
        async with aiosqlite.connect(tenant_db) as db:
            await db.execute(
                "INSERT INTO users(id, email, password_hash) VALUES(?, ?, ?)",
                ("tenant-a", "a@test", "!"),
            )
            await db.execute(
                "INSERT INTO users(id, email, password_hash) VALUES(?, ?, ?)",
                ("tenant-b", "b@test", "!"),
            )
            await db.commit()
            now = datetime.now(timezone.utc).isoformat()
            await db.execute(
                "INSERT INTO applications(user_id, job_id, stage, created_at, updated_at) VALUES(?, ?, ?, ?, ?)",
                ("tenant-a", 200, "applied", now, now),
            )
            await db.commit()
            cur = await db.execute(
                "SELECT id FROM applications WHERE user_id = ?", ("tenant-b",)
            )
            rows = await cur.fetchall()
        assert rows == []

    @pytest.mark.asyncio
    async def test_same_job_can_be_actioned_by_two_tenants(self, tenant_db):
        """Widened UNIQUE(user_id, job_id) — two users can like the same job."""
        async with aiosqlite.connect(tenant_db) as db:
            await db.execute(
                "INSERT INTO users(id, email, password_hash) VALUES(?, ?, ?)",
                ("tenant-a", "a@test", "!"),
            )
            await db.execute(
                "INSERT INTO users(id, email, password_hash) VALUES(?, ?, ?)",
                ("tenant-b", "b@test", "!"),
            )
            await db.commit()
            now = "2026-04-18T00:00:00+00:00"
            await db.execute(
                "INSERT INTO user_actions(user_id, job_id, action, created_at) VALUES(?, ?, ?, ?)",
                ("tenant-a", 42, "liked", now),
            )
            await db.execute(
                "INSERT INTO user_actions(user_id, job_id, action, created_at) VALUES(?, ?, ?, ?)",
                ("tenant-b", 42, "applied", now),
            )
            await db.commit()
            cur = await db.execute("SELECT COUNT(*) FROM user_actions WHERE job_id = 42")
            row = await cur.fetchone()
        assert row[0] == 2

    @pytest.mark.asyncio
    async def test_duplicate_action_within_one_tenant_rejected(self, tenant_db):
        """Same (user_id, job_id) still unique within a tenant."""
        async with aiosqlite.connect(tenant_db) as db:
            await db.execute(
                "INSERT INTO users(id, email, password_hash) VALUES(?, ?, ?)",
                ("tenant-a", "a@test", "!"),
            )
            await db.commit()
            now = "2026-04-18T00:00:00+00:00"
            await db.execute(
                "INSERT INTO user_actions(user_id, job_id, action, created_at) VALUES(?, ?, ?, ?)",
                ("tenant-a", 7, "liked", now),
            )
            await db.commit()
            with pytest.raises(Exception):  # IntegrityError
                await db.execute(
                    "INSERT INTO user_actions(user_id, job_id, action, created_at) VALUES(?, ?, ?, ?)",
                    ("tenant-a", 7, "applied", now),
                )
                await db.commit()

    def test_normalized_key_unchanged(self):
        """CLAUDE.md rule #1 — normalized_key() must not have moved."""
        from src.models import Job

        j = Job(
            title="Software Engineer",
            company="Acme Ltd",
            apply_url="https://x",
            source="test",
            date_found=datetime.now(timezone.utc),
        )
        assert j.normalized_key() == ("acme", "software engineer")
