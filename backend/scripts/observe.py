"""Observation layer — see WHAT is stored, for WHICH user, under WHICH profile.

The whole point: never answer "is the data isolated?" or "why does this user see
that?" from memory or from reading code. Run this and read the numbers.

    # everything about one person
    python scripts/observe.py --user someone@example.com

    # system-wide integrity: orphans, unattributed rows, per-table ownership
    python scripts/observe.py --system

Against production, prefix with Railway so DATABASE_PUBLIC_URL is injected:

    railway run -s Postgres python backend/scripts/observe.py --system

SAFETY, deliberately built in rather than left to the operator:
  * READ-ONLY. Only SELECTs are issued.
  * Emails are MASKED in every line of output.
  * CV text, tokens and credentials are NEVER printed — only lengths and counts.
Those three rules exist because this output gets pasted into chats and issues.

The ownership model it checks (the design this exists to police):
  * `user_id` owns everything — profiles, feed, settings, channels, documents.
  * `jobs` / `job_enrichment` / `job_embeddings` are the SHARED catalog and must
    carry no user_id (CLAUDE.md rules #10/#17). That is correct, not a leak.
  * every scored row names the `profile_version` that produced it, so any
    artifact can be traced back to the profile snapshot behind it.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Optional

# Per-user tables and the column that ties each row to its owner. Keeping this
# as data (not a pile of hand-written queries) means a new per-user table is one
# line here and is then covered by every check below — the orphan sweep included.
PER_USER_TABLES: tuple[tuple[str, str], ...] = (
    ("user_profiles", "user_id"),
    ("user_profile_versions", "user_id"),
    ("user_feed", "user_id"),
    ("user_actions", "user_id"),
    ("applications", "user_id"),
    ("application_stage_history", "user_id"),
    ("user_channels", "user_id"),
    ("notification_rules", "user_id"),
    ("notification_ledger", "user_id"),
    ("user_notification_digests", "user_id"),
    ("application_receipts", "user_id"),
    # Application spine (docs/plans/2026-09-04-application-spine/spec.md).
    ("application_events", "user_id"),
    ("application_artifacts", "user_id"),
    ("api_tokens", "user_id"),
    ("tailored_documents", "user_id"),
    ("tailored_usage", "user_id"),
    ("sessions", "user_id"),
    # Auth + audit trail. Added after the drift guard in tests/test_observe.py
    # caught them missing — which is the whole reason that test exists: a
    # per-user table nobody registers here is one this layer silently cannot see.
    ("email_verifications", "user_id"),
    ("password_resets", "user_id"),
    ("oauth_states", "user_id"),
    ("oauth_grants", "user_id"),
    ("audit_log", "user_id"),
)

# The SHARED catalog. These MUST NOT have a user_id — a user_id appearing here
# is the leak, and its absence is the design working.
# Slice 5 (#483) dropped `job_enrichment` and `job_embeddings` (migration
# 0038); `jobs` is all that is left of the shared catalog — and every row
# in it is now an ad some user brought.
SHARED_TABLES: tuple[str, ...] = ("jobs",)


def mask_email(email: Optional[str]) -> str:
    """Never print a full address. Enough to identify, not enough to harvest."""
    if not email or "@" not in email:
        return "(none)"
    user, domain = email.split("@", 1)
    return f"{user[:4]}***@{domain}"


def _table_exists(cur: Any, table: str) -> bool:
    """Existence check via current_schema(), never to_regclass.

    `to_regclass` follows `search_path`, which falls back to `public` — so a
    caller running inside its own schema can get a TRUE for a table it cannot
    see, and then operate on the wrong rows.
    """
    cur.execute(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema = current_schema() AND table_name = %s",
        (table,),
    )
    return cur.fetchone() is not None


def _count(cur: Any, sql: str, params: tuple = ()) -> int:
    cur.execute(sql, params)
    row = cur.fetchone()
    return int(row[0]) if row else 0


def _rule(title: str) -> None:
    print(f"\n{'=' * 74}\n{title}\n{'=' * 74}")


# ---------------------------------------------------------------------------
# --user : everything about one person
# ---------------------------------------------------------------------------


def observe_user(cur: Any, email: str) -> int:
    cur.execute(
        "SELECT id, email, created_at, email_verified_at, deleted_at "
        "FROM users WHERE lower(email) = lower(%s)",
        (email,),
    )
    row = cur.fetchone()
    if row is None:
        print(f"no user found for {mask_email(email)}")
        return 1
    uid, mail, created, verified, deleted = row

    _rule(f"USER  {mask_email(mail)}")
    print(f"  user_id     : {uid}")
    print(f"  created     : {created}")
    print(f"  verified    : {'yes' if verified else 'NO'}")
    print(f"  deleted     : {deleted or '-'}")

    _rule("PROFILE — the snapshots this user's data was extracted into")
    versions = 0
    if _table_exists(cur, "user_profile_versions"):
        cur.execute(
            "SELECT id, created_at, source_action, length(cv_data::text) "
            "FROM user_profile_versions WHERE user_id = %s ORDER BY id DESC LIMIT 10",
            (uid,),
        )
        rows = cur.fetchall()
        versions = len(rows)
        if rows:
            print(f"  {'ver':<7}{'created':<22}{'why it was written':<26}{'cv bytes':>9}")
            for vid, cts, action, blob in rows:
                print(f"  v{vid:<6}{str(cts)[:19]:<22}{str(action or '-')[:24]:<26}{blob or 0:>9}")
        else:
            print("  (no profile versions — nothing has been extracted yet)")

    _rule("EVERYTHING OWNED BY THIS user_id")
    for table, col in PER_USER_TABLES:
        if table in {"user_feed", "user_profile_versions"} or not _table_exists(cur, table):
            continue
        n = _count(cur, f"SELECT count(*) FROM {table} WHERE {col} = %s", (uid,))  # noqa: S608
        print(f"  {table:<28}{n:>7}")

    # Provenance on the most expensive artifact the product makes.
    if _table_exists(cur, "tailored_documents"):
        total = _count(
            cur, "SELECT count(*) FROM tailored_documents WHERE user_id = %s", (uid,)
        )
        stamped = _count(
            cur,
            "SELECT count(profile_version) FROM tailored_documents WHERE user_id = %s",
            (uid,),
        )
        if total:
            note = "" if stamped == total else "  <-- cannot be traced to a profile"
            print(f"\n  tailored docs traceable to a profile: {stamped}/{total}{note}")

    print(f"\n  summary: {versions} profile version(s) on record.")
    return 0


# ---------------------------------------------------------------------------
# --system : integrity across every table
# ---------------------------------------------------------------------------


def observe_system(cur: Any) -> int:
    _rule("SHARED CATALOG (must carry NO user_id — rules #10/#17)")
    leaks = 0
    for table in SHARED_TABLES:
        if not _table_exists(cur, table):
            print(f"  {table:<22} (absent)")
            continue
        cur.execute(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = %s "
            "AND column_name IN ('user_id', 'tenant_id')",
            (table,),
        )
        owned = int(cur.fetchone()[0])
        rows = _count(cur, f"SELECT count(*) FROM {table}")  # noqa: S608
        verdict = "OK (shared, unowned)" if owned == 0 else "LEAK — user column present"
        leaks += owned
        print(f"  {table:<22}{rows:>9} rows   {verdict}")

    _rule("PER-USER TABLES — ownership and orphans")
    print(f"  {'table':<30}{'rows':>8}{'owners':>8}{'orphans':>9}{'null owner':>12}")
    orphans_total = 0
    for table, col in PER_USER_TABLES:
        if not _table_exists(cur, table):
            continue
        rows = _count(cur, f"SELECT count(*) FROM {table}")  # noqa: S608
        owners = _count(cur, f"SELECT count(DISTINCT {col}) FROM {table}")  # noqa: S608
        # An orphan is a row whose owner no longer exists — invisible to the app
        # and impossible to delete on request.
        orphans = _count(
            cur,
            f"SELECT count(*) FROM {table} t WHERE t.{col} IS NOT NULL "  # noqa: S608
            f"AND NOT EXISTS (SELECT 1 FROM users u WHERE u.id = t.{col})",
        )
        nulls = _count(cur, f"SELECT count(*) FROM {table} WHERE {col} IS NULL")  # noqa: S608
        orphans_total += orphans + nulls
        mark = "  <--" if (orphans or nulls) else ""
        print(f"  {table:<30}{rows:>8}{owners:>8}{orphans:>9}{nulls:>12}{mark}")

    _rule("PROVENANCE — can each artifact name the profile that made it?")
    for table in ("user_feed", "tailored_documents"):
        if not _table_exists(cur, table):
            continue
        cur.execute(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = %s "
            "AND column_name = 'profile_version'",
            (table,),
        )
        if not int(cur.fetchone()[0]):
            print(f"  {table:<26} no profile_version column")
            continue
        total = _count(cur, f"SELECT count(*) FROM {table}")  # noqa: S608
        stamped = _count(cur, f"SELECT count(profile_version) FROM {table}")  # noqa: S608
        pct = round(100 * stamped / total) if total else 0
        mark = "" if pct == 100 or total == 0 else "   <-- untraceable rows"
        print(f"  {table:<26}{stamped:>7} / {total:<7} stamped ({pct}%){mark}")

    _rule("REFERENTIAL BACKSTOP")
    fks = _count(
        cur,
        "SELECT count(*) FROM information_schema.table_constraints "
        "WHERE constraint_type = 'FOREIGN KEY' AND table_schema = current_schema()",
    )
    print(f"  foreign keys enforced by the database: {fks}")
    if fks == 0:
        print("  NOTE: the psycopg shim strips every REFERENCES/ON DELETE CASCADE,")
        print("  so isolation rests entirely on application code. The orphan counts")
        print("  above are therefore the ONLY evidence that it is holding.")

    print(
        f"\nVERDICT: {leaks} shared-table ownership leak(s), "
        f"{orphans_total} orphaned/unowned row(s)."
    )
    return 0 if (leaks == 0 and orphans_total == 0) else 2


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--user", metavar="EMAIL", help="everything stored for one person")
    g.add_argument("--system", action="store_true", help="integrity across all tables")
    args = ap.parse_args(argv)

    dsn = os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        print("no DATABASE_PUBLIC_URL / DATABASE_URL in the environment", file=sys.stderr)
        return 1

    import psycopg  # noqa: PLC0415 — keeps `--help` usable without the driver

    with psycopg.connect(dsn) as conn:
        # Belt and braces: the queries are SELECT-only, and the session is
        # read-only so a future edit cannot turn this into a writer by accident.
        conn.read_only = True
        with conn.cursor() as cur:
            if args.user:
                return observe_user(cur, args.user)
            return observe_system(cur)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
