"""Tests for the observation layer (scripts/observe.py).

The point of that script is to answer "what is stored, for which user, under
which profile" from MEASUREMENT rather than memory. So the tests that matter are
the ones that stop it lying: it must never leak an address, and it must never go
blind when a new per-user table appears.
"""

from __future__ import annotations

import pytest

from scripts.observe import PER_USER_TABLES, SHARED_TABLES, mask_email


class TestMaskEmail:
    """Output from this script gets pasted into chats and issues."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("ranjithmaligaguruprakash@gmail.com", "ranj***@gmail.com"),
            ("ab@x.io", "ab***@x.io"),          # shorter than the 4-char prefix
            ("", "(none)"),
            (None, "(none)"),
            ("not-an-email", "(none)"),          # no @ → do not echo it back
        ],
    )
    def test_masks_every_shape(self, raw, expected):
        assert mask_email(raw) == expected

    def test_never_emits_the_full_local_part(self):
        raw = "verylonglocalpart@example.com"
        out = mask_email(raw)
        assert "verylonglocalpart" not in out
        assert out.endswith("@example.com"), "domain is kept — it identifies without harvesting"


class TestTableListsMatchTheSchema:
    """Drift guards. The script is only trustworthy while its table lists match
    reality — a new per-user table that nobody adds here is a table the
    observation layer silently cannot see, which is worse than no layer at all.
    """

    @pytest.mark.asyncio
    async def test_every_user_owned_table_is_registered(self):
        from src.api import dependencies as api_deps

        db = await api_deps.get_db()
        cur = await db._conn.execute(
            "SELECT table_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND column_name = 'user_id'"
        )
        found = {r[0] for r in await cur.fetchall()}
        registered = {t for t, _ in PER_USER_TABLES}

        missing = found - registered - set(SHARED_TABLES)
        assert not missing, (
            "these tables carry a user_id but are NOT in PER_USER_TABLES, so "
            f"observe.py cannot see them: {sorted(missing)}"
        )

    @pytest.mark.asyncio
    async def test_shared_catalog_tables_carry_no_owner_column(self):
        """Rules #10/#17 — the invariant the whole design rests on."""
        from src.api import dependencies as api_deps

        db = await api_deps.get_db()
        for table in SHARED_TABLES:
            cur = await db._conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = ? "
                "AND column_name IN ('user_id', 'tenant_id')",
                (table,),
            )
            owned = [r[0] for r in await cur.fetchall()]
            assert owned == [], (
                f"{table} is the SHARED catalog and must carry no owner column, "
                f"found {owned}"
            )


# NOTE: the script's live SQL is not smoke-tested here. Connecting a raw psycopg
# cursor would land in `public`, not the per-test schema conftest creates, so such
# a test would either read the wrong rows or prove nothing. It is verified the way
# it is meant to be used instead — run against PRODUCTION via
# `railway run -s Postgres python backend/scripts/observe.py --system`, which on
# 2026-07-28 reported 0 ownership leaks and 0 orphans across 18 tables, and
# independently flagged tailored_documents as 0/4 traceable.
