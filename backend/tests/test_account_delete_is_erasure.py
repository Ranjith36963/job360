"""`DELETE /api/auth/users/me` is an ERASURE, not a reversible soft-delete.

Why this file exists: `.claude/skills/verify-job360/CHECKLIST.md` item 31 told
the nightly sweep the route "sets `deleted_at`" and to "restore after to keep
the demo account". `routes/auth.delete_account` calls
`repositories.database.JobDatabase.hard_delete_user`, which drops the `users`
row (GDPR Art.17) — there is nothing left to restore. An agent following the
old wording would have destroyed the account it was told it could bring back.

The pre-existing coverage in `test_account_mgmt.py` cannot catch that: it
asserts only that a later login fails, which is true under BOTH a tombstone and
an erasure. This test asserts the distinguishing fact — no row survives.
"""

from __future__ import annotations

import json

import pytest

from src.repositories import pg


async def _users_rows(db_path: str, user_id: str) -> list[dict]:
    async with pg.connect(db_path) as db:
        db.row_factory = pg.Row
        cur = await db.execute("SELECT id, email, deleted_at FROM users WHERE id = ?", (user_id,))
        return [dict(r) for r in await cur.fetchall()]


@pytest.mark.asyncio
async def test_delete_me_erases_the_user_row(authenticated_async_context, fixture_user_id):
    """204, then the `users` row is GONE — not tombstoned with `deleted_at`."""
    from src.core import settings

    db_path = str(settings.DB_PATH)

    before = await _users_rows(db_path, fixture_user_id)
    assert len(before) == 1, "fixture user should exist before the delete"

    async with authenticated_async_context() as client:
        resp = await client.request(
            "DELETE",
            "/api/auth/users/me",
            content=json.dumps({"current_password": "s3cretpassword"}),
            headers={"Content-Type": "application/json"},
        )
    assert resp.status_code == 204, resp.text

    after = await _users_rows(db_path, fixture_user_id)
    assert after == [], (
        "DELETE /users/me must ERASE the user row (hard_delete_user). "
        f"A surviving row means the route soft-deletes and the CHECKLIST's "
        f"'restore after' would be possible again: {after}"
    )


@pytest.mark.asyncio
async def test_delete_me_wrong_password_leaves_the_row_intact(authenticated_async_context, fixture_user_id):
    """The erasure is gated on the current password (hard rule #26)."""
    from src.core import settings

    db_path = str(settings.DB_PATH)

    async with authenticated_async_context() as client:
        resp = await client.request(
            "DELETE",
            "/api/auth/users/me",
            content=json.dumps({"current_password": "WRONGPASSWORD"}),
            headers={"Content-Type": "application/json"},
        )
    assert resp.status_code == 401, resp.text

    rows = await _users_rows(db_path, fixture_user_id)
    assert len(rows) == 1, "a rejected delete must not erase anything"
    assert rows[0]["deleted_at"] is None
