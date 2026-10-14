"""A new table with a `user_id` column must not silently dodge account erasure.

`hard_delete_user` only deletes from `JobDatabase._PER_USER_TABLES`. Nothing else
stops a new migration adding `some_table(user_id ...)` that erasure never touches
(a GDPR Art.17 hole). This test lists every table at migration head that has a
`user_id` column and requires each to be either erased or deliberately allowlisted.
"""

import uuid

import pytest

from migrations import runner
from src.repositories.database import JobDatabase

# Tables with a user_id column that hard_delete_user handles OUTSIDE the generic
# _PER_USER_TABLES sweep. Adding to this list needs a reason that holds for GDPR.
ERASURE_ALLOWLIST = {
    # Security history kept for legitimate interest; hard_delete_user NULLs
    # user_id (anonymises) instead of deleting the rows.
    "audit_log",
}


@pytest.mark.asyncio
async def test_every_user_id_table_is_erased_or_allowlisted(tmp_path):
    db_path = str(tmp_path / f"erasure_cols_{uuid.uuid4().hex[:8]}.db")
    db = JobDatabase(db_path)
    await db.init_db()
    await runner.up(db_path)
    try:
        cur = await db._db.execute(
            "SELECT DISTINCT table_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND column_name = 'user_id'"
        )
        tables = {r[0] for r in await cur.fetchall()}
    finally:
        await db.close()

    assert tables, "found no user_id tables - the schema query is broken"
    # The reverse: a stale or mistyped list entry is silently skipped by
    # hard_delete_user (it filters to tables that have user_id), so name it.
    stale = sorted(set(JobDatabase._PER_USER_TABLES) - tables)
    assert not stale, f"_PER_USER_TABLES names tables with no user_id column: {stale}"
    handled = set(JobDatabase._PER_USER_TABLES) | ERASURE_ALLOWLIST
    missing = sorted(tables - handled)
    assert not missing, (
        f"tables with a user_id column that account erasure does not cover: {missing}. "
        "Add them to JobDatabase._PER_USER_TABLES (hard_delete_user deletes from it) "
        "or, with a reason, to ERASURE_ALLOWLIST here."
    )
