"""SI1 — the per-user search must actually TRIGGER notifications.

Before this fix, run_search wrote user_feed but never enqueued
send_notification, so no user ever received a job-match alert and the
score_threshold gate was dead code. These tests pin the wiring:
``_enqueue_notifications`` enqueues send_notification for the rows just
written that clear the user's enabled notification_rules score_threshold,
and no-ops safely otherwise.
"""

from datetime import datetime, timezone

import pytest

from src.main import _enqueue_notifications
from src.repositories import pg


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _seed_user_and_rule(conn, user_id: str, threshold: int, enabled: int = 1) -> None:
    await conn.execute(
        "INSERT INTO users(id, email, password_hash) VALUES(?, ?, ?)",
        (user_id, f"{user_id}@example.com", "h"),
    )
    await conn.execute(
        "INSERT INTO notification_rules(user_id, notify_mode, enabled, score_threshold, "
        "created_at, updated_at) VALUES(?, ?, ?, ?, ?, ?)",
        (user_id, "instant", enabled, threshold, _now(), _now()),
    )
    await conn.commit()


@pytest.mark.asyncio
async def test_si1_enqueues_only_above_threshold(migrated_db_path):
    async with pg.connect(migrated_db_path) as conn:
        await _seed_user_and_rule(conn, "u1", threshold=50)
        captured: list = []

        def stub(fn, *args):
            captured.append((fn, *args))

        # scores: 80 (>=50 ✓), 30 (<50 ✗), 50 (>=50 ✓)
        n = await _enqueue_notifications(conn, "u1", [(10, 80), (11, 30), (12, 50)], stub)

    assert n == 2
    assert ("send_notification", "u1", 10, "instant") in captured
    assert ("send_notification", "u1", 12, "instant") in captured
    # the below-threshold job (11) must NOT be enqueued
    assert all(args[2] != 11 for args in captured)


@pytest.mark.asyncio
async def test_si1_no_enabled_rule_enqueues_nothing(migrated_db_path):
    async with pg.connect(migrated_db_path) as conn:
        captured: list = []
        n = await _enqueue_notifications(
            conn, "u_no_rule", [(10, 90)], lambda *a: captured.append(a)
        )
    assert n == 0
    assert captured == []


@pytest.mark.asyncio
async def test_si1_disabled_rule_enqueues_nothing(migrated_db_path):
    async with pg.connect(migrated_db_path) as conn:
        await _seed_user_and_rule(conn, "u_disabled", threshold=0, enabled=0)
        captured: list = []
        n = await _enqueue_notifications(
            conn, "u_disabled", [(10, 90)], lambda *a: captured.append(a)
        )
    assert n == 0
    assert captured == []


@pytest.mark.asyncio
async def test_si1_supports_async_enqueue_hook(migrated_db_path):
    async with pg.connect(migrated_db_path) as conn:
        await _seed_user_and_rule(conn, "u2", threshold=0)
        captured: list = []

        async def astub(fn, *args):
            captured.append((fn, *args))

        n = await _enqueue_notifications(conn, "u2", [(5, 10)], astub)

    assert n == 1
    assert captured == [("send_notification", "u2", 5, "instant")]
