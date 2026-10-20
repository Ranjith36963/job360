#!/usr/bin/env python3
"""Backup checks used by .github/workflows/db-backup.yml (gap 7, G410).

Two questions the nightly dump could not answer before:

  compare    Did the restored copy keep every row the source had when the dump
             started?  Source counts are taken with psql just BEFORE pg_dump;
             writes during the dump can only ADD rows, so restored >= source is
             the pass rule.  A smaller restored count (or a missing table) is a
             backup that lost data and fails the job.
  freshness  Is the newest nightly object in R2 younger than 36 h?  Run from its
             own cron, so a nightly job that stopped (or never uploaded) turns
             the workflow red instead of staying silent.

Pure standard library; no secrets or connection strings ever pass through here.
The files and listings it reads hold only table names, counts, object keys and
timestamps.

  python scripts/backup_checks.py compare SOURCE_COUNTS RESTORED_COUNTS
  python scripts/backup_checks.py freshness LISTING [--max-hours 36]

A counts file has one "table count" pair per line.  A LISTING is the output of
  aws s3api list-objects-v2 --prefix job360- \
      --query 'Contents[].[Key,LastModified]' --output text
(tab-separated key and ISO timestamp).  Exit 0 = ok, 1 = check failed.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

NIGHTLY_KEY = re.compile(r"^job360-\d{8}T\d{6}Z\.sql\.gz\.gpg$")


def parse_counts(text: str) -> dict[str, int]:
    """'table count' lines -> {table: count}.  Blank lines are skipped."""
    counts: dict[str, int] = {}
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        if len(parts) != 2 or not parts[1].isdigit():
            raise ValueError(f"bad counts line: {line!r}")
        counts[parts[0]] = int(parts[1])
    return counts


def compare_counts(source: dict[str, int], restored: dict[str, int]) -> tuple[list[tuple], bool]:
    """Rows of (table, source, restored, verdict) and whether all passed."""
    rows: list[tuple] = []
    ok = True
    for table in sorted(source):
        src = source[table]
        got = restored.get(table)
        if got is None:
            verdict = "MISSING in restore"
            ok = False
        elif got < src:
            verdict = f"LOST {src - got} rows"
            ok = False
        elif got > src:
            verdict = f"ok (+{got - src} written during dump)"
        else:
            verdict = "ok"
        rows.append((table, src, "-" if got is None else got, verdict))
    if not source:
        ok = False  # an empty source list would pass vacuously: refuse
    return rows, ok


def render_counts(rows: list[tuple], ok: bool) -> str:
    head = "Row counts match the source" if ok else "ROW COUNT MISMATCH - backup is NOT trustworthy"
    lines = [f"## {head}", "", "| table | source | restored | result |", "|---|---|---|---|"]
    lines += [f"| {t} | {s} | {r} | {v} |" for t, s, r, v in rows]
    return "\n".join(lines) + "\n"


def _parse_ts(raw: str) -> datetime:
    dt = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def newest_nightly(listing: str) -> tuple[str, datetime] | None:
    """The (key, LastModified) of the newest nightly object, or None."""
    best: tuple[str, datetime] | None = None
    for line in listing.splitlines():
        parts = line.split("\t")
        if len(parts) != 2 or not NIGHTLY_KEY.match(parts[0].strip()):
            continue  # "None" (empty bucket), other prefixes, stray lines
        stamp = _parse_ts(parts[1])
        if best is None or stamp > best[1]:
            best = (parts[0].strip(), stamp)
    return best


def check_freshness(listing: str, now: datetime, max_hours: float) -> tuple[bool, str]:
    newest = newest_nightly(listing)
    if newest is None:
        return False, "no nightly backup object found in the bucket"
    key, stamp = newest
    age = (now - stamp).total_seconds() / 3600
    msg = f"newest nightly backup {key} is {age:.1f} h old (limit {max_hours:g} h)"
    return age <= max_hours, msg


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("compare")
    c.add_argument("source")
    c.add_argument("restored")
    f = sub.add_parser("freshness")
    f.add_argument("listing")
    f.add_argument("--max-hours", type=float, default=36.0)
    args = ap.parse_args(argv)

    if args.cmd == "compare":
        rows, ok = compare_counts(
            parse_counts(Path(args.source).read_text(encoding="utf-8")),
            parse_counts(Path(args.restored).read_text(encoding="utf-8")),
        )
        sys.stdout.write(render_counts(rows, ok))
        return 0 if ok else 1

    ok, msg = check_freshness(
        Path(args.listing).read_text(encoding="utf-8"), datetime.now(timezone.utc), args.max_hours
    )
    print(("OK: " if ok else "STALE: ") + msg)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
