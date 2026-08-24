"""Read-only prod check: what delivery channels do real users actually have?

Run: railway run -s Postgres python check_prod_channels.py
Uses DATABASE_PUBLIC_URL (plain DATABASE_URL only resolves inside Railway).
Prints COUNTS ONLY - never credential values.
"""
from __future__ import annotations

import os
import sys

try:
    import psycopg
except ImportError:  # pragma: no cover
    print("psycopg not installed in this env")
    sys.exit(1)

dsn = os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL")
if not dsn:
    print("NO DSN: neither DATABASE_PUBLIC_URL nor DATABASE_URL is set")
    sys.exit(1)

QUERIES = [
    (
        "user_channels by type",
        "SELECT channel_type, COUNT(*) AS n, COUNT(DISTINCT user_id) AS users, "
        "SUM(enabled::int) AS enabled_n "
        "FROM user_channels GROUP BY channel_type ORDER BY n DESC",
    ),
    (
        "pending digests by channel",
        "SELECT channel, COUNT(*) AS n FROM user_notification_digests "
        "WHERE sent = 0 GROUP BY channel ORDER BY n DESC",
    ),
    (
        "notification_ledger by channel",
        "SELECT channel, COUNT(*) AS n FROM notification_ledger GROUP BY channel ORDER BY n DESC",
    ),
    (
        "notification_rules modes",
        "SELECT notify_mode, COUNT(*) AS n FROM notification_rules GROUP BY notify_mode",
    ),
    ("total users", "SELECT COUNT(*) AS n FROM users"),
    (
        "oauth_states rows",
        "SELECT COUNT(*) AS n FROM oauth_states",
    ),
]

with psycopg.connect(dsn) as conn:
    for label, sql in QUERIES:
        print(f"\n=== {label} ===")
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
                rows = cur.fetchall()
                cols = [d.name for d in cur.description] if cur.description else []
                if not rows:
                    print("(no rows)")
                    continue
                print(" | ".join(cols))
                for r in rows:
                    print(" | ".join(str(v) for v in r))
        except Exception as exc:  # noqa: BLE001 - diagnostic script
            conn.rollback()
            print(f"ERROR: {type(exc).__name__}: {exc}")
