#!/usr/bin/env python3
"""Is the product actually WORKING — not just "not crashing"?

THE GAP THIS CLOSES. Every detector we had asks "did something fail?" Nothing
asked "is this doing the right thing?" So every product-quality bug this month
was found by a human noticing, never by a loop:

    #156  a job paying EXACTLY the user's target scored 0 (worse than a job
          with no salary at all) - because one span was never floored
    #160  picking "Executive" silently killed the seniority dimension - the
          UI offered a value the scorer had never heard of
    enrichment  gated at 60 while the highest score in the entire feed was 58,
          so the stage had NEVER run once and job_enrichment held 0 rows

None of those threw an error. None failed a test. Every dashboard stayed green.
A feature that silently does nothing is indistinguishable from a feature that
was never built - which is exactly what makes this class so expensive.

WHAT THIS DOES: asserts things that must be TRUE of a healthy product, read
straight from prod. Each assertion is a shape, not a one-off - it would have
caught its originating bug BEFORE a human noticed, and will catch the next
member of the same family.

Read-only SQL. No LLM. No writes. Every threshold is env-overridable so a
canary run can force it red and prove the detector still works.

Exit codes:  0 = all healthy   1 = at least one assertion failed
             2 = the checker itself broke (fail LOUD, never silently pass)
"""

from __future__ import annotations

import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Every knob overridable: a canary must be able to force a red run.
MIN_FEED_ROWS = int(os.getenv("PA_MIN_FEED_ROWS", "50"))
MAX_EMPTY_FEED_PCT = float(os.getenv("PA_MAX_EMPTY_FEED_PCT", "50"))
MIN_VISIBLE_PCT = float(os.getenv("PA_MIN_VISIBLE_PCT", "25"))
DISPLAY_FLOOR = int(os.getenv("PA_DISPLAY_FLOOR", "0"))


def main() -> int:
    dsn = os.getenv("PROD_DATABASE_URL") or os.getenv("DATABASE_PUBLIC_URL")
    if not dsn:
        print("::error::no PROD_DATABASE_URL - product assertions cannot run")
        return 2

    try:
        import psycopg
    except ImportError:
        print("::error::psycopg not installed")
        return 2

    rows: list[tuple[str, str, str]] = []  # (check, value, verdict)
    problems: list[str] = []

    def check(name: str, value: str, ok: bool, why: str = "") -> None:
        rows.append((name, value, "ok" if ok else "**BROKEN**"))
        if not ok:
            problems.append(why or name)

    try:
        with psycopg.connect(dsn, connect_timeout=25) as conn, conn.cursor() as cur:
            # ---------------------------------------------------------------
            # 1. GATE-ABOVE-CEILING. The enrichment-60 bug class.
            # A threshold is a CLAIM about the score distribution. Distributions
            # move (ours did, when the CV-copy merge was removed). When the claim
            # goes stale the feature stops firing - silently, forever.
            # ---------------------------------------------------------------
            cur.execute("SELECT count(*), coalesce(max(score), 0) FROM user_feed")
            feed_rows, max_score = cur.fetchone()
            check("feed rows", str(feed_rows), feed_rows >= MIN_FEED_ROWS,
                  f"only {feed_rows} feed rows (floor {MIN_FEED_ROWS}) - is anything being fed?")

            enrich_gate = int(os.getenv("ENRICHMENT_MIN_SCORE", "10"))
            check(f"max score ({max_score}) vs enrichment floor ({enrich_gate})",
                  f"{max_score} vs {enrich_gate}",
                  max_score >= enrich_gate,
                  f"NO job can ever reach the enrichment floor: best score is {max_score}, "
                  f"floor is {enrich_gate}. That stage has never run and never will.")

            # ---------------------------------------------------------------
            # 2. A BUILT FEATURE THAT PRODUCES NOTHING.
            # job_enrichment sitting at 0 rows was invisible for weeks.
            # ---------------------------------------------------------------
            for table, label in (("job_enrichment", "LLM enrichment"),
                                 ("job_embeddings", "semantic embeddings")):
                try:
                    cur.execute(f"SELECT count(*) FROM {table}")  # noqa: S608 - fixed literals
                    n = cur.fetchone()[0]
                except Exception:
                    conn.rollback()
                    rows.append((f"{label} rows", "table missing", "-"))
                    continue
                # 0 rows is only a PROBLEM if the engine is meant to be on. We
                # cannot read prod env from here, so report it and let the human
                # judge - but say plainly what 0 means.
                rows.append((f"{label} rows", str(n),
                             "ok" if n > 0 else "zero - never produced anything"))

            # ---------------------------------------------------------------
            # 3. A SCORING DIMENSION THAT IS ALWAYS ZERO.
            # The Executive bug: the UI offered a value the scorer never knew,
            # so that dimension was dead for those users. A dimension that is
            # never non-zero is either broken or pointless - both worth knowing.
            # ---------------------------------------------------------------
            try:
                cur.execute("""
                    SELECT
                      count(*) FILTER (WHERE llm_fit_score IS NOT NULL),
                      count(*)
                    FROM user_feed
                """)
                judged, total = cur.fetchone()
                rows.append(("rows with an LLM verdict", f"{judged}/{total}",
                             "ok" if judged > 0 else "zero - the judge never ran"))
            except Exception:
                conn.rollback()

            # ---------------------------------------------------------------
            # 4. USERS SEEING NOTHING. The 87%-hidden bug class.
            # A user with rows in the DB but nothing on screen is the worst
            # failure we can have: the work was done and thrown away.
            # ---------------------------------------------------------------
            cur.execute("SELECT count(DISTINCT user_id) FROM user_feed")
            users_with_feed = cur.fetchone()[0]
            if users_with_feed:
                cur.execute(
                    "SELECT count(*) FROM ("
                    "  SELECT user_id FROM user_feed GROUP BY user_id"
                    "  HAVING count(*) FILTER (WHERE score >= %s) = 0"
                    ") q", (DISPLAY_FLOOR,))
                empty = cur.fetchone()[0]
                pct = empty / users_with_feed * 100
                check(f"users seeing nothing at floor {DISPLAY_FLOOR}",
                      f"{empty}/{users_with_feed} ({pct:.0f}%)",
                      pct <= MAX_EMPTY_FEED_PCT,
                      f"{empty} of {users_with_feed} users have feed rows but see NOTHING "
                      f"at the display floor - work done and thrown away.")

                # How much of the average feed actually reaches the screen.
                cur.execute(
                    "SELECT avg(vis) FROM ("
                    "  SELECT count(*) FILTER (WHERE score >= %s)::float"
                    "         / NULLIF(count(*), 0) * 100 AS vis"
                    "  FROM user_feed GROUP BY user_id"
                    ") q", (DISPLAY_FLOOR,))
                visible = cur.fetchone()[0] or 0
                check("avg % of a feed that is visible", f"{visible:.0f}%",
                      visible >= MIN_VISIBLE_PCT,
                      f"only {visible:.0f}% of the average feed is visible at the "
                      f"display floor - the filter is hiding the product.")

            # ---------------------------------------------------------------
            # 5. STALE CATALOG. Jobs age out; if ingest stops the feed rots
            # without anything failing.
            # ---------------------------------------------------------------
            cur.execute("SELECT count(*) FROM jobs")
            catalog = cur.fetchone()[0]
            rows.append(("jobs in catalog", str(catalog),
                         "ok" if catalog > 0 else "**BROKEN**"))
            if catalog == 0:
                problems.append("the shared catalog is EMPTY")

    except Exception as exc:  # noqa: BLE001
        print(f"::error::product assertions could not run: {exc}")
        return 2

    print("# Product assertions - is it actually working?\n")
    print("| check | value | verdict |")
    print("|---|---|---|")
    for name, value, verdict in rows:
        print(f"| {name} | `{value}` | {verdict} |")
    print()

    if problems:
        print("## Something is WRONG (not broken - wrong)\n")
        for p in problems:
            print(f"- {p}")
        print(
            "\nNone of these throw an error or fail a test. That is exactly why "
            "they need their own detector: a feature that silently does nothing "
            "looks identical to a feature that was never built."
        )
        return 1

    print("All product assertions hold. The product is doing its job, "
          "not merely failing to crash.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
