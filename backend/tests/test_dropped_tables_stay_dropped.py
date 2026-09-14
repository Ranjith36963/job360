"""The tables 0039/0040 dropped are absent at migration HEAD.

Five LIVING docs named `user_actions` (and one named `user_feed`) as live
per-user state long after `0040_drop_notification_tables` removed them. Prose
could not catch that: `init_db()` still CREATEs `user_actions` as legacy
scaffolding, because `0002_multi_tenant`'s rebuild pattern copies FROM it — so
the name is in the tree, in the baseline schema, and grep-visible, while the
table it names does not survive to head.

So the docs stopped listing the names and cite this instead. What matters is
not which migration dropped what, but what the schema looks like when every
migration has run: that is the only shape an incident responder, a reviewer or
an agent ever queries.

One schema bootstrap per test (the fixture runs init_db + every migration), so
each test asserts over its whole set rather than parametrising.
"""
from __future__ import annotations

import pytest

# Dropped by 0039_drop_sourcing_tables (slice 5, #483).
_SOURCING_TABLES = ("run_log", "job_enrichment", "job_embeddings")

# Dropped by 0040_drop_notification_tables (the mission sweep).
_NOTIFICATION_TABLES = (
    "notification_rules",
    "notification_ledger",
    "user_channels",
    "user_notification_digests",
    "user_actions",
    "user_feed",
)

# The per-user state that DID survive — what a doc may name.
_LIVE_PER_USER_TABLES = (
    "applications",
    "application_events",
    "application_artifacts",
    "application_receipts",
)


async def _table_names(db_path: str) -> set[str]:
    from src.repositories import pg as _pg

    async with _pg.connect(db_path) as db:
        cur = await db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        return {row[0] for row in await cur.fetchall()}


@pytest.mark.asyncio
async def test_dropped_tables_are_absent_at_head(migrated_db_path):
    names = await _table_names(migrated_db_path)
    still_there = sorted(
        t for t in _SOURCING_TABLES + _NOTIFICATION_TABLES if t in names
    )
    assert not still_there, (
        f"dropped on purpose but present at migration head: {still_there}. If a "
        f"slice genuinely brings one back, give it its own migration and update "
        f"this test — never a bare CREATE in init_db()."
    )


@pytest.mark.asyncio
async def test_live_per_user_tables_survive(migrated_db_path):
    names = await _table_names(migrated_db_path)
    missing = sorted(t for t in _LIVE_PER_USER_TABLES if t not in names)
    assert not missing, f"gone from the schema: {missing} — the docs naming them are now wrong."
