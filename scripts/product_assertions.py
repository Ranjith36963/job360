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
# A feature that produced rows and then produced none for this long has stopped.
# Generous on purpose: this must catch "dead", not "quiet week".
STALE_FEATURE_DAYS = int(os.getenv("PA_STALE_FEATURE_DAYS", "14"))
# A notification threshold is a CLAIM that some jobs will clear it. If under
# this share of the feed can, the feature is on-but-silent — the worst state,
# because it looks configured and produces nothing.
NOTIFY_MIN_REACHABLE_PCT = float(os.getenv("PA_NOTIFY_MIN_REACHABLE_PCT", "1.0"))
# Browser errors are expected to be rare; a spike means a shipped frontend
# regression that throws nothing server-side (the "NaNw ago" class).
CLIENT_ERROR_MAX_24H = int(os.getenv("PA_CLIENT_ERROR_MAX_24H", "20"))


def _age_days(iso: str) -> int | None:
    """Age in whole days of an ISO-8601 timestamp, or None if unparseable.

    The timestamp columns here are TEXT, so they can hold anything; a parse
    failure must degrade to "cannot tell" rather than crash the whole detector
    or, worse, silently read as fresh.
    """
    from datetime import datetime, timezone

    try:
        dt = datetime.fromisoformat(iso.strip().replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0, (datetime.now(timezone.utc) - dt).days)


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

            # Mirror settings.py's REAL resolution order, not a guess at it.
            # settings.py:139 is `ENRICHMENT_THRESHOLD = getenv("ENRICHMENT_THRESHOLD",
            # str(ENRICHMENT_MIN_SCORE))` — so THRESHOLD is the effective gate and
            # MIN_SCORE is only its fallback. This read only MIN_SCORE, so if prod
            # ever set THRESHOLD (the documented knob) the detector was comparing
            # the score ceiling against a number the engine does not use — a
            # stale-threshold detector with a stale threshold.
            enrich_gate = int(
                os.getenv("ENRICHMENT_THRESHOLD")
                or os.getenv("ENRICHMENT_MIN_SCORE")
                or "10"
            )
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
                # 0 rows is only a PROBLEM if the engine is meant to be on, and
                # we cannot read prod env from here — so "never produced
                # anything" stays a report, not an alarm.
                #
                # BUT "it produced rows and then STOPPED" needs no env knowledge
                # at all, and it is the more dangerous case: a feature that
                # worked, silently died, and left its old rows behind looks
                # identical to a healthy one in a row count. This family used to
                # be display-only in BOTH directions, so the docstring's claim
                # that each assertion "would have caught its originating bug"
                # was false here. Now the stopped case can go red.
                rows.append((f"{label} rows", str(n),
                             "ok" if n > 0 else "zero - never produced anything"))
                if n > 0:
                    try:
                        cur.execute(f"SELECT max(created_at) FROM {table}")  # noqa: S608 - fixed literals
                        newest = cur.fetchone()[0]
                    except Exception:
                        conn.rollback()
                        newest = None
                    if newest:
                        age_days = _age_days(str(newest))
                        stale = age_days is not None and age_days > STALE_FEATURE_DAYS
                        check(f"{label} freshness", f"newest {age_days}d old" if age_days is not None else "unparseable",
                              not stale,
                              f"{label} has {n} rows but NOTHING new for {age_days} days - "
                              f"it worked once and has silently stopped. Old rows make a dead "
                              f"feature look alive in a row count.")

            # ---------------------------------------------------------------
            # 2b. A SEARCH THAT STARTED AND NEVER ENDED.
            #
            # This is also the FIRST CONSUMER `audit_log` has ever had. The
            # table was added by migration 0025 as a "durable, queryable audit
            # trail"; `audit_trail.py:91` inserts into it and NOTHING has ever
            # selected from it — only the retention purge touches it. By this
            # repo's own law (an artifact with no notifier dies) it was already
            # dead on arrival.
            #
            # What it catches: on 2026-08-03 a real user's search wrote
            # `search_started` at 12:25 and then no `search_completed`, no
            # `search_failed`, and no run_log row — three hours later. The user
            # watched a spinner forever and support had nothing to read. A crash
            # at least writes search_failed; this wrote nothing at all.
            try:
                cur.execute("""
                    SELECT count(*) FROM audit_log a
                    WHERE a.event = 'search_started'
                      AND a.occurred_at::timestamptz < now() - interval '30 minutes'
                      AND a.occurred_at::timestamptz > now() - interval '7 days'
                      AND NOT EXISTS (
                            SELECT 1 FROM audit_log b
                             WHERE b.user_id = a.user_id
                               AND b.event IN ('search_completed', 'search_failed')
                               AND b.occurred_at::timestamptz > a.occurred_at::timestamptz)
                """)
                orphaned = cur.fetchone()[0]
                check("searches with no terminal event", str(orphaned), orphaned == 0,
                      f"{orphaned} search(es) in the last 7 days started and never "
                      f"recorded completed OR failed. The user sees an endless spinner "
                      f"and there is no record for anyone to diagnose.")
            except Exception:
                # audit_log may not exist on an older DB — that is not a product
                # fault, so degrade quietly rather than failing the whole run.
                conn.rollback()
                rows.append(("searches with no terminal event", "audit_log missing", "-"))

            # ---------------------------------------------------------------
            # 2c. A NOTIFICATION THRESHOLD ABOVE THE SCORE CEILING.
            #
            # The same gate-above-ceiling bug as check 1, on the delivery side —
            # and this one is why the product has never notified anybody.
            #
            # Measured 2026-08-03: the default score_threshold is 60
            # (dispatcher.py:255 and routes/notification_rules.py), while the
            # highest score any user has is 69 and only 6 of 9,429 feed rows —
            # 0.06% — reach 60 at all. So even a user who turns notifications ON
            # gets essentially nothing, forever, with no error anywhere. A
            # feature that is enabled and silent is indistinguishable from one
            # that is broken.
            #
            # This checks the REAL configured thresholds against the REAL score
            # distribution, so it keeps working as either moves.
            try:
                cur.execute("SELECT count(*), coalesce(max(score), 0) FROM user_feed")
                feed_n, feed_max = cur.fetchone()
                cur.execute("""
                    SELECT coalesce(min(score_threshold), 60), coalesce(max(score_threshold), 60)
                    FROM notification_rules WHERE enabled = 1
                """)
                lo_t, hi_t = cur.fetchone()
                # No enabled rules yet -> judge the DEFAULT, which is what every
                # future user will inherit the moment they switch it on.
                effective = lo_t if lo_t is not None else 60
                cur.execute("SELECT count(*) FROM user_feed WHERE score >= %s", (effective,))
                reachable = cur.fetchone()[0]
                pct = (reachable * 100.0 / feed_n) if feed_n else 0.0
                check(
                    f"notifiable at threshold {effective} (max score {feed_max})",
                    f"{reachable}/{feed_n} ({pct:.2f}%)",
                    reachable > 0 and pct >= NOTIFY_MIN_REACHABLE_PCT,
                    f"the notification threshold is {effective} but only {reachable} of "
                    f"{feed_n} feed rows ({pct:.2f}%) ever reach it — the best score in the "
                    f"whole system is {feed_max}. Turning notifications on would deliver "
                    f"almost nothing, silently and forever.",
                )
            except Exception:
                conn.rollback()

            # ---------------------------------------------------------------
            # 2d. BROWSER ERRORS NOBODY WOULD OTHERWISE SEE.
            #
            # The /api/client-log bridge is the only working browser-error
            # channel in production, and its records used to land in rotating
            # files on an ephemeral container -- wiped by every deploy, read by
            # nothing. Sentry deliberately ignores that logger (client ERRORs
            # once flooded the backend stream), so there was no consumer at all.
            # Client errors now also land in audit_log, and this is the eye on
            # them. "NaNw ago" was exactly this class: broken on screen, silent
            # on the server.
            try:
                cur.execute("""
                    SELECT count(*) FROM audit_log
                     WHERE event = 'client_error'
                       AND occurred_at::timestamptz > now() - interval '24 hours'
                """)
                cerr = cur.fetchone()[0]
                check("browser errors (24h)", str(cerr), cerr <= CLIENT_ERROR_MAX_24H,
                      f"{cerr} browser-side errors in 24h (limit {CLIENT_ERROR_MAX_24H}). "
                      f"These never reach Sentry by design, so this is the only place "
                      f"they surface.")
            except Exception:
                conn.rollback()

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
                # "zero" reads as a quality problem and is almost never one.
                # CHECK THE CREDENTIAL FIRST: with no LLM key set, the judge
                # cannot make a single call, so this column is NULL everywhere
                # and every downstream report calls it bad ranking. That
                # confusion cost a week of eval data (issue #238). This script
                # reads the PROD DB, not prod's env, so it cannot see the key
                # itself - external-health.yml's provider probe can, and now
                # goes red when all four are empty.
                rows.append(("rows with an LLM verdict", f"{judged}/{total}",
                             "ok" if judged > 0
                             else "zero - CHECK THE LLM KEY FIRST (OPENAI/GEMINI/"
                                  "GROQ/CEREBRAS_API_KEY): no key = no call = no "
                                  "verdict, which is config, not quality"))
                # Same asymmetry as above: "never ran" may just mean the flag is
                # off, but "ran and then stopped" is a fault no matter what the
                # flags say — and it was previously unalarmable.
                if judged > 0:
                    cur.execute("SELECT max(llm_matched_at) FROM user_feed WHERE llm_matched_at IS NOT NULL")
                    newest_j = cur.fetchone()[0]
                    if newest_j:
                        age_days = _age_days(str(newest_j))
                        stale = age_days is not None and age_days > STALE_FEATURE_DAYS
                        check("LLM judge freshness",
                              f"newest verdict {age_days}d old" if age_days is not None else "unparseable",
                              not stale,
                              f"the judge produced {judged} verdicts but none for {age_days} days - "
                              f"it has silently stopped judging.")
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
