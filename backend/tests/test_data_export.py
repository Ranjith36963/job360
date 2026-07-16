"""GDPR Article 20 data export (docs/fable/05 C7).

The audit flagged that a user had no way to get their data out. `GET
/api/auth/users/me/export` returns everything we hold on the caller as one JSON
file. These tests pin the three things that matter:
  1. it actually contains the user's own rows (value-presence, rule #21),
  2. it NEVER ships secrets (password hash / encrypted channel creds),
  3. it is scoped to the session's user and cannot be pointed at anyone else
     (rule #12 — no user_id from the URL/body).

Ids are freshly generated per test: the test harness reuses one schema for
`:memory:` DBs, so fixed ids collide with rows left by an earlier run.
"""
from __future__ import annotations

import uuid

import pytest

from src.repositories.database import JobDatabase


async def _mk_user(db: JobDatabase, email: str, pw_hash: str = "h") -> str:
    uid = str(uuid.uuid4())
    await db._conn.execute(
        "INSERT INTO users (id, email, password_hash) VALUES (?, ?, ?)",
        (uid, email, pw_hash),
    )
    await db._conn.commit()
    return uid


@pytest.mark.asyncio
async def test_export_includes_own_rows_and_redacts_secrets():
    db = JobDatabase(":memory:")
    await db.init_db()
    try:
        email = f"{uuid.uuid4().hex}@example.com"
        uid = await _mk_user(db, email, pw_hash="argon2-secret-hash")

        out = await db.export_user_data(uid)

        # 1. the user's own record is present...
        assert out["user"] is not None
        assert out["user"]["email"] == email
        # 2. ...but the password hash is NOT handed out.
        assert out["user"]["password_hash"] == "[redacted]"
        assert "argon2-secret-hash" not in str(out)
        # Token tables must not appear at all.
        for forbidden in ("sessions", "password_resets", "email_verifications", "oauth_states"):
            assert forbidden not in out, f"{forbidden} must not be exported"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_export_is_scoped_to_the_requested_user_only():
    """rule #12 — one user's export must never contain another user's rows."""
    db = JobDatabase(":memory:")
    await db.init_db()
    try:
        my_email = f"{uuid.uuid4().hex}@example.com"
        their_email = f"{uuid.uuid4().hex}@example.com"
        mine = await _mk_user(db, my_email)
        await _mk_user(db, their_email)

        out = await db.export_user_data(mine)
        assert out["user"]["email"] == my_email
        assert their_email not in str(out), "leaked another user's data"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_export_never_includes_the_shared_catalog():
    """rules #10/#17 — the export is the USER's data, not the whole jobs table."""
    db = JobDatabase(":memory:")
    await db.init_db()
    try:
        uid = await _mk_user(db, f"{uuid.uuid4().hex}@example.com")
        out = await db.export_user_data(uid)
        for shared in ("jobs", "job_enrichment", "job_embeddings"):
            assert shared not in out, f"shared catalog table {shared} must not be exported"
    finally:
        await db.close()
