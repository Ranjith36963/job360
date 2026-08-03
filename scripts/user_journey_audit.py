#!/usr/bin/env python3
"""Walk EVERY user through the whole pipeline and find where it breaks for them.

WHY THIS EXISTS. Our other detectors look at aggregates: total feed rows, max
score, catalog size. An aggregate hides the thing that actually matters — that
the product can be perfectly healthy on average and completely broken for one
person. Found live 2026-08-03 by the first run of this script:

    user 7edd5b59  has a profile, and ZERO feed rows.  Aggregates said fine.
    user 88e7d907  has 152 extracted skills and ZERO target job titles.

Neither throws an error. Neither fails a test. Both are somebody's product
being quietly useless.

THE IDEA: the pipeline is a chain of stages. For each user, walk the chain and
report the FIRST stage that broke. That turns "the product feels off" into
"extraction works for this user, search config is empty, so the feed is empty" —
a sentence a repair loop can act on.

    signup -> profile input -> extraction -> search keywords -> feed
           -> scoring -> visible on the dashboard

Read-only SQL. No LLM. No writes.

Exit codes:  0 = every onboarded user is healthy
             1 = at least one user is broken at some stage
             2 = the audit itself failed (fail LOUD, never silently pass)
"""

from __future__ import annotations

import json
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# A user who never uploaded anything is not a bug — they just never started.
# Everything below applies only to users who DID give us input.
MIN_SKILLS = int(os.getenv("UJ_MIN_SKILLS", "3"))
MIN_FEED = int(os.getenv("UJ_MIN_FEED", "10"))
DISPLAY_FLOOR = int(os.getenv("UJ_DISPLAY_FLOOR", "0"))


def mask(email: str | None) -> str:
    if not email or "@" not in email:
        return "<none>"
    return f"{email[0]}***@{email.split('@')[-1]}"


def audit_user(cur, uid, email) -> tuple[str, str, dict]:
    """Return (stage_reached, verdict, facts) for one user.

    verdict is 'ok', 'broken', or 'not-started'.
    """
    f: dict = {}

    def one(q, default=0):
        cur.execute(q, (uid,))
        row = cur.fetchone()
        return row[0] if row and row[0] is not None else default

    f["profile_rows"] = one("SELECT count(*) FROM user_profiles WHERE user_id=%s")

    # STAGE 1 — did they give us anything at all?
    if not f["profile_rows"]:
        return "signup", "not-started", f

    cur.execute("SELECT cv_data, preferences FROM user_profiles WHERE user_id=%s", (uid,))
    row = cur.fetchone()
    cv = json.loads(row[0]) if row and row[0] else {}
    pref = json.loads(row[1]) if row and row[1] else {}

    f["cv_chars"] = len(cv.get("raw_text") or "")
    f["skills"] = len(cv.get("skills") or [])
    f["cv_titles"] = len(cv.get("job_titles") or [])
    f["target_titles"] = len(pref.get("target_job_titles") or [])
    f["extra_skills"] = len(pref.get("additional_skills") or [])
    f["has_linkedin"] = bool(cv.get("linkedin_raw_text"))
    f["has_github"] = bool(cv.get("github_repos_brief"))

    # STAGE 2 — extraction. Input went in; did anything come out?
    if f["cv_chars"] and f["skills"] < MIN_SKILLS:
        return "extraction", "broken", f

    # STAGE 3 — search keywords. Extraction can succeed and still produce
    # nothing to search WITH, which is the same as having no product.
    if f["skills"] == 0 and f["cv_titles"] == 0 and f["target_titles"] == 0:
        return "search-keywords", "broken", f

    # STAGE 4 — the feed. Keywords existed; did any job reach this person?
    f["feed_rows"] = one("SELECT count(*) FROM user_feed WHERE user_id=%s")
    if f["feed_rows"] < MIN_FEED:
        return "feed", "broken", f

    # STAGE 5 — scoring. Rows exist; are the scores meaningful, or all one value?
    f["max_score"] = one("SELECT coalesce(max(score),0) FROM user_feed WHERE user_id=%s")
    f["distinct_scores"] = one(
        "SELECT count(DISTINCT score) FROM user_feed WHERE user_id=%s"
    )
    if f["max_score"] == 0 or f["distinct_scores"] < 2:
        return "scoring", "broken", f

    # STAGE 6 — the dashboard. Scored rows exist; do any survive the filter?
    cur.execute(
        "SELECT count(*) FROM user_feed WHERE user_id=%s AND score >= %s",
        (uid, DISPLAY_FLOOR),
    )
    f["visible"] = cur.fetchone()[0]
    f["visible_pct"] = round(f["visible"] / f["feed_rows"] * 100) if f["feed_rows"] else 0
    if f["visible"] == 0:
        return "dashboard", "broken", f

    # Quality signals that are NOT failures — worth reporting, not blocking.
    f["llm_verdicts"] = one(
        "SELECT count(*) FROM user_feed WHERE user_id=%s AND llm_fit_score IS NOT NULL"
    )
    return "complete", "ok", f


def main() -> int:
    dsn = os.getenv("PROD_DATABASE_URL") or os.getenv("DATABASE_PUBLIC_URL")
    if not dsn:
        print("::error::no PROD_DATABASE_URL - the user journey audit cannot run")
        return 2
    try:
        import psycopg
    except ImportError:
        print("::error::psycopg not installed")
        return 2

    STAGES = [
        "signup", "extraction", "search-keywords", "feed", "scoring",
        "dashboard", "complete",
    ]

    try:
        with psycopg.connect(dsn, connect_timeout=25) as conn, conn.cursor() as cur:
            cur.execute("SELECT id, email FROM users ORDER BY created_at")
            users = cur.fetchall()
            results = [(uid, email, *audit_user(cur, uid, email)) for uid, email in users]
    except Exception as exc:  # noqa: BLE001
        print(f"::error::user journey audit could not run: {exc}")
        return 2

    onboarded = [r for r in results if r[3] != "not-started"]
    broken = [r for r in onboarded if r[3] == "broken"]

    print("# User journey audit - where does the pipeline break, per person?\n")
    print(f"- Users total: **{len(results)}**")
    print(f"- Onboarded (gave us input): **{len(onboarded)}**")
    print(f"- Broken somewhere: **{len(broken)}**\n")

    if not onboarded:
        print("No onboarded users yet. Nothing to audit - an honest empty result.")
        return 0

    # Funnel: how far does each stage carry people?
    print("## The funnel\n")
    print("| stage | users who got this far |")
    print("|---|---|")
    for i, st in enumerate(STAGES):
        got_here = sum(1 for r in onboarded if STAGES.index(r[2]) >= i)
        bar = "#" * min(got_here, 30)
        print(f"| {st} | {got_here} {bar} |")
    print()

    print("## Per user\n")
    print("| user | verdict | reached | evidence |")
    print("|---|---|---|---|")
    for uid, email, stage, verdict, f in results:
        tag = {"ok": "OK", "broken": "**BROKEN**", "not-started": "-"}[verdict]
        if verdict == "not-started":
            ev = "never uploaded a CV or set preferences"
        elif verdict == "broken":
            ev = {
                "extraction": f"CV has {f.get('cv_chars',0)} chars but only "
                              f"{f.get('skills',0)} skills came out",
                "search-keywords": "nothing to search with: no skills, no CV titles, "
                                   "no target titles",
                "feed": f"only {f.get('feed_rows',0)} jobs reached this user",
                "scoring": f"max score {f.get('max_score',0)}, "
                           f"{f.get('distinct_scores',0)} distinct values - "
                           "scoring is not discriminating",
                "dashboard": f"{f.get('feed_rows',0)} scored jobs, "
                             f"{f.get('visible',0)} visible - the filter hides everything",
            }.get(stage, stage)
        else:
            ev = (f"{f.get('skills',0)} skills, {f.get('feed_rows',0)} jobs, "
                  f"{f.get('visible_pct',0)}% visible, "
                  f"{f.get('llm_verdicts',0)} AI verdicts")
        print(f"| `{str(uid)[:8]}` {mask(email)} | {tag} | {stage} | {ev} |")
    print()

    # Quality warnings: healthy users can still have a degraded experience.
    warnings = []
    for uid, email, stage, verdict, f in results:
        if verdict != "ok":
            continue
        who = f"`{str(uid)[:8]}`"
        if f.get("target_titles", 0) == 0:
            warnings.append(
                f"{who} has NO target job titles - search runs on the CV alone, so "
                "the user's own stated goal is not steering their results")
        if f.get("skills", 0) > 100:
            warnings.append(
                f"{who} has {f['skills']} extracted skills - suspiciously many. "
                "Over-extraction dilutes matching: everything looks like a partial fit")
        if not f.get("has_linkedin") and not f.get("has_github"):
            warnings.append(f"{who} has CV only - no LinkedIn or GitHub signal")
        if f.get("llm_verdicts", 0) == 0:
            warnings.append(f"{who} has no AI verdicts - the judge never ran for them")

    if warnings:
        print("## Working, but degraded\n")
        print("Not failures - nobody is blocked. But this is a worse product than "
              "we think we are shipping:\n")
        for w in warnings:
            print(f"- {w}")
        print()

    if broken:
        print("## Broken\n")
        for uid, email, stage, verdict, f in broken:
            print(f"- `{str(uid)[:8]}` breaks at **{stage}**")
        print("\nAggregate checks miss all of these: the product can look healthy "
              "on average while being completely useless for one person.")
        return 1

    print("Every onboarded user reaches the dashboard with visible, scored jobs.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
