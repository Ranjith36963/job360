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
             3 = nothing is BROKEN, but a product DECISION is waiting on the
                 owner. Deliberately not 1: see DECISION_MARKER below for why
                 an alarm that repeats a decision turns into wallpaper.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

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
# How far the newest user_feed row may fall behind the newest job before the
# PIPE between them counts as severed. Its own constant, deliberately NOT
# STALE_FEATURE_DAYS: that knob is 14 DAYS and guards job_enrichment /
# job_embeddings, where a quiet fortnight is plausible. The catalog refresh runs
# DAILY, so one missed night is normal and two is not — 48 hours is the first
# threshold that cannot be explained away by a single skipped run. Measured
# against the real outage: 93 hours. At 14 days it would never have fired.
FEED_LAG_MAX_HOURS = float(os.getenv("PA_FEED_LAG_MAX_HOURS", "48"))
# How old the newest job in the catalog may be before ingestion counts as dead.
# Same daily-refresh reasoning, and a separate knob for the same reason: this
# watches `jobs` itself, FEED_LAG_MAX_HOURS watches the pipe OUT of it, and
# either can break while the other is fine.
CATALOG_MAX_AGE_HOURS = float(os.getenv("PA_CATALOG_MAX_AGE_HOURS", "48"))
# The "produced rows, then stopped" guard and the timestamp column it must read
# on each table. THESE NAMES ARE LOAD-BEARING: the guard used to hardcode
# `created_at`, which exists on NEITHER table (they are `enriched_at` and
# `embedding_updated_at`), so every run raised UndefinedColumn into a bare
# `except` and skipped the check in silence. Cross-checked against the
# migrations by backend/tests/test_feed_lag_edge.py so a rename cannot quietly
# disarm it a second time.
FEATURE_FRESHNESS_TABLES: tuple[tuple[str, str, str], ...] = (
    ("job_enrichment", "LLM enrichment", "enriched_at"),
    ("job_embeddings", "semantic embeddings", "embedding_updated_at"),
)
# How stale those two tables may get before the feature counts as stopped —
# and it is an HOURS question, not a fortnight question.
#
# THE BUG THIS REPLACES. Restoring the freshness ROW (above) was only half the
# fix: the row came back still comparing against STALE_FEATURE_DAYS = 14. Read
# live from prod 2026-08-15, both features had been frozen since 2026-08-08 and
# BOTH ROWS PRINTED `ok`:
#     LLM enrichment freshness      newest 7d old   ok
#     semantic embeddings freshness newest 7d old   ok
# They could not have gone red before 2026-08-22. A restored row that cannot go
# red is the same guard-shaped nothing as the one it replaced.
#
# WHERE 48 COMES FROM — read from the worker, not chosen by taste
# (backend/src/workers/settings.py):
#     enrichment_sweep   minute={10, 40}          -> every 30 MINUTES
#     refresh_catalog    hour=4, minute=0         -> DAILY, so new candidates
#                                                    arrive once a day
# The sweep therefore gets 48 chances to pick up each day's batch. One quiet
# day is explainable (no new candidates, or one missed refresh); two is not.
# Same shape as FEED_LAG_MAX_HOURS, and its own knob for the same reason:
# sharing STALE_FEATURE_DAYS is exactly what disarmed it.
# Pinned to those crons by backend/tests/test_product_assertions_main.py.
FEATURE_FRESHNESS_MAX_HOURS = float(os.getenv("PA_FEATURE_FRESHNESS_MAX_HOURS", "48"))
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
    try:
        dt = datetime.fromisoformat(iso.strip().replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0, (datetime.now(timezone.utc) - dt).days)


def _parse_iso(value: object) -> datetime | None:
    """Parse one of the ISO-8601 TEXT timestamp columns, or None.

    Same contract as `_age_days`: these are unconstrained TEXT columns, so a
    parse failure must mean "cannot tell" — never "fresh".
    """
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _age_hours(iso: object, now: object = None) -> float | None:
    """Age in HOURS of an ISO-8601 timestamp, or None if unreadable.

    `_age_days` floors to whole days, so it cannot express "36 hours" at all —
    useless next to an hours-scale threshold. `now` is injectable so the
    threshold behaviour can be tested without freezing the clock.
    """
    then = _parse_iso(iso)
    if then is None:
        return None
    ref = _parse_iso(now) if now is not None else datetime.now(timezone.utc)
    if ref is None:
        return None
    return max(0.0, (ref - then).total_seconds() / 3600.0)


def feed_lag_hours(newest_job: object, newest_feed: object) -> float | None:
    """How many hours the newest FEED row trails the newest CATALOG job.

    This is an EDGE measurement, not a node measurement — the distinction is the
    whole point of the check that uses it. Both endpoints can be perfectly
    healthy on their own while the pipe between them is severed, and a row count
    on either side cannot see that. Returns None when either timestamp is
    unreadable, so the caller can report "blind" instead of "healthy".

    Never negative: a feed row written after the newest job means the pipe is
    running ahead, which is fine, not a fault.
    """
    job_dt, feed_dt = _parse_iso(newest_job), _parse_iso(newest_feed)
    if job_dt is None or feed_dt is None:
        return None
    return max(0.0, (job_dt - feed_dt).total_seconds() / 3600.0)


def feed_lag_cause(searches_since: int | None) -> str:
    """Which of the two very different faults produced this lag.

    Same symptom, opposite fixes:

    ``severed``  a user DID search and no feed row came out — a crash, a poison
                 row, a scoring exception. Fix the code.
    ``starved``  nobody searched, and NOTHING ELSE writes ``user_feed``. The
                 ARQ crons are nightly_ghost_sweep, refresh_catalog,
                 notification_tick and enrichment_sweep
                 (src/workers/settings.py:210-229), and refresh_catalog passes
                 ``user_id=None`` deliberately for cost safety, which makes the
                 feed write structurally unreachable. So new jobs reach a
                 person only when that person clicks Search. Fix the product.
    ``unknown``  audit_log could not be read. Never guess — a confident wrong
                 diagnosis sends the owner hunting a crash that does not exist,
                 and a guard that cries wolf gets muted.
    """
    if searches_since is None:
        return "unknown"
    return "severed" if searches_since > 0 else "starved"


def feed_lag_explanation(cause: str, lag: float, stranded: int) -> str:
    """The sentence the owner actually reads. One per cause, on purpose."""
    head = (f"the newest job is {lag:.1f}h newer than the newest user_feed row, "
            f"and {stranded} jobs have been ingested since anything last reached "
            f"a dashboard. ")
    if cause == "severed":
        return head + (
            "Searches DID run in that window and produced no feed row, so the "
            "pipe between two individually healthy tables is severed — the work "
            "is being done and thrown away. Look for a crash mid-ingest."
        )
    if cause == "starved":
        return head + (
            "No search ran in that window at all, and nothing else writes "
            "user_feed: the only crons are the ghost sweep, the catalog "
            "refresh (user_id=None by design, so it cannot write a feed row), "
            "the notification tick and the enrichment sweep. So the product "
            "puts new jobs in front of a person ONLY when that person happens "
            "to click Search. Nothing is crashing — there is no scheduled path "
            "from a new job to a user at all."
        )
    return head + (
        "Could not read audit_log, so it is not possible to say whether a "
        "search ran and failed or no search ran at all. Both are real faults; "
        "check run history by hand rather than assuming."
    )


# Everything after this marker in stdout is the DECISION message, sliced out by
# product-health.yml and posted to Slack #needs-your-decision. A marker (not a
# heading) because headings get reworded and a workflow that silently stops
# finding its own payload is another guard-shaped nothing.
DECISION_MARKER = "<!-- product-decision-start -->"

# WHY A DECISION IS NOT AN ALARM — the whole point of the split.
#
# A STARVED feed is not a fault. Nothing crashed; there is simply no scheduled
# path from a new job to a user, because refresh_catalog passes user_id=None on
# purpose for cost safety. No amount of alarming can fix that: only the owner
# can, by choosing one of the options below.
#
# Before this split, the starved case went into `problems` like any break. What
# that produced, measured: red on ~23% of days in multi-day streaks, and
# product-health.yml AUTO-CLOSES its own issue the moment anybody searches (the
# lag drops under 48h on its own). So the alarm opened, spammed comments,
# closed itself, and came back the following week — for a product gap that was
# never once fixed by the noise.
#
# THAT IS HOW AN ALARM DIES. A signal that repeats without anything changing
# stops being read; the reader learns "this one is always red" and files the
# whole detector under wallpaper. Then the day it fires for a REAL severed pipe,
# nobody looks. A repeated decision does not add urgency — it subtracts
# credibility from every other row in the table. So: raise it ONCE, to a human,
# in the channel where humans make calls, and then be quiet.
DECISION_OPTIONS = """
The choice, and it is yours — nothing here is a bug to fix:

**OPTION A — build the scheduled fan-out.** After `refresh_catalog` runs at
04:00, score the new jobs into every active user's feed. New jobs then reach a
dashboard whether or not anyone clicks Search. Cost: CPU per user per night;
the paid LLM stages stay structurally off unless their flags are on.

**OPTION B — accept it, and say so in the product.** Keep the feed
search-triggered by design, delete this alarm, and tell the user plainly that
their feed updates when they search. Cheapest option; it is only wrong if the
product promises a feed that refreshes itself.

**OPTION C — the middle.** A cheap keyword-only nightly fan-out for users who
signed in recently, no LLM stages at all. Most of A's value, a fraction of the
cost, and it decays to nothing for dormant accounts.

Until you pick one, this stays quiet. It will NOT be raised again — a decision
alarm that repeats becomes wallpaper, and then the real breaks get ignored too.
"""


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
    decisions: list[str] = []  # owner calls, NOT breaks - see DECISION_OPTIONS

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
            for table, label, ts_col in FEATURE_FRESHNESS_TABLES:
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
                    # The table EXISTS (the count above succeeded), so a failure
                    # here is the checker being wrong, not the DB being old — and
                    # it must be LOUD. Swallowing it is exactly how this guard sat
                    # disarmed: it asked for `created_at`, which neither table has.
                    try:
                        cur.execute(f"SELECT max({ts_col}) FROM {table}")  # noqa: S608 - literals from FEATURE_FRESHNESS_TABLES
                        newest = cur.fetchone()[0]
                    except Exception as exc:  # noqa: BLE001
                        conn.rollback()
                        newest = None
                        check(f"{label} freshness", f"cannot read {table}.{ts_col}", False,
                              f"the {label} freshness guard cannot run: "
                              f"{type(exc).__name__} on {table}.{ts_col}. It has been "
                              f"reporting nothing at all, which reads exactly like "
                              f"reporting health.")
                    if newest:
                        # HOURS, not whole days. _age_days floors, so it cannot
                        # even express the gap between a sweep that runs every
                        # 30 minutes and one that stopped yesterday — and
                        # against a 14-day knob it printed `ok` through a live
                        # 6-day freeze. See FEATURE_FRESHNESS_MAX_HOURS.
                        age_h = _age_hours(str(newest))
                        stale = age_h is None or age_h > FEATURE_FRESHNESS_MAX_HOURS
                        # Blindness is not health: an unreadable timestamp goes
                        # red too, same contract as the feed-lag check.
                        seen = f"{age_h:.1f}h" if age_h is not None else f"?? ({newest!r})"
                        check(f"{label} freshness (max {FEATURE_FRESHNESS_MAX_HOURS:.0f}h)",
                              f"newest {seen} old" if age_h is not None else "unparseable",
                              not stale,
                              f"{label} has {n} rows but NOTHING new for {seen} - it "
                              f"worked once and has silently stopped. The sweep runs "
                              f"every 30 minutes, so this is not a quiet patch. Old "
                              f"rows make a dead feature look alive in a row count.")

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
                rows.append(("rows with an LLM verdict", f"{judged}/{total}",
                             "ok" if judged > 0 else "zero - the judge never ran"))
                # Same asymmetry as above: "never ran" may just mean the flag is
                # off, but "ran and then stopped" is a fault no matter what the
                # flags say — and it was previously unalarmable.
                # NOTE: this one deliberately KEEPS the day-scale knob while
                # the two above moved to hours. The judge is not on a cron — it
                # runs inside a search, so its cadence is however often a human
                # clicks Search. Tightening it to 48h would just re-raise the
                # STARVED feed under a second name, weekly, forever: the same
                # wallpaper problem the decision split below exists to stop.
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

            # The heading above promised a STALENESS check and the code below it
            # was `count(*) > 0` — an existence test wearing a freshness label.
            # If every source died tonight the catalog would hold its 10,257 rows
            # forever and this section would stay green forever with them. Rows
            # are not recency; only the newest timestamp is.
            cur.execute("SELECT max(first_seen_at) FROM jobs")
            newest_job_seen = cur.fetchone()[0]
            if catalog > 0:
                cat_age = _age_hours(newest_job_seen)
                if cat_age is None:
                    check("catalog freshness", "UNREADABLE max(first_seen_at)", False,
                          f"cannot tell how old the newest job is "
                          f"(first_seen_at={newest_job_seen!r}) — blind, not healthy.")
                else:
                    check(f"catalog freshness (max {CATALOG_MAX_AGE_HOURS:.0f}h)",
                          f"newest job {cat_age:.1f}h old",
                          cat_age <= CATALOG_MAX_AGE_HOURS,
                          f"the newest job in the catalog is {cat_age:.1f}h old. "
                          f"Ingestion has stopped. The {catalog} rows already there "
                          f"keep every count green while the product slowly serves "
                          f"nothing but stale listings.")

            # ---------------------------------------------------------------
            # 6. THE PIPE BETWEEN TWO HEALTHY TABLES. **THIS IS AN EDGE CHECK,
            # NOT A NODE CHECK** — and that distinction is the entire lesson.
            #
            # Every other assertion in this file asks "is THIS TABLE healthy":
            # enough rows, recent rows, sane values. This one asks "is the pipe
            # between two healthy tables still connected".
            #
            # Measured live 2026-08-15, read-only from prod:
            #     newest job (jobs.first_seen_at)  2026-08-15T04:05:15+00:00
            #     newest feed row (user_feed)      2026-08-11T07:02:12+00:00
            #     jobs ingested in between         1,345
            #     lag                              93.05 HOURS
            # Four days in which nothing new reached any dashboard — and
            # product-health.yml reported 13/13 GREEN throughout. It could not
            # do otherwise. `jobs` was healthy: 10,257 rows, newest hours old.
            # `user_feed` was healthy: 10,508 rows, 210x the floor of 50. Check
            # 1 above counts feed rows against MIN_FEED_ROWS and never asks
            # whether any of them are FRESH, so a feed frozen since 8-11 reads
            # exactly like a feed written this morning.
            #
            # The threshold is FEED_LAG_MAX_HOURS (48), not STALE_FEATURE_DAYS
            # (14 days). See the constant for why: at 336 hours this outage
            # would have gone completely unnoticed. Guard on the arithmetic and
            # on the choice of constant: backend/tests/test_feed_lag_edge.py.
            #
            # AND IT MUST NAME THE CAUSE, not just the symptom. Two opposite
            # faults make the same lag — a search that ran and produced nothing
            # (fix the code), or no search at all (fix the product, because
            # NOTHING on a schedule writes user_feed: refresh_catalog passes
            # user_id=None by design, so the feed write is structurally
            # unreachable from every cron). Measured during this very outage:
            # audit_log held ZERO search events, so the true reading is STARVED,
            # not severed. Shouting "severed" here would have sent the owner
            # hunting a crash that does not exist — see feed_lag_cause().
            # ---------------------------------------------------------------
            newest_job = newest_job_seen
            cur.execute("SELECT max(created_at) FROM user_feed")
            newest_feed = cur.fetchone()[0]
            if newest_job is None or newest_feed is None:
                # An empty side is not a severed pipe — check 1 (feed rows) and
                # the catalog count above already own the "nothing here at all"
                # case, and double-reporting it would just add noise.
                rows.append(("feed lag behind catalog", "one side empty", "-"))
            else:
                lag = feed_lag_hours(newest_job, newest_feed)
                if lag is None:
                    # Blindness must never read as health. These are TEXT
                    # columns; data_invariants.py exists because one of them
                    # once held a Unix epoch string.
                    check("feed lag behind catalog", "UNREADABLE timestamps", False,
                          f"cannot measure the catalog->feed pipe: "
                          f"jobs.first_seen_at={newest_job!r}, "
                          f"user_feed.created_at={newest_feed!r}. A check that "
                          f"cannot see is not a check that passed.")
                else:
                    cur.execute(
                        "SELECT count(*) FROM jobs WHERE first_seen_at > %s",
                        (str(newest_feed),))
                    stranded = cur.fetchone()[0]
                    # Red is not enough — it must name the RIGHT cause, because
                    # "a search crashed" and "nobody searched" need opposite
                    # fixes. Only the search history separates them.
                    try:
                        cur.execute(
                            "SELECT count(*) FROM audit_log WHERE event = 'search_started' "
                            "AND occurred_at > %s", (str(newest_feed),))
                        searches_since: int | None = cur.fetchone()[0]
                    except Exception:  # noqa: BLE001 - audit_log may predate this DB
                        conn.rollback()
                        searches_since = None
                    cause = feed_lag_cause(searches_since)
                    lag_ok = lag <= FEED_LAG_MAX_HOURS
                    label = f"feed lag behind catalog (max {FEED_LAG_MAX_HOURS:.0f}h)"
                    value = f"{lag:.1f}h, {stranded} jobs stranded, {cause}"
                    why = feed_lag_explanation(cause, lag, stranded)
                    if lag_ok:
                        rows.append((label, value, "ok"))
                    elif cause == "starved":
                        # NOT a break, so NOT `problems`: nothing crashed, and
                        # the fix is a product choice only the owner can make.
                        # Routed once to Slack #needs-your-decision instead of
                        # into the weekly self-closing issue. See
                        # DECISION_OPTIONS for why repeating it would be worse
                        # than never raising it at all.
                        rows.append((label, value, "**DECISION**"))
                        decisions.append(why)
                    else:
                        # severed (a search ran and produced nothing) or
                        # unknown (audit_log unreadable). Both are real faults
                        # in the code, and `unknown` is not permission to stay
                        # quiet — a lag nobody can explain is still a lag.
                        check(label, value, False, why)

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
        # A real break OUTRANKS a pending decision. Never mask an outage behind
        # "there is a decision waiting" — and the canary forces a red run and
        # demands exactly 1, so this ordering keeps the canary honest too.
        return 1

    if decisions:
        # LAST in the output on purpose: product-health.yml slices from the
        # marker to EOF and posts that to Slack #needs-your-decision.
        print(DECISION_MARKER)
        print("## A DECISION is waiting on you (nothing is broken)\n")
        for d in decisions:
            print(f"{d}\n")
        print(DECISION_OPTIONS.strip())
        return 3

    print("All product assertions hold. The product is doing its job, "
          "not merely failing to crash.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
