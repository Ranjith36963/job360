"""T12 — a real concurrent-write integration test (no mocked lock).

``test_db_retry.py`` only proves ``with_write_retry`` retries a *mocked*
``OperationalError``; nothing in the suite fires two real writers at the
same row to see how the actual Postgres connection (the C1 "shared
connection" concern) behaves under a genuine race.

This test opens TWO independent connections against the SAME schema-per-path
test DB (the same pattern ``get_request_db`` uses in production — one fresh
connection per writer, never shared across coroutines) and fires them at the
SAME ``user_feed`` row concurrently via ``asyncio.gather``. ``upsert_feed_row``
is a real ``INSERT ... ON CONFLICT(user_id, job_id) DO UPDATE`` — the row's
UNIQUE constraint is exactly the case C1 worries about. The assertion is the
"weaker but real" invariant this ticket asks for: both writers complete
without raising, exactly one row exists (no duplicate / no corruption), and
its final (score, profile_version) pair is EXACTLY one of the two attempted
writes — never a mismatched hybrid of the two.
"""
import asyncio

import pytest

from src.repositories import pg
from src.services.feed import FeedService


async def _seed_user(db_path: str, user_id: str) -> None:
    async with pg.connect(db_path) as db:
        await db.execute(
            "INSERT INTO users(id, email, password_hash) VALUES(?, ?, ?)",
            (user_id, f"{user_id}@example.com", "!"),
        )
        await db.commit()


async def _writer(db_path: str, *, user_id: str, job_id: int, score: int, profile_version: int) -> None:
    """Opens its OWN connection — mirrors the per-request pattern in
    ``get_request_db`` (src/api/dependencies.py) rather than sharing one
    connection across coroutines, which psycopg3 forbids."""
    async with pg.connect(db_path) as db:
        svc = FeedService(db)
        await svc.upsert_feed_row(
            user_id=user_id,
            job_id=job_id,
            score=score,
            bucket="24h",
            profile_version=profile_version,
        )


@pytest.mark.asyncio
async def test_two_real_connections_race_the_same_upsert_row(migrated_db_path):
    user_id = "racer"
    job_id = 42
    await _seed_user(migrated_db_path, user_id)

    # Two DISTINCT profile_version writers so ``upsert_feed_row``'s freeze
    # branch (score kept when profile_version is unchanged) never masks the
    # race — whichever writer's INSERT/UPDATE actually lands last must win
    # BOTH its score and its profile_version together, never a mix of the two.
    writer_a = _writer(migrated_db_path, user_id=user_id, job_id=job_id, score=55, profile_version=1)
    writer_b = _writer(migrated_db_path, user_id=user_id, job_id=job_id, score=77, profile_version=2)

    # No exception from either coroutine == the UNIQUE-key conflict was
    # handled the way ON CONFLICT DO UPDATE is supposed to handle it, not a
    # crash from two real backends racing the same row.
    await asyncio.gather(writer_a, writer_b)

    async with pg.connect(migrated_db_path) as db:
        cur = await db.execute(
            "SELECT score, profile_version FROM user_feed WHERE user_id = ? AND job_id = ?",
            (user_id, job_id),
        )
        rows = await cur.fetchall()

    # Exactly one row: the UNIQUE(user_id, job_id) constraint held under the
    # real race — no duplicate row snuck in between the two writers' INSERTs.
    assert len(rows) == 1

    row = rows[0]
    final = (row["score"], row["profile_version"])
    # The weaker-but-real invariant: no lost update, no partial/corrupt state.
    # Whichever writer landed last, its score and profile_version travelled
    # together — never (55, 2) or (77, 1), which would mean the row was torn
    # between the two concurrent writes.
    assert final in {(55, 1), (77, 2)}, f"corrupted row state: {final}"
