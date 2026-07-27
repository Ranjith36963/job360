#!/usr/bin/env python3
"""ABSENCE detector — asks "did something STOP?", not "did something break?".

Every other check in this repo is a PRESENCE detector: it fires when an error
happens, a test fails, or a probe returns non-2xx. None of them can see a
process that simply stopped running — and that is exactly the failure that hurt
most (issue #142):

    Measured in prod 2026-07-27: nothing fetched jobs on a timer, so the shared
    catalog drained via the 30-day purge. run_log held 11 rows in 25 days;
    5,827 jobs fetched all-time vs 112 visible; most users saw an empty feed.
    Throughout, EVERY instrument stayed green — 200s, no errors, uptime fine.

Silence is the signal here. The checks below are all of the form "X has not
happened recently enough", which is unobservable to error-driven monitoring.

Deliberately: NO LLM, no writes, read-only SQL. Cost ~0.

Exit 0 = nothing has stopped. Exit 1 = something stopped. Exit 2 = the checker
itself broke (fail LOUD — a checker that dies quietly is the thing it exists to
prevent).

Run locally:  railway run -s Postgres python scripts/absence_check.py
Run in CI:    DATABASE_URL=${{ secrets.PROD_DATABASE_URL }} python scripts/absence_check.py
"""

from __future__ import annotations

import os
import sys

# The report uses arrows/box characters; Windows consoles default to cp1252 and
# a UnicodeEncodeError would make this exit 2 ("checker broke") on a dev machine
# while passing in CI — a portability trap that trains people to distrust it.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── Thresholds. Each answers "how long before absence means STOPPED?" ────────
# The catalog cron runs daily (04:00 UTC), so a healthy run_log is < ~25h old.
# 36h allows one missed run plus slippage before crying wolf.
# Env-overridable so a CANARY run (issue #143) can set an impossible threshold
# and assert this detector actually goes red. A detector that has never been
# seen to fail is not known to work — "green" might mean blind.
MAX_RUN_LOG_AGE_HOURS = float(os.environ.get("ABSENCE_MAX_RUN_LOG_AGE_H", "36"))
# Newest job in the catalog. Fetching can "succeed" and still return nothing if
# every source breaks at once, so age-of-newest-row is checked separately from
# whether a run happened at all.
MAX_NEWEST_JOB_AGE_HOURS = float(os.environ.get("ABSENCE_MAX_JOB_AGE_H", "48"))
# Below this the catalog is draining toward the empty feed that started all
# this. 112 was the measured broken state; 200 is a floor that would have caught
# it while there was still time.
MIN_CATALOG_JOBS = int(os.environ.get("ABSENCE_MIN_JOBS", "200"))


def _dsn() -> str:
    """Prefer an explicitly-supplied prod DSN; never print it."""
    for var in ("PROD_DATABASE_URL", "DATABASE_PUBLIC_URL", "DATABASE_URL"):
        val = os.environ.get(var, "").strip()
        if val:
            return val
    # Railway's Postgres service exposes the parts separately.
    dom = os.environ.get("RAILWAY_TCP_PROXY_DOMAIN", "")
    port = os.environ.get("RAILWAY_TCP_PROXY_PORT", "")
    pw = os.environ.get("PGPASSWORD") or os.environ.get("POSTGRES_PASSWORD", "")
    user = os.environ.get("PGUSER") or os.environ.get("POSTGRES_USER", "postgres")
    db = os.environ.get("PGDATABASE") or os.environ.get("POSTGRES_DB", "railway")
    if dom and port and pw:
        return f"postgresql://{user}:{pw}@{dom}:{port}/{db}"
    return ""


def main() -> int:
    dsn = _dsn()
    if not dsn:
        print("NO DATABASE URL in env — cannot check for absence.")
        print("Set PROD_DATABASE_URL (CI) or run via `railway run -s Postgres`.")
        return 2

    import psycopg  # noqa: PLC0415 — keep import cost off the no-DSN path

    stopped: list[str] = []
    rows: list[tuple[str, str, str]] = []  # (check, value, verdict)

    with psycopg.connect(dsn, connect_timeout=15, autocommit=True) as conn:

        def scalar(sql: str):
            cur = conn.execute(sql)
            row = cur.fetchone()
            return row[0] if row else None

        # 1) Has the pipeline run at all recently?
        age_h = scalar(
            "SELECT EXTRACT(EPOCH FROM (now() - MAX(timestamp::timestamptz)))/3600 "
            "FROM run_log"
        )
        if age_h is None:
            stopped.append("run_log is EMPTY — the pipeline has never run")
            rows.append(("last pipeline run", "never", "STOPPED"))
        else:
            age_h = float(age_h)
            bad = age_h > MAX_RUN_LOG_AGE_HOURS
            rows.append(
                (
                    "last pipeline run",
                    f"{age_h:.1f}h ago",
                    "STOPPED" if bad else "ok",
                )
            )
            if bad:
                stopped.append(
                    f"no pipeline run for {age_h:.1f}h "
                    f"(limit {MAX_RUN_LOG_AGE_HOURS:.0f}h) — the catalog cron is not running"
                )

        # 2) Is the catalog still being fed? (a run can succeed and fetch nothing)
        job_age_h = scalar(
            "SELECT EXTRACT(EPOCH FROM (now() - MAX(first_seen_at::timestamptz)))/3600 "
            "FROM jobs WHERE first_seen_at IS NOT NULL"
        )
        if job_age_h is None:
            stopped.append("no job in the catalog has a first_seen_at — nothing is being ingested")
            rows.append(("newest job", "none", "STOPPED"))
        else:
            job_age_h = float(job_age_h)
            bad = job_age_h > MAX_NEWEST_JOB_AGE_HOURS
            rows.append(("newest job", f"{job_age_h:.1f}h old", "STOPPED" if bad else "ok"))
            if bad:
                stopped.append(
                    f"newest job is {job_age_h:.1f}h old "
                    f"(limit {MAX_NEWEST_JOB_AGE_HOURS:.0f}h) — fetching has stopped"
                )

        # 3) Is the catalog draining toward empty?
        total = int(scalar("SELECT COUNT(*) FROM jobs") or 0)
        bad = total < MIN_CATALOG_JOBS
        rows.append(("jobs in catalog", str(total), "DRAINING" if bad else "ok"))
        if bad:
            stopped.append(
                f"only {total} jobs in the catalog (floor {MIN_CATALOG_JOBS}) — "
                "purge is outrunning ingest; users will see empty feeds"
            )

        # 4) Context, never a failure on its own: how many users have nothing?
        try:
            empty_users = int(
                scalar(
                    "SELECT COUNT(*) FROM users u WHERE u.deleted_at IS NULL AND NOT EXISTS "
                    "(SELECT 1 FROM user_feed f WHERE f.user_id = u.id)"
                )
                or 0
            )
            rows.append(("users with an empty feed", str(empty_users), "info"))
        except Exception as exc:  # noqa: BLE001 — context only, must never fail the check
            rows.append(("users with an empty feed", f"unavailable ({type(exc).__name__})", "info"))

    print("# Absence check — did anything STOP?\n")
    print("| check | value | verdict |")
    print("|---|---|---|")
    for name, value, verdict in rows:
        mark = {"ok": "ok", "info": "—"}.get(verdict, f"**{verdict}**")
        print(f"| {name} | {value} | {mark} |")

    if stopped:
        print("\n## Something stopped\n")
        for s in stopped:
            print(f"- {s}")
        print(
            "\nThis is an ABSENCE failure: no error was thrown and no test failed, "
            "so nothing else in the system can see it."
        )
        return 1

    print("\nNothing has stopped — the pipeline ran, the catalog is being fed, and it is not draining.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 — a checker must fail LOUD, never silently green
        print(f"absence_check crashed: {type(exc).__name__}: {exc}")
        sys.exit(2)
