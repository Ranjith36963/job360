"""The per-process schema template (conftest ``_template_sql``) builds the SAME schema as migrating.

``migrated_db_path`` and ``authenticated_async_context`` no longer run init_db +
every migration per test: each pytest process migrates one template schema,
dumps it, and replays the dump into each test's own fresh schema. That is only
safe while the copy is indistinguishable from a fresh migration, so this test
compares the two through the catalog: relations, columns (in order), defaults,
identities, constraints, indexes, sequences (and their owners and values),
functions, triggers, views, types, policies, rules, extended statistics and
every seeded row.
"""
from __future__ import annotations

import asyncio
import os

import psycopg

from src.repositories import pg
from tests import conftest

_CATALOG = {
    "relations": """
        SELECT c.relname, c.relkind, c.relpersistence, c.relrowsecurity, c.relforcerowsecurity,
               coalesce(array_to_string(c.reloptions, ','), ''), coalesce(obj_description(c.oid, 'pg_class'), '')
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = %(s)s""",
    # rank(), not attnum: a migration that dropped a column leaves an attnum gap a
    # replayed CREATE TABLE does not have. Column ORDER is what SELECT * sees.
    "columns": """
        SELECT c.relname, a.attname, rank() OVER (PARTITION BY c.oid ORDER BY a.attnum),
               format_type(a.atttypid, a.atttypmod), a.attnotnull, a.attidentity, a.attgenerated,
               coalesce(pg_get_expr(d.adbin, d.adrelid), ''), coalesce(co.collname, ''),
               coalesce(pg_get_serial_sequence(quote_ident(n.nspname) || '.' || quote_ident(c.relname), a.attname), ''),
               coalesce(col_description(c.oid, a.attnum), '')
        FROM pg_attribute a
        JOIN pg_class c ON c.oid = a.attrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
        LEFT JOIN pg_collation co ON co.oid = a.attcollation AND a.attcollation <> 100
        WHERE n.nspname = %(s)s AND c.relkind IN ('r', 'p', 'v', 'm', 'f') AND a.attnum > 0 AND NOT a.attisdropped""",
    "constraints": """
        SELECT coalesce(c.relname, ''), k.conname, k.contype, pg_get_constraintdef(k.oid, true),
               k.condeferrable, k.condeferred, k.convalidated
        FROM pg_constraint k JOIN pg_namespace n ON n.oid = k.connamespace
        LEFT JOIN pg_class c ON c.oid = k.conrelid
        WHERE n.nspname = %(s)s""",
    "indexes": "SELECT tablename, indexname, indexdef FROM pg_indexes WHERE schemaname = %(s)s",
    "sequences": """
        SELECT sequencename, data_type::text, start_value, min_value, max_value, increment_by, cycle,
               cache_size, last_value
        FROM pg_sequences WHERE schemaname = %(s)s""",
    "sequence_owners": """
        SELECT s.relname, t.relname, a.attname, d.deptype
        FROM pg_depend d
        JOIN pg_class s ON s.oid = d.objid AND s.relkind = 'S'
        JOIN pg_namespace n ON n.oid = s.relnamespace
        JOIN pg_class t ON t.oid = d.refobjid
        JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = d.refobjsubid
        WHERE n.nspname = %(s)s AND d.classid = 'pg_class'::regclass""",
    "functions": """
        SELECT p.proname, pg_get_function_identity_arguments(p.oid), pg_get_functiondef(p.oid)
        FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = %(s)s""",
    "triggers": """
        SELECT c.relname, t.tgname, pg_get_triggerdef(t.oid, true), t.tgenabled
        FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = %(s)s AND NOT t.tgisinternal""",
    "views": """
        SELECT c.relname, pg_get_viewdef(c.oid, true)
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = %(s)s AND c.relkind IN ('v', 'm')""",
    "types": """
        SELECT t.typname, t.typtype, coalesce(string_agg(e.enumlabel, ',' ORDER BY e.enumsortorder), '')
        FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace
        LEFT JOIN pg_enum e ON e.enumtypid = t.oid
        LEFT JOIN pg_class c ON c.oid = t.typrelid
        WHERE n.nspname = %(s)s AND (c.oid IS NULL OR c.relkind = 'c') AND t.typcategory <> 'A'
        GROUP BY 1, 2""",
    "policies": """
        SELECT tablename, policyname, permissive, roles::text, cmd, coalesce(qual, ''), coalesce(with_check, '')
        FROM pg_policies WHERE schemaname = %(s)s""",
    "rules": "SELECT tablename, rulename, definition FROM pg_rules WHERE schemaname = %(s)s",
    "statistics": """
        SELECT s.stxname, pg_get_statisticsobjdef(s.oid)
        FROM pg_statistic_ext s JOIN pg_namespace n ON n.oid = s.stxnamespace
        WHERE n.nspname = %(s)s""",
}

# Wall-clock values a migration writes as it runs. Two FRESH migrations differ
# here too (0002 seeds the placeholder user with created_at = now), so they are
# not a property of the copy; every other column of every seeded row is compared.
_WALL_CLOCK = {("_schema_migrations", "applied_at"), ("users", "created_at")}


def _snapshot(schema: str) -> dict[str, list[tuple]]:
    out: dict[str, list[tuple]] = {}
    with psycopg.connect(pg.DEFAULT_DSN, autocommit=True) as conn:
        for name, sql in _CATALOG.items():
            rows = conn.execute(sql, {"s": schema}).fetchall()
            out[name] = sorted(tuple(str(v).replace(schema, "<schema>") for v in r) for r in rows)
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = %(s)s AND c.relkind IN ('r', 'p') ORDER BY 1",
                {"s": schema},
            ).fetchall()
        ]
        data = []
        for table in tables:
            cols = [
                r[0]
                for r in conn.execute(
                    "SELECT attname FROM pg_attribute WHERE attrelid = (quote_ident(%(s)s) || '.' || quote_ident(%(t)s))::regclass "
                    "AND attnum > 0 AND NOT attisdropped ORDER BY attnum",
                    {"s": schema, "t": table},
                ).fetchall()
                if (table, r[0]) not in _WALL_CLOCK
            ]
            collist = ", ".join(f'"{c}"' for c in cols)
            rows = conn.execute(f'SELECT {collist} FROM "{schema}"."{table}" ORDER BY {collist}').fetchall()  # noqa: S608
            data.append((table, len(rows), repr(rows).replace(schema, "<schema>")))
        out["data"] = data
    return out


def test_template_copy_matches_a_fresh_migration(tmp_path):
    fresh_path, copy_path = str(tmp_path / "fresh.db"), str(tmp_path / "copy.db")
    try:
        conftest._migrate_from_scratch(fresh_path)
        conftest._bootstrap_async_db(copy_path)  # the template path whenever pg_dump is available
        fresh = _snapshot(pg.schema_for_path(fresh_path))
        copy = _snapshot(pg.schema_for_path(copy_path))
    finally:
        asyncio.run(pg.drop_schema(fresh_path))
        asyncio.run(pg.drop_schema(copy_path))

    assert len(fresh["relations"]) > 100 and len(fresh["data"]) > 30, "the snapshot must see the real schema"
    for kind in fresh:
        only_fresh = sorted(set(fresh[kind]) - set(copy[kind]))
        only_copy = sorted(set(copy[kind]) - set(fresh[kind]))
        assert not only_fresh and not only_copy, f"{kind}: fresh-only {only_fresh[:5]} / copy-only {only_copy[:5]}"
        assert fresh[kind] == copy[kind], f"{kind}: same rows, different multiplicity"


def test_ci_builds_from_the_template():
    """CI must take the fast path: without pg_dump the suite silently goes back
    to ~0.8 s of migrations per test (the 30-minute timeout this replaced)."""
    if os.environ.get("CI") != "true" or os.environ.get("J360_TEST_SCHEMA_TEMPLATE") == "0":
        return  # a developer box may lack pg_dump; the fallback is correct, only slower
    assert conftest._template_sql() is not None, "pg_dump missing or failing on the CI runner"
