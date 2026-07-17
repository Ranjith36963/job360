"""Dev helper: dump useful summaries from the Job360 SQLite DB.

Usage:
    python backend/scripts/dump_db.py
    python backend/scripts/dump_db.py --db-path /tmp/test.db
    python backend/scripts/dump_db.py --user alice@example.com

Prints:
    * Tables present + row counts
    * Latest 5 run_log rows (with run_uuid + per_source_errors if non-empty)
    * Top 10 recently-seen jobs by match_score
    * If --user given and found: their recent user_feed + user_actions rows
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Ensure backend/ is on sys.path so `src.*` resolves when run as a script.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.repositories import pgsync  # noqa: E402

DEFAULT_DB = Path(__file__).resolve().parents[1] / "data" / "jobs.db"

# Lazy rich import — fall back to plain print if unavailable.
try:  # pragma: no cover - decorative path
    from rich.console import Console
    from rich.table import Table

    _CONSOLE: Console | None = Console()
except Exception:  # pragma: no cover - decorative path
    _CONSOLE = None
    Table = None  # type: ignore[assignment]


def _say(msg: str) -> None:
    if _CONSOLE is not None:
        _CONSOLE.print(msg)
    else:
        print(msg)


def _print_rows(title: str, columns: list[str], rows: list[tuple]) -> None:
    if _CONSOLE is not None and Table is not None:
        t = Table(title=title, show_lines=False)
        for c in columns:
            t.add_column(c, overflow="fold")
        for row in rows:
            t.add_row(*[("" if v is None else str(v)) for v in row])
        _CONSOLE.print(t)
    else:
        print(f"\n== {title} ==")
        print("\t".join(columns))
        for row in rows:
            print("\t".join(("" if v is None else str(v)) for v in row))


def _table_counts(conn: pgsync.Connection) -> list[tuple[str, int]]:
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
    out: list[tuple[str, int]] = []
    for (name,) in cur.fetchall():
        try:
            n = conn.execute(f"SELECT COUNT(*) FROM '{name}'").fetchone()[0]
        except pgsync.Error as e:
            n = -1
            _say(f"[warn] count failed for {name}: {e}")
        out.append((name, n))
    return out


def _latest_runs(conn: pgsync.Connection, limit: int = 5) -> list[tuple]:
    cur = conn.execute("PRAGMA table_info(run_log)")
    cols = {row[1] for row in cur.fetchall()}
    has_uuid = "run_uuid" in cols
    has_errs = "per_source_errors" in cols
    select = ["id", "timestamp", "total_found", "new_jobs", "sources_queried"]
    if has_uuid:
        select.append("run_uuid")
    if has_errs:
        select.append("per_source_errors")
    q = f"SELECT {', '.join(select)} FROM run_log ORDER BY id DESC LIMIT ?"
    rows = conn.execute(q, (limit,)).fetchall()
    formatted: list[tuple] = []
    for row in rows:
        row = list(row)
        if has_errs:
            raw = row[-1]
            if raw:
                try:
                    parsed = json.loads(raw)
                    # Only keep non-empty error entries.
                    non_empty = {k: v for k, v in parsed.items() if v}
                    row[-1] = json.dumps(non_empty) if non_empty else ""
                except Exception:
                    row[-1] = str(raw)[:80]
            else:
                row[-1] = ""
        formatted.append(tuple(row))
    return select, formatted  # type: ignore[return-value]


def _top_jobs(conn: pgsync.Connection, limit: int = 10) -> list[tuple]:
    q = (
        "SELECT id, match_score, title, company, location, first_seen"
        " FROM jobs ORDER BY first_seen DESC, match_score DESC LIMIT ?"
    )
    return conn.execute(q, (limit,)).fetchall()


def _user_id_for_email(conn: pgsync.Connection, email: str) -> str | None:
    try:
        row = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
    except pgsync.Error as e:
        _say(f"[warn] users lookup failed: {e}")
        return None
    return row[0] if row else None


def _user_feed(conn: pgsync.Connection, user_id: str, limit: int = 10) -> list[tuple]:
    try:
        return conn.execute(
            "SELECT job_id, status, score, notified_at FROM user_feed"
            " WHERE user_id = ? ORDER BY notified_at DESC NULLS LAST LIMIT ?",
            (user_id, limit),
        ).fetchall()
    except pgsync.Error:
        return conn.execute(
            "SELECT job_id, status, score, notified_at FROM user_feed" " WHERE user_id = ? ORDER BY rowid DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()


def _user_actions(conn: pgsync.Connection, user_id: str, limit: int = 10) -> list[tuple]:
    return conn.execute(
        "SELECT job_id, action, created_at FROM user_actions" " WHERE user_id = ? ORDER BY rowid DESC LIMIT ?",
        (user_id, limit),
    ).fetchall()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db-path", default=str(DEFAULT_DB))
    p.add_argument("--user", help="Email of user to dump feed/actions for")
    args = p.parse_args()

    db_path = Path(args.db_path)
    if not db_path.exists():
        _say(f"ERROR: DB not found: {db_path}")
        return 1

    conn = pgsync.connect(str(db_path))
    try:
        counts = _table_counts(conn)
        _print_rows("Tables", ["table", "rows"], counts)

        cols, runs = _latest_runs(conn)  # type: ignore[assignment]
        _print_rows("Latest 5 run_log rows", cols, runs)

        jobs = _top_jobs(conn)
        _print_rows(
            "Top 10 recent jobs (first_seen DESC, match_score DESC)",
            ["id", "match_score", "title", "company", "location", "first_seen"],
            jobs,
        )

        if args.user:
            uid = _user_id_for_email(conn, args.user)
            if uid is None:
                _say(f"No user found for email: {args.user}")
            else:
                _say(f"Resolved user {args.user} -> id {uid}")
                feed = _user_feed(conn, uid)
                _print_rows(
                    f"user_feed for {args.user}",
                    ["job_id", "status", "score", "notified_at"],
                    feed,
                )
                actions = _user_actions(conn, uid)
                _print_rows(
                    f"user_actions for {args.user}",
                    ["job_id", "action", "created_at"],
                    actions,
                )
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
