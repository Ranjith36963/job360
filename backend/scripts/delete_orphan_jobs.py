"""Delete orphan `jobs` rows — catalog rows nothing references any more.

Production's `jobs` table still holds ~19k rows scraped by the sourcing era
(deleted in slice 5, #483). Today a row is only born by `bring_job`, and every
brought row is referenced by the application born in the same request. A row
that NO table points at is dead weight: no user can reach it.

ORPHAN = a `jobs` row whose id appears in no `job_id` column of any other
table in the schema. The referencing tables are DISCOVERED from
information_schema at run time (every column literally named `job_id`), so a
legacy table still present in production is honoured automatically. On the
migrated schema (head 0059) that is:

    applications.job_id               the per-user Application (spine)
    application_receipts.job_id       frozen receipts (they also COPY the ad)
    application_stage_history.job_id  stage transitions
    tailored_documents.job_id         tailored CV / cover letter
    tailored_usage.job_id             tailoring rate-limit ledger

No JSON / JSONB column stores a job id (checked 2026-10-14: the only jsonb
column is jobs.shelf_provenance; `"job_id"` in backend/src appears only in API
replies and log lines). There are no FOREIGN KEYs onto jobs.id.

A row first/last seen within --min-age-days (default 1) is never deleted, so a
`bring_job` caught between "insert the job" and "birth the application" is
safe. The DELETE re-checks the orphan predicate itself, so a row that gains a
reference between the SELECT and the DELETE is kept.

SAFETY
  * Default is --dry-run: counts only, nothing written.
  * Deleting needs BOTH --execute AND --i-have-a-backup.
  * Batches of 500 ids, each batch its own transaction. Idempotent: a second
    run finds nothing to delete.
  * Output is counts, sources and dates only — never a title, company or
    description.

PRODUCTION RUNBOOK (run from the repo root; `railway run -s Postgres` injects
DATABASE_PUBLIC_URL — plain DATABASE_URL only resolves inside Railway):

  1. Back up the table (pg_dump must be >= the server's major version):

       railway run -s Postgres sh -c 'pg_dump "$DATABASE_PUBLIC_URL" \\
           --table=public.jobs --format=custom \\
           --file=jobs_backup_$(date +%Y%m%d_%H%M).dump'

     Check it: pg_restore --list jobs_backup_*.dump | head

  2. Dry run (read-only):

       railway run -s Postgres python backend/scripts/delete_orphan_jobs.py

  3. Delete:

       railway run -s Postgres python backend/scripts/delete_orphan_jobs.py \\
           --execute --i-have-a-backup

  Restore (if ever needed) — re-inserts only rows whose id is missing:
       pg_restore --data-only --table=jobs ... into a scratch schema, then
       INSERT INTO public.jobs SELECT ... WHERE id NOT IN (SELECT id FROM public.jobs).
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

BATCH_SIZE = 500


def referencing_tables(cur: Any) -> list[str]:
    """Every table (other than `jobs`) in the current schema with a `job_id` column."""
    cur.execute(
        "SELECT DISTINCT table_name FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND column_name = 'job_id' "
        "AND table_name <> 'jobs' ORDER BY table_name"
    )
    return [r[0] for r in cur.fetchall()]


def _orphan_predicate(tables: list[str]) -> Any:
    """`NOT EXISTS (...) AND NOT EXISTS (...)` over every referencing table."""
    from psycopg import sql  # noqa: PLC0415

    if not tables:
        return sql.SQL("TRUE")
    return sql.SQL(" AND ").join(
        sql.SQL("NOT EXISTS (SELECT 1 FROM {} r WHERE r.job_id = j.id)").format(sql.Identifier(t))
        for t in tables
    )


def _cutoff(min_age_days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=min_age_days)).isoformat()


def _age_predicate() -> Any:
    """A row touched after the cutoff is too young to judge (TEXT ISO timestamps)."""
    from psycopg import sql  # noqa: PLC0415

    return sql.SQL("COALESCE(j.last_seen_at, j.first_seen_at, j.first_seen, '') < %(cutoff)s")


def summarize(cur: Any, *, min_age_days: int = 1) -> dict[str, Any]:
    """Counts only. Never reads title/company/description."""
    from psycopg import sql  # noqa: PLC0415

    tables = referencing_tables(cur)
    orphan = _orphan_predicate(tables)
    params = {"cutoff": _cutoff(min_age_days)}

    cur.execute("SELECT COUNT(*) FROM jobs")
    total = int(cur.fetchone()[0])
    cur.execute(sql.SQL("SELECT COUNT(*) FROM jobs j WHERE NOT ({})").format(orphan))
    referenced = int(cur.fetchone()[0])
    cur.execute(
        sql.SQL("SELECT COUNT(*), MIN(j.first_seen), MAX(j.first_seen) FROM jobs j WHERE {} AND {}").format(
            orphan, _age_predicate()
        ),
        params,
    )
    n, oldest, newest = cur.fetchone()
    cur.execute(
        sql.SQL(
            "SELECT j.source, COUNT(*) FROM jobs j WHERE {} AND {} GROUP BY j.source ORDER BY 2 DESC, 1"
        ).format(orphan, _age_predicate()),
        params,
    )
    by_source = [(str(s), int(c)) for s, c in cur.fetchall()]
    return {
        "referencing_tables": tables,
        "total": total,
        "referenced": referenced,
        "orphans": int(n),
        "too_young": total - referenced - int(n),
        "by_source": by_source,
        "oldest_orphan": oldest,
        "newest_orphan": newest,
    }


def delete_orphans(conn: Any, *, min_age_days: int = 1, batch_size: int = BATCH_SIZE) -> int:
    """Delete orphans in batches, one transaction per batch. Returns rows deleted."""
    from psycopg import sql  # noqa: PLC0415

    with conn.cursor() as cur:
        tables = referencing_tables(cur)
    orphan = _orphan_predicate(tables)
    params: dict[str, Any] = {"cutoff": _cutoff(min_age_days), "limit": batch_size}
    select_q = sql.SQL("SELECT j.id FROM jobs j WHERE {} AND {} ORDER BY j.id LIMIT %(limit)s").format(
        orphan, _age_predicate()
    )
    # The predicate is repeated in the DELETE: a row that gained a reference
    # since the SELECT is kept.
    delete_q = sql.SQL("DELETE FROM jobs j WHERE j.id = ANY(%(ids)s) AND {} AND {}").format(
        orphan, _age_predicate()
    )
    deleted = 0
    batch_no = 0
    while True:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(select_q, params)
                ids = [int(r[0]) for r in cur.fetchall()]
                if not ids:
                    break
                cur.execute(delete_q, {**params, "ids": ids})
                n = cur.rowcount
        batch_no += 1
        deleted += n
        print(f"batch {batch_no}: deleted {n} (total {deleted})", flush=True)
        if n == 0:  # every candidate gained a reference mid-run; nothing left to do
            break
    return deleted


def render(summary: dict[str, Any]) -> str:
    lines = [
        f"referencing tables : {', '.join(summary['referencing_tables']) or '(none)'}",
        f"total jobs         : {summary['total']}",
        f"referenced jobs    : {summary['referenced']}",
        f"orphan jobs        : {summary['orphans']}",
        f"too young to judge : {summary['too_young']}",
        f"oldest orphan      : {summary['oldest_orphan'] or '-'}",
        f"newest orphan      : {summary['newest_orphan'] or '-'}",
        "orphans by source  :",
    ]
    lines += [f"  {src:<24} {n}" for src, n in summary["by_source"]] or ["  (none)"]
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None, *, conn: Any = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="report counts only (the default)")
    ap.add_argument("--execute", action="store_true", help="actually delete (needs --i-have-a-backup)")
    ap.add_argument("--i-have-a-backup", action="store_true", dest="have_backup",
                    help="confirm a pg_dump of `jobs` was taken (see the docstring)")
    ap.add_argument("--min-age-days", type=int, default=1,
                    help="never delete a row seen within this many days (default 1)")
    args = ap.parse_args(argv)

    if args.execute and args.dry_run:
        print("--execute and --dry-run are mutually exclusive", file=sys.stderr)
        return 2
    if args.execute and not args.have_backup:
        print("refusing to delete: --execute needs --i-have-a-backup "
              "(take the pg_dump in this script's docstring first)", file=sys.stderr)
        return 2
    if args.min_age_days < 0:
        print("--min-age-days must be >= 0", file=sys.stderr)
        return 2

    own = conn is None
    if own:
        dsn = os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL")
        if not dsn:
            print("no DATABASE_PUBLIC_URL / DATABASE_URL in the environment", file=sys.stderr)
            return 1
        import psycopg  # noqa: PLC0415 — keeps `--help` usable without the driver

        conn = psycopg.connect(dsn, autocommit=True)
    try:
        with conn.cursor() as cur:
            before = summarize(cur, min_age_days=args.min_age_days)
        print("mode: " + ("EXECUTE" if args.execute else "dry-run (nothing written)"))
        print(render(before))
        if not args.execute:
            return 0
        deleted = delete_orphans(conn, min_age_days=args.min_age_days)
        with conn.cursor() as cur:
            after = summarize(cur, min_age_days=args.min_age_days)
        print(f"deleted {deleted}; jobs now {after['total']} "
              f"(referenced {after['referenced']}, orphans left {after['orphans']})")
        return 0
    finally:
        if own:
            conn.close()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
