#!/usr/bin/env python3
"""Production data invariants — is the data VALID, not merely PRESENT?

WHY THIS FILE EXISTS.

On 2026-08-03 the live dashboard showed a job card reading **"Posted NaNw ago"**.
It had been doing so for days. On that same day, every scheduled detector we own
ran and reported success:

    uptime          success     synthetic-live  success
    absence         success     product-health  success
    user-journey    success

Six green loops, one visibly broken product. That is not bad luck, it is a
category error: every one of those detectors measures **quantity** — are there
enough rows, is anything still moving, did something stop. Not one of them asks
whether a value is *what its column claims to be*.

The root cause was exactly that kind of value. `jobs.posted_at` is an
unconstrained TEXT column, and `arbeitnow.py` stored the upstream `created_at`
verbatim — a Unix epoch integer:

    posted_at       = '1785750903'
    date_confidence = 'high'          <- and we CERTIFIED it

The frontend then did `new Date('1785750903')` -> NaN. Every guard in `timeAgo()`
compares against NaN, every comparison is false, so control fell through to the
final `return` and shipped the string "NaNw ago" to a real person. Nothing threw.
Sentry recorded zero events. It is not that we missed an alarm — for this class
of fault, **no alarm existed**.

WHAT THIS DOES. A library of cheap, dumb, deterministic SQL invariants over the
production database. Each one is a claim the product depends on being true. They
are boring on purpose: no statistics, no thresholds to tune, no model to trust.
A violation is a fact, not an opinion.

WHAT IT DELIBERATELY CANNOT DO. Some faults are invisible from the database.
Found the same day: the dashboard header showed "4078 jobs matched" while the
time-bucket tabs read 100, because the tabs are computed client-side over a
query that hits the API's default `limit=100`. The DB is right. The API is right.
Only the rendered screen lies. No SQL can ever see that — which is why the
detector has a second leg in `frontend/tests/synthetic/live-smoke.mjs`. Two
bugs, two legs; neither leg alone would have caught both.

CONTRACT (0/1/2 match scripts/product_assertions.py so the workflows stay the
same shape; that script additionally uses exit 3 for "a product DECISION is
waiting on the owner", which has no meaning here — a broken invariant is always
a break, never a choice):
    exit 0  every invariant holds
    exit 1  at least one NEW violation -> file an issue
    exit 2  the checker itself broke -> a red run means "I am blind", not "clean"
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from typing import Any

# ── Knobs. Env-overridable so a drill can force a red without editing code. ──
# NEW_WINDOW is the heart of the anti-staleness design (see Invariant.why below):
# only violations on rows first seen inside this window raise the alarm.
NEW_WINDOW_HOURS = int(os.getenv("DI_NEW_WINDOW_HOURS", "48"))
# A drill/canary knob: set to 1 to make the always-true invariant fail, proving
# this script can still go red. A checker that cannot fail is not a checker.
FORCE_RED = os.getenv("DI_FORCE_RED", "") == "1"
SAMPLE_ROWS = int(os.getenv("DI_SAMPLE_ROWS", "3"))


@dataclass
class Invariant:
    """One claim the product depends on being true of production data.

    ``where`` is a SQL predicate selecting VIOLATING rows. It is deliberately
    written as a predicate rather than a full query so the runner can split
    every invariant into new-vs-legacy counts without each entry repeating that
    logic.
    """

    id: str
    table: str
    age_col: str          # ISO-8601 text column used to tell new rows from old
    where: str            # SQL predicate: TRUE for a violating row
    why: str              # what BREAKS for a user when this is false
    incident: str = ""    # provenance: the real event that motivated it
    sample_cols: str = "id, source"
    group_by: str = ""    # optional column to break the count down by
    # Some invariants cannot be expressed as a row predicate (e.g. a global
    # count comparison). Those supply a full query returning a single integer.
    scalar_sql: str = ""
    scalar_ok: str = "== 0"


# ---------------------------------------------------------------------------
# THE LIBRARY.
#
# Every entry carries `why` (what a USER loses) and `incident` (why we believe
# it). That provenance is load-bearing: a threshold with no story behind it is
# un-deletable, because nobody later can tell whether it still means anything.
# An invariant that fires twice and is ruled "not a real problem" should be
# FIXED OR DELETED the same day — a muted invariant is worse than none.
# ---------------------------------------------------------------------------
INVARIANTS: list[Invariant] = [
    Invariant(
        id="posted_at_parses",
        table="jobs",
        age_col="first_seen_at",
        where=r"posted_at IS NOT NULL AND posted_at <> '' "
              r"AND posted_at !~ '^\d{4}-\d{2}-\d{2}'",
        why="a job's posted date is not a date. The UI calls new Date() on it, "
            "gets NaN, and every comparison against NaN is false — so the card "
            "renders 'Posted NaNw ago'. Recency scoring silently returns 0 for "
            "these jobs too, so they rank wrong as well as read wrong.",
        incident="2026-08-03: 526 rows held epoch strings ('1785750903') or UK "
                 "dates ('27/07/2026'). arbeitnow 385, reed 98, nofluffjobs 24, "
                 "himalayas 19. Zero Sentry events.",
        group_by="source",
    ),
    Invariant(
        id="posted_at_in_range",
        table="jobs",
        age_col="first_seen_at",
        # Lexical comparison on ISO prefixes — correct for YYYY-MM-DD, and it
        # cannot raise a cast error on the garbage the previous invariant finds.
        where=r"posted_at ~ '^\d{4}-\d{2}-\d{2}' AND ("
              r"substring(posted_at from 1 for 10) < '2015-01-01' OR "
              r"substring(posted_at from 1 for 10) > to_char(now() + interval '2 days', 'YYYY-MM-DD'))",
        why="a date that parses but is absurd — a job posted in 1970 or next "
            "year. Millisecond-vs-second epoch confusion produces exactly this, "
            "and it passes the parse check while making recency meaningless.",
        incident="Companion to posted_at_parses: fixing the format without "
                 "checking the value would trade a visible bug for a silent one.",
        group_by="source",
    ),
    Invariant(
        id="confidence_honesty",
        table="jobs",
        age_col="first_seen_at",
        where=r"date_confidence = 'high' AND (posted_at IS NULL OR posted_at = '' "
              r"OR posted_at !~ '^\d{4}-\d{2}-\d{2}')",
        why="we labelled a date we cannot parse as HIGH confidence. Anything "
            "downstream that trusts that flag is being lied to by our own code.",
        incident="2026-08-03: job 34718 carried posted_at='1785750903' with "
                 "date_confidence='high'.",
        group_by="source",
    ),
    Invariant(
        id="apply_url_is_reachable_shape",
        table="jobs",
        age_col="first_seen_at",
        where=r"apply_url IS NOT NULL AND apply_url <> '' AND apply_url !~ '^https?://'",
        why="the apply link is the entire point of the product. A malformed one "
            "fails only when a user clicks it, which we never see.",
        incident="2026-08-03, found by this invariant on its FIRST run: 2,805 "
                 "jobs — 43% of the entire catalog, every one from devitjobs — "
                 "carried a bare slug instead of a URL ('FBI-TMT-Metadata-Lead'). "
                 "The Apply button, the single action the whole product exists "
                 "to produce, was dead on nearly half the catalog and no alarm, "
                 "test or human had ever noticed.",
        group_by="source",
    ),
    Invariant(
        id="salary_range_sane",
        table="jobs",
        age_col="first_seen_at",
        where="salary_min IS NOT NULL AND salary_max IS NOT NULL "
              "AND salary_min > salary_max",
        why="a salary range that runs backwards renders as nonsense and breaks "
            "the salary dimension of scoring.",
        incident="fx.py converts 18 currencies and salary.py normalises cadence; "
                 "either can invert a range without raising.",
        group_by="source",
    ),
    Invariant(
        id="salary_magnitude_sane",
        table="jobs",
        age_col="first_seen_at",
        where="(salary_min IS NOT NULL AND (salary_min < 0 OR salary_min > 2000000)) "
              "OR (salary_max IS NOT NULL AND (salary_max < 0 OR salary_max > 2000000))",
        why="an hourly rate stored as an annual salary (or a currency left "
            "unconverted) produces £3 or £8,000,000 jobs. Both read as broken "
            "and both poison salary scoring.",
        incident="Cadence normalisation is a known-hard path; this is its smoke "
                 "alarm.",
        group_by="source",
    ),
    Invariant(
        id="match_score_in_bounds",
        table="jobs",
        age_col="first_seen_at",
        where="match_score IS NOT NULL AND (match_score < 0 OR match_score > 100)",
        why="scores outside 0-100 break every threshold in the system at once — "
            "feed visibility, the enrichment gate, the judge funnel.",
        incident="CLAUDE.md rule #27: multi-dim weights sum to 130 before a clamp "
                 "to [0,100]. This invariant detects that clamp being removed.",
    ),
    Invariant(
        id="feed_score_in_bounds",
        table="user_feed",
        age_col="created_at",
        where="(score IS NOT NULL AND (score < 0 OR score > 100)) "
              "OR (llm_fit_score IS NOT NULL AND (llm_fit_score < 0 OR llm_fit_score > 100))",
        why="the same clamp failure, on the per-user side. user_feed.score has a "
            "CHECK constraint; llm_fit_score does not — so the judge is the "
            "unguarded half.",
        incident="Migration 0017 added llm_fit_score without a bound.",
        sample_cols="id, user_id",
    ),
    Invariant(
        id="llm_verdict_fields_move_together",
        table="user_feed",
        age_col="created_at",
        where="(llm_fit_score IS NOT NULL)::int + (llm_verdict IS NOT NULL)::int "
              "+ (llm_matched_at IS NOT NULL)::int NOT IN (0, 3)",
        why="a half-written judge verdict. Either all three LLM columns are set "
            "or none are; anything else means the judge died mid-write and the "
            "row now shows a score with no reasoning, or reasoning with no score.",
        incident="Migration 0017 writes three columns with no transaction "
                 "guarantee tying them together.",
        sample_cols="id, user_id",
    ),
    Invariant(
        id="feed_has_no_orphans",
        table="user_feed",
        age_col="created_at",
        where="NOT EXISTS (SELECT 1 FROM jobs j WHERE j.id = user_feed.job_id)",
        why="a feed row pointing at a job that no longer exists renders as a "
            "broken or blank card. purge_old_jobs() deletes from the shared "
            "catalog on a 30-day window; feed rows are per-user and outlive it.",
        incident="CLAUDE.md rule #3 guards the purge threshold; nothing guards "
                 "what the purge leaves dangling.",
        sample_cols="id, user_id",
    ),
    # ── ROUND 2 ──────────────────────────────────────────────────────────
    # These exist because a Fable model was run as an INDEPENDENT detector over
    # one real user's whole lifecycle, and the diff against this library was
    # measured. It found 12 faults; this library had caught 2 of them. Every
    # invariant below closes one of the misses, using the SQL that model wrote.
    #
    # That diff is the method, not a one-off: an expensive model finds what a
    # cheap check cannot imagine, and each thing it finds becomes a cheap check
    # forever. Run it again when the product changes shape.
    Invariant(
        id="index_row_size_canary",
        table="jobs",
        age_col="first_seen_at",
        where="length(normalized_company) + length(normalized_title) > 2000",
        why="a row approaching Postgres's 2,704-byte btree index limit. When one "
            "crosses it the INSERT raises ProgramLimitExceeded, and because the "
            "insert loop has no per-job guard, THE WHOLE SEARCH ABORTS — one "
            "poison row silently freezes a user's feed.",
        incident="2026-07-27: both of the active user's searches crashed on a "
                 "HackerNews comment stored as title+company. His feed then "
                 "received zero new rows for SEVEN DAYS while the catalog grew "
                 "by ~2,800 jobs. Measured 2026-08-03: the largest pair is 2,491 "
                 "bytes against a 2,704 ceiling — this is armed, not theoretical.",
        group_by="source",
    ),
    Invariant(
        id="title_is_not_a_paragraph",
        table="jobs",
        age_col="first_seen_at",
        where="length(title) > 200 OR (title = company AND length(title) > 80)",
        why="a whole forum comment stored as a job title. It renders as an "
            "unreadable card, produces a meaningless dedup key, and is the raw "
            "material for the index-size crash above.",
        incident="2026-08-03: 14 titles over 300 chars, longest 1,551. Several "
                 "reach the dashboard; one is visible on the real user's feed.",
        group_by="source",
    ),
    Invariant(
        id="paid_verdicts_are_visible",
        table="user_feed",
        age_col="created_at",
        where="llm_fit_score >= 70 AND EXISTS (SELECT 1 FROM jobs j "
              "WHERE j.id = user_feed.job_id AND (j.staleness_state IS DISTINCT FROM 'active' "
              "OR substring(j.first_seen from 1 for 10) < "
              "to_char(now() - interval '7 days', 'YYYY-MM-DD')))",
        why="we PAID an LLM to judge these as strong matches, and the dashboard's "
            "staleness and 7-day window then hide them. The user never sees the "
            "best thing we found for them, and the money is spent either way.",
        incident="2026-08-03: 35 of 54 judged rows (65%) were invisible, "
                 "including a job scored 85 'Strong fit' that was posted five "
                 "days earlier and is still open.",
        sample_cols="id, user_id",
    ),
    Invariant(
        id="feed_scored_by_current_profile",
        table="user_feed",
        age_col="created_at",
        where="profile_version IS NOT NULL AND profile_version <> "
              "(SELECT max(v.id) FROM user_profile_versions v WHERE v.user_id = user_feed.user_id)",
        why="scores computed against a profile the user has since replaced. They "
            "sit on a different scale from everything around them, so they sort "
            "wrongly against current rows and mislead every threshold that reads "
            "them.",
        incident="2026-08-03: 43 rows still carried profile_version=2 from 7-02, "
                 "because get_catalog_jobs_for_rescore caps at 5,000 rows and the "
                 "catalog had grown to 6,457. The gap widens every day.",
        sample_cols="id, user_id",
    ),
    Invariant(
        id="rescore_covers_whole_catalog",
        table="jobs",
        age_col="first_seen_at",
        where="",
        scalar_sql="SELECT GREATEST(0, (SELECT count(*) FROM jobs) - 50000)",
        why="the catalog is bigger than the re-score query's LIMIT, so the oldest "
            "rows are never re-scored when a profile changes. Silent, and it "
            "grows every single day.",
        incident="2026-08-03: 6,457 jobs vs a hard limit of 5,000 in "
                 "database.py:get_catalog_jobs_for_rescore, so 1,457 jobs were "
                 "never re-scored. The limit was raised to 50,000; this "
                 "invariant now guards THAT ceiling, so the same silent "
                 "truncation cannot return as the catalog grows.",
    ),
    Invariant(
        id="applied_job_still_openable",
        table="applications",
        age_col="created_at",
        where="EXISTS (SELECT 1 FROM jobs j WHERE j.id = applications.job_id "
              "AND j.staleness_state IS DISTINCT FROM 'active')",
        why="the user's own application points at a job whose detail page now "
            "404s, because the detail route serves active jobs only. Clicking "
            "your own application and getting 'not found' reads as data loss — "
            "on the highest-intent object in the product.",
        incident="2026-08-03: the active user's application on job 56 "
                 "('AI / ML Intern') could no longer be opened.",
        sample_cols="id, user_id",
    ),
    Invariant(
        id="search_always_has_a_terminal_event",
        table="audit_log",
        age_col="occurred_at",
        where="",
        scalar_sql="SELECT count(*) FROM audit_log a WHERE a.event = 'search_started' "
                   "AND a.occurred_at::timestamptz < now() - interval '30 minutes' "
                   "AND a.occurred_at::timestamptz > now() - interval '7 days' "
                   "AND NOT EXISTS (SELECT 1 FROM audit_log b WHERE b.user_id = a.user_id "
                   "AND b.event IN ('search_completed','search_failed') "
                   "AND b.occurred_at::timestamptz > a.occurred_at::timestamptz)",
        why="a search that started and never finished, failed, or recorded "
            "anything. The user watches a spinner forever and support has no "
            "record to look at. A crash at least writes search_failed; this "
            "writes nothing at all.",
        incident="2026-08-03 12:25: a search started and produced no terminal "
                 "event three hours later, and no run_log row. The same shape "
                 "appeared on 7-27.",
    ),
    Invariant(
        id="notifications_have_ever_delivered",
        table="user_feed",
        age_col="created_at",
        where="",
        scalar_sql="SELECT CASE WHEN (SELECT count(*) FROM users) > 3 "
                   "AND (SELECT count(*) FROM notification_rules) = 0 THEN 1 ELSE 0 END",
        why="this is a job-ALERT product that has never alerted anyone. Not one "
            "notification rule exists, so no digest, no instant send and no "
            "channel can ever fire. New matching jobs reach the user only if "
            "they happen to open the site and press Search themselves.",
        incident="2026-08-03: notification_rules, user_channels, "
                 "notification_ledger and user_notification_digests are ALL "
                 "empty, and notified_at is NULL on every one of 6,054 feed rows. "
                 "The core promise of the product is silently absent.",
    ),
    Invariant(
        id="catalog_has_descriptions",
        table="jobs",
        age_col="first_seen_at",
        where="",
        scalar_sql="SELECT count(*) FROM jobs WHERE (description IS NULL OR description = '') "
                   "AND substring(first_seen_at from 1 for 10) >= "
                   "to_char(now() - interval '2 days', 'YYYY-MM-DD')",
        why="a job with no description cannot be skill-matched, so the 40-point "
            "skill component scores near zero and the job can never compete. "
            "Fetching it at all was wasted work.",
        incident="2026-08-03: 69% of the catalog had an empty description — "
                 "devitjobs, greenhouse, workday, smartrecruiters and linkedin "
                 "were 100% empty. The user's score distribution collapsed into "
                 "the 10-19 band as a direct result, starving every threshold "
                 "above it.",
    ),
    Invariant(
        id="catalog_dedup_holds",
        table="jobs",
        age_col="first_seen_at",
        where="",  # scalar form below
        scalar_sql="SELECT count(*) - count(DISTINCT (normalized_company, normalized_title)) "
                   "FROM jobs",
        why="duplicate (company, title) pairs in the shared catalog. The UNIQUE "
            "constraint should make this impossible, so a non-zero value means "
            "the constraint or the SQL translation layer is not doing its job.",
        incident="pg.translate() rewrites legacy SQLite-flavoured SQL to Postgres "
                 "at RUNTIME and is production-critical. This is a one-line audit "
                 "of it.",
    ),
    Invariant(
        id="catalog_is_uk_only",
        table="jobs",
        age_col="first_seen_at",
        # Explicit foreign countries and unambiguous foreign cities only. This
        # deliberately does NOT re-implement the gate's judgement — it is the
        # tripwire for the obvious case the gate must never miss, so it stays
        # readable and cannot itself become a source of false alarms.
        where=r"location ~* '\m(united states|usa|canada|australia|india|germany|"
              r"france|spain|italy|netherlands|poland|brazil|singapore|japan|china|"
              r"indianapolis|houston|san francisco|new york|toronto|mississauga|"
              r"sydney|shanghai|warsaw|madrid|barcelona|munich|münchen|berlin|"
              r"aachen|darmstadt|brussels|lima|são paulo|sao paulo|ottawa|"
              r"palo alto|belo horizonte)\M' "
              r"AND location !~* '\m(uk|united kingdom|london|england|scotland|wales)\M' "
              # RETIRED ROWS ARE NOT A VIOLATION. The one-time sweep marks
              # pre-gate foreign jobs `confirmed_expired` rather than deleting
              # them (a delete cascades to user_feed and is unrecoverable).
              # Without this clause the invariant counts the very rows the
              # sweep just fixed and fires forever — and a permanently-firing
              # invariant gets muted, which this file's own doctrine calls
              # worse than having none.
              r"AND (staleness_state IS NULL OR staleness_state = 'active')",
        why="a job in Warsaw or Indianapolis is in a UK-only product's catalog. "
            "The user searches for UK work and gets a role they cannot take — a "
            "product fault, not a ranking miss. Every such row also consumes "
            "enrichment budget, embedding budget and a candidate-shelf slot that "
            "a real UK job needed.",
        incident="2026-08-07: 156 clearly foreign jobs were live (greenhouse 77, "
                 "workday 24, devitjobs 11). Each of the 46 sources applied its "
                 "own _is_uk_or_remote check or none at all, so the leak was "
                 "structural. Fixed by the single-chokepoint UK gate "
                 "(src/services/uk_gate.py); this invariant is the watcher that "
                 "makes a regression page instead of hide.",
        group_by="source",
    ),
]


def _iso_cutoff_expr(age_col: str) -> str:
    """SQL fragment: TRUE when this row is NEW (inside the alarm window).

    The age columns are ISO-8601 TEXT, not timestamps, so this compares date
    prefixes lexically — correct for ISO, and it cannot raise a cast error on a
    malformed value (which, given this script exists to find malformed values,
    matters more than usual).
    """
    return (
        f"({age_col} IS NOT NULL AND substring({age_col} from 1 for 10) >= "
        f"to_char(now() - interval '{NEW_WINDOW_HOURS} hours', 'YYYY-MM-DD'))"
    )


def _run_invariant(cur: Any, inv: Invariant) -> dict[str, Any]:
    """Evaluate one invariant. Returns a result dict; never raises for SQL
    reasons the caller can't act on — a broken invariant is reported as such
    rather than taking the whole run down with it."""
    out: dict[str, Any] = {"id": inv.id, "new": 0, "legacy": 0, "breakdown": [],
                           "sample": [], "error": ""}
    try:
        if inv.scalar_sql:
            cur.execute(inv.scalar_sql)
            n = int(cur.fetchone()[0] or 0)
            # A scalar invariant has no age dimension: any violation is "new",
            # because we cannot tell when it started.
            out["new"] = n
            return out

        newp = _iso_cutoff_expr(inv.age_col)
        cur.execute(
            f"SELECT count(*) FILTER (WHERE {newp}), "  # noqa: S608 - literals only
            f"       count(*) FILTER (WHERE NOT {newp}) "
            f"FROM {inv.table} WHERE {inv.where}"
        )
        new_n, old_n = cur.fetchone()
        out["new"], out["legacy"] = int(new_n or 0), int(old_n or 0)

        if out["new"] or out["legacy"]:
            if inv.group_by:
                cur.execute(
                    f"SELECT {inv.group_by}, count(*) FROM {inv.table} "  # noqa: S608
                    f"WHERE {inv.where} GROUP BY 1 ORDER BY 2 DESC LIMIT 8"
                )
                out["breakdown"] = [(str(a), int(b)) for a, b in cur.fetchall()]
            cur.execute(
                f"SELECT {inv.sample_cols} FROM {inv.table} "  # noqa: S608
                f"WHERE {inv.where} LIMIT {SAMPLE_ROWS}"
            )
            out["sample"] = [tuple(str(x) for x in r) for r in cur.fetchall()]
    except Exception as exc:  # noqa: BLE001 - one bad invariant must not blind the rest
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def main() -> int:
    dsn = os.getenv("PROD_DATABASE_URL") or os.getenv("DATABASE_PUBLIC_URL")
    if not dsn:
        # Loud, not silent. A detector that skips because a secret is missing is
        # the dead-watcher failure this whole harness exists to prevent.
        print("::error::no PROD_DATABASE_URL - data invariants cannot run")
        return 2
    try:
        import psycopg
    except ImportError:
        print("::error::psycopg not installed - data invariants cannot run")
        return 2

    results: list[dict[str, Any]] = []
    try:
        with psycopg.connect(dsn, connect_timeout=25) as conn, conn.cursor() as cur:
            for inv in INVARIANTS:
                results.append(_run_invariant(cur, inv))
            if FORCE_RED:
                # The canary. Proves this script can still go red, every run.
                # "Green" must be a measurement, never a default.
                results.append({"id": "CANARY", "new": 1, "legacy": 0,
                                "breakdown": [], "sample": [], "error": ""})
    except Exception as exc:  # noqa: BLE001
        print(f"::error::could not reach production DB: {type(exc).__name__}: {exc}")
        return 2

    broken = [r for r in results if r["error"]]
    firing = [r for r in results if r["new"] > 0 and not r["error"]]
    legacy_only = [r for r in results if r["new"] == 0 and r["legacy"] > 0 and not r["error"]]
    by_id = {i.id: i for i in INVARIANTS}

    print("# Production data invariants\n")
    print(f"Window for NEW violations: last {NEW_WINDOW_HOURS}h\n")
    print("| invariant | new | legacy | verdict |")
    print("|---|---|---|---|")
    for r in results:
        verdict = ("**CHECKER BROKE**" if r["error"]
                   else "**FIRING**" if r["new"] else "legacy only" if r["legacy"] else "ok")
        print(f"| `{r['id']}` | {r['new']} | {r['legacy']} | {verdict} |")
    print()

    for r in firing:
        inv = by_id.get(r["id"])
        print(f"## FIRING: `{r['id']}` — {r['new']} new violations\n")
        if inv:
            print(f"**What breaks for a user:** {inv.why}\n")
            if inv.incident:
                print(f"**Why we check this:** {inv.incident}\n")
        if r["breakdown"]:
            print("**By source:** " + ", ".join(f"{k} {v}" for k, v in r["breakdown"]) + "\n")
        if r["sample"]:
            print("**Sample rows:** " + "; ".join(" / ".join(s) for s in r["sample"]) + "\n")
        if r["legacy"]:
            print(f"_({r['legacy']} older rows also violate — fix the source first, "
                  "then backfill.)_\n")

    if legacy_only:
        print("## Legacy only (informational — not alarming)\n")
        print("These violate but no NEW row has since the window opened, so the "
              "source is probably already fixed and the remaining rows need a "
              "backfill, not a code change.\n")
        for r in legacy_only:
            print(f"- `{r['id']}`: {r['legacy']} old rows")
        print()

    if broken:
        print("## The checker itself is broken\n")
        for r in broken:
            print(f"- `{r['id']}`: {r['error']}")
        print("\nA green run means nothing while this is true.\n")
        return 2

    if firing:
        print("None of these threw an exception. None reached Sentry. That is the "
              "point: a value that is merely WRONG looks exactly like a value that "
              "is right, until a person sees it on their screen.")
        # Machine-readable tail so the workflow can open one issue per invariant
        # rather than one blanket issue nobody can scope a fix to.
        print("\n<!--FIRING_JSON:" + json.dumps([r["id"] for r in firing]) + "-->")
        return 1

    print("Every production data invariant holds.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
