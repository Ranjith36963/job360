"""Dev helper: dump useful summaries from the Job360 DB.

(`run_log` went with the sourcing era — migration 0039, slice 5 #483 — and
`user_feed` / `user_actions` went with the notification/pipeline tables —
migration 0040 — so the run summary and the per-user feed/actions dump this
script used to print are both gone with them.)

Usage:
    python backend/scripts/dump_db.py
    python backend/scripts/dump_db.py --db-path /tmp/test.db

Prints:
    * Tables present + row counts
    * Top 10 recently-seen jobs by match_score
"""

from __future__ import annotations

import argparse
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


def _top_jobs(conn: pgsync.Connection, limit: int = 10) -> list[tuple]:
    q = (
        "SELECT id, match_score, title, company, location, first_seen"
        " FROM jobs ORDER BY first_seen DESC, match_score DESC LIMIT ?"
    )
    return conn.execute(q, (limit,)).fetchall()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db-path", default=str(DEFAULT_DB))
    args = p.parse_args()

    db_path = Path(args.db_path)
    if not db_path.exists():
        _say(f"ERROR: DB not found: {db_path}")
        return 1

    conn = pgsync.connect(str(db_path))
    try:
        counts = _table_counts(conn)
        _print_rows("Tables", ["table", "rows"], counts)

        jobs = _top_jobs(conn)
        _print_rows(
            "Top 10 recent jobs (first_seen DESC, match_score DESC)",
            ["id", "match_score", "title", "company", "location", "first_seen"],
            jobs,
        )
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
