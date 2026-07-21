"""Email matching must be case-insensitive, or users get locked out of their own accounts.

The bug
-------
Emails were stored verbatim and looked up with `WHERE email = ?` — a
case-sensitive compare in Postgres. Register as "Alice@Example.com", come back
later and type "alice@example.com" (autofill, phone auto-capitalisation, or just
typing naturally) and the SELECT returns nothing. Login then runs its
timing-safe dummy-hash path and answers a flat 401 "invalid credentials" — with
the correct password.

The user tries Forgot Password. That lookup was case-sensitive too, so it found
nothing, logged "unknown email", and sent no mail — while still returning 204,
because not revealing whether an account exists is deliberate. So the user sees
"check your email", nothing arrives, and from their side the account has simply
vanished with no explanation.

Why LOWER() on reads and not just .lower() on writes
----------------------------------------------------
Lowercasing writes alone would fix future signups and leave every ALREADY
registered mixed-case user permanently locked out — a worse bug than the one
being fixed. The reads use LOWER(email) = LOWER(?) so existing rows still match,
which is why no migration is needed.
"""

import uuid

import pytest

from migrations import runner


def _fresh_db_path(tmp_path) -> str:
    """A unique FILE path, deliberately not ``:memory:``.

    ``pg.schema_for_path`` hashes a file path to a STABLE schema, so every
    connect shares state. ``:memory:`` gets a FRESH schema per connect, so
    ``init_db()`` creates ``users`` in one schema and the next connect lands in
    an empty one.

    That bug passed locally and failed only on CI: locally ``search_path`` falls
    back to ``public``, which still held tables from earlier runs, so leftover
    residue satisfied the query. A clean database has none.
    """
    return str(tmp_path / f"email_{uuid.uuid4().hex[:8]}.db")



@pytest.mark.asyncio
async def test_login_finds_user_regardless_of_email_case(tmp_path):
    """The core lockout: stored mixed-case, typed lowercase, must still match."""
    from src.repositories import pg
    from src.repositories.database import JobDatabase

    tag = uuid.uuid4().hex[:8]
    uid = f"u-{tag}"
    db_path = _fresh_db_path(tmp_path)
    db = JobDatabase(db_path)
    await db.init_db()
    # `users` is created by a MIGRATION (Batch 2), not by init_db() — init_db()
    # only builds the legacy catalog tables. Without this the table is absent.
    # This passed locally purely because `search_path` falls back to `public`,
    # which still held `users` from earlier runs; on a clean database it fails.
    await runner.up(db_path)
    try:
        conn = db._db
        conn.row_factory = pg.Row
        # A user registered BEFORE the fix — stored verbatim, mixed case.
        await conn.execute(
            "INSERT INTO users(id, email, password_hash) VALUES(?, ?, ?)",
            (uid, f"Alice.{tag}@Example.COM", "hash"),
        )
        await conn.commit()

        # They type it lowercase. This is the exact query the login route runs.
        cur = await conn.execute(
            "SELECT id FROM users WHERE LOWER(email) = LOWER(?) AND deleted_at IS NULL",
            (f"alice.{tag}@example.com",),
        )
        row = await cur.fetchone()
        assert row is not None and row["id"] == uid, (
            "a legitimate user typing their own email in a different case was not "
            "found — they get 'invalid credentials' with the correct password"
        )

        # And the old case-sensitive form is what used to fail — proving the
        # scenario is real, not hypothetical.
        cur = await conn.execute(
            "SELECT id FROM users WHERE email = ? AND deleted_at IS NULL",
            (f"alice.{tag}@example.com",),
        )
        assert await cur.fetchone() is None, (
            "case-sensitive lookup unexpectedly matched — the bug this guards "
            "against may no longer be reproducible, re-check the fix"
        )
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_password_reset_finds_user_regardless_of_case(tmp_path):
    """Forgot-password must not silently no-op on a case variant.

    This is the cruellest half: the route returns 204 either way (deliberate
    no-enumeration), so a miss is indistinguishable from success to the user.
    """
    from src.repositories import pg
    from src.repositories.database import JobDatabase

    tag = uuid.uuid4().hex[:8]
    uid = f"u-{tag}"
    db_path = _fresh_db_path(tmp_path)
    db = JobDatabase(db_path)
    await db.init_db()
    # `users` is created by a MIGRATION (Batch 2), not by init_db() — init_db()
    # only builds the legacy catalog tables. Without this the table is absent.
    # This passed locally purely because `search_path` falls back to `public`,
    # which still held `users` from earlier runs; on a clean database it fails.
    await runner.up(db_path)
    try:
        conn = db._db
        conn.row_factory = pg.Row
        await conn.execute(
            "INSERT INTO users(id, email, password_hash) VALUES(?, ?, ?)",
            (uid, f"Bob.Smith.{tag}@Work.example", "hash"),
        )
        await conn.commit()

        cur = await conn.execute(
            "SELECT id FROM users WHERE LOWER(email) = LOWER(?) AND deleted_at IS NULL",
            (f"bob.smith.{tag}@work.example",),
        )
        assert (await cur.fetchone()) is not None, (
            "reset lookup missed a case variant — the user gets 'check your email' "
            "and no email ever arrives"
        )
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_new_registrations_store_lowercase(tmp_path):
    """New rows are canonical, so UNIQUE actually blocks case-variant duplicates.

    Without this, "Alice@x.com" and "alice@x.com" are two separate accounts that
    deliver to the same real inbox.
    """
    from src.repositories import pg
    from src.repositories.database import JobDatabase

    tag = uuid.uuid4().hex[:8]
    uid = f"u-{tag}"
    db_path = _fresh_db_path(tmp_path)
    db = JobDatabase(db_path)
    await db.init_db()
    # `users` is created by a MIGRATION (Batch 2), not by init_db() — init_db()
    # only builds the legacy catalog tables. Without this the table is absent.
    # This passed locally purely because `search_path` falls back to `public`,
    # which still held `users` from earlier runs; on a clean database it fails.
    await runner.up(db_path)
    try:
        conn = db._db
        conn.row_factory = pg.Row
        # what the register route now inserts
        await conn.execute(
            "INSERT INTO users(id, email, password_hash) VALUES(?, ?, ?)",
            (uid, f"Carol.{tag}@Example.com".lower(), "hash"),
        )
        await conn.commit()

        cur = await conn.execute("SELECT email FROM users WHERE id = ?", (uid,))
        row = await cur.fetchone()
        assert row["email"] == f"carol.{tag}@example.com"

        # a case-variant signup must now collide instead of creating a twin
        with pytest.raises(pg.IntegrityError):
            await conn.execute(
                "INSERT INTO users(id, email, password_hash) VALUES(?, ?, ?)",
                (uid + "-b", f"Carol.{tag}@Example.com".lower(), "hash"),
            )
    finally:
        await db.close()
