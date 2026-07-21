"""GDPR erasure must PROVE it worked, not merely fail to raise.

The bug
-------
`hard_delete_user` wrapped every per-user DELETE in `except Exception: pass`.
The stated intent — tolerate a table that does not exist in a partial test
schema — is reasonable, which is exactly why nobody looked again. But the same
handler swallowed permission errors, FK violations, deadlocks and a dropped
connection, so a PARTIALLY FAILED erasure returned normally and the caller told
the user "your account and data have been deleted".

For Art.17 that is not a bug, it is a false statement to a data subject.

Why the fix checks the OUTCOME rather than the exception
--------------------------------------------------------
Type-filtering cannot separate the two cases here: `pg.py` converts
`UndefinedTable` into `OperationalError`, which is also what a lost connection
raises. So "this table doesn't exist" and "the database went away" are
indistinguishable by exception type at this layer. The fix therefore counts
surviving rows afterwards and raises if any remain.

These tests assert the BEHAVIOUR (it raises, and the message names the table)
rather than that the code contains a try/except — a structural test would pass
against a handler that still silently swallowed everything.
"""

import uuid

import pytest

from migrations import runner
from src.repositories.database import JobDatabase


def _fresh_db_path(tmp_path) -> str:
    """A unique FILE path, deliberately not ``:memory:``.

    ``pg.schema_for_path`` hashes a file path to a STABLE schema, so every
    connect to it shares state. ``:memory:`` gets a FRESH schema per connect —
    so ``init_db()`` would create ``users`` in one schema and the next connect
    would land in an empty one.

    That bug passed locally and only failed on CI: locally ``search_path`` falls
    back to ``public``, which had tables left over from earlier runs, so the
    residue silently satisfied the query. A clean database has no such residue.
    The uuid keeps runs from colliding with each other.
    """
    return str(tmp_path / f"erasure_{uuid.uuid4().hex[:8]}.db")


@pytest.mark.asyncio
async def test_erasure_raises_when_rows_survive(tmp_path, monkeypatch):
    """If a per-user table still holds rows afterwards, erasure must FAIL LOUD."""
    db_path = _fresh_db_path(tmp_path)
    db = JobDatabase(db_path)
    await db.init_db()
    # `users` is created by a MIGRATION (Batch 2), not by init_db() — init_db()
    # only builds the legacy catalog tables. Without this the table is absent.
    # This passed locally purely because `search_path` falls back to `public`,
    # which still held `users` from earlier runs; on a clean database it fails.
    await runner.up(db_path)

    try:
        # Real erasure of a non-existent user is a no-op and must not raise:
        # nothing survives, so the verification finds nothing.
        await db.hard_delete_user("ghost-user-that-never-existed")

        # Now make ONE table's DELETE fail the way a real incident does —
        # permission denied / FK block / connection dropped. Before the fix this
        # was caught by `except Exception: pass` and erasure returned normally,
        # so the caller told the user their data was gone while it was not.
        # Fail every per-user DELETE (but not the final `DELETE FROM users`), so
        # the test does not depend on which tables this particular schema has.
        real_execute = db._db.execute
        failed: list[str] = []

        async def _execute_with_failing_deletes(sql, params=None):
            stripped = sql.strip().upper()
            if stripped.startswith("DELETE FROM") and " USERS " not in f" {stripped} ":
                failed.append(sql)
                raise RuntimeError("permission denied")
            return await real_execute(sql, params)

        monkeypatch.setattr(db._db, "execute", _execute_with_failing_deletes)

        with pytest.raises(RuntimeError) as exc:
            await db.hard_delete_user("victim")

        msg = str(exc.value)
        assert "erasure did not complete" in msg, (
            f"a failed DELETE must surface as a failed erasure, got: {msg}"
        )
        assert "permission denied" in msg, (
            f"the underlying cause must be preserved so it is actionable; got: {msg}"
        )
        assert failed, "the test did not actually intercept any DELETE"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_erasure_tolerates_a_genuinely_absent_table(tmp_path):
    """A table missing from this schema is skipped — the original, correct tolerance.

    This is the case the old `except Exception: pass` existed for, and it must
    keep working: erasure on a partial schema should not explode.
    """
    db_path = _fresh_db_path(tmp_path)
    db = JobDatabase(db_path)
    await db.init_db()
    # `users` is created by a MIGRATION (Batch 2), not by init_db() — init_db()
    # only builds the legacy catalog tables. Without this the table is absent.
    # This passed locally purely because `search_path` falls back to `public`,
    # which still held `users` from earlier runs; on a clean database it fails.
    await runner.up(db_path)
    try:
        # No exception even though several _PER_USER_TABLES are created by later
        # migrations and may not exist in this bare schema.
        await db.hard_delete_user("nobody")
    finally:
        await db.close()
