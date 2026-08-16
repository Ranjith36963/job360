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

CORRECTION (2026-08-11, issue #198). The 7edd5b59 finding above was a
MISDIAGNOSIS, and it re-fired every morning for nine days. That account never
confirmed its email; `POST /api/search` is gated on that, and `user_feed` is
only ever written by a search or a profile-change re-score — so it could not
have feed rows, and nothing was broken. Traced in prod: zero `run_log` rows for
it, and its single profile write (11:53) predates the first job in the catalog
(18:46) by seven hours. STAGE 1b below is the fix. The lesson is not "be less
sensitive": it is that a detector naming the WRONG stage costs more than one
that stays quiet, because everyone who reads it hunts a bug that isn't there.

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


def audit_user(cur, uid, email, verified=True) -> tuple[str, str, dict]:
    """Return (stage_reached, verdict, facts) for one user.

    verdict is 'ok', 'broken', or 'not-started'.

    ``verified`` is ``users.email_verified_at IS NOT NULL``. See STAGE 1b.
    """
    f: dict = {"email_confirmed": bool(verified)}

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

    # STAGE 1b — the product's own front door.
    #
    # `POST /api/search` is gated on a confirmed email (api/routes/search.py:177
    # -> auth_deps.require_verified_user), and `user_feed` is ONLY ever written
    # by a search or by a profile-change re-score. So an account that never
    # confirmed its email cannot have feed rows — not because anything broke,
    # but because it never got through the door.
    #
    # This cost 9 days. Issue #198 reported user 7edd5b59 "BROKEN at feed -
    # only 0 jobs reached this user" every morning from 2026-08-03. Verified
    # against prod 2026-08-11: that account confirmed no email, ran no search
    # (`run_log` has zero rows for it), and its single profile write at
    # 11:53:40 predates the FIRST job in the catalog (18:46) by seven hours.
    # There was no feed bug. A detector that names the wrong stage sends every
    # reader after a bug that does not exist, which is worse than silence.
    #
    # Not swallowed: these users are reported in their own section, because a
    # signup funnel leaking at email confirmation is worth knowing about. It is
    # just not an alarm — nothing is broken to fix.
    if not verified:
        return "email-not-confirmed", "not-started", f

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
            cur.execute(
                "SELECT id, email, email_verified_at IS NOT NULL "
                "FROM users ORDER BY created_at"
            )
            users = cur.fetchall()
            results = [
                (uid, email, *audit_user(cur, uid, email, verified=bool(ok)))
                for uid, email, ok in users
            ]
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
            ev = (
                "signed up and gave us input, but never confirmed their email - "
                "search is gated on it, so no job can reach them"
                if stage == "email-not-confirmed"
                else "never uploaded a CV or set preferences"
            )
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

    # Stalled at the front door. Not an alarm — nothing is broken, so there is
    # nothing to repair. But a signup funnel that leaks here is still a real
    # product fact, and burying it would just swap one blind spot for another.
    stalled = [r for r in results if r[2] == "email-not-confirmed"]
    if stalled:
        print("## Signed up, gave us input, never confirmed their email\n")
        print("Not a failure of ours: `POST /api/search` requires a confirmed "
              "email, and the feed is only written by a search or a profile "
              "re-score. These people cannot have a feed by design.\n")
        for uid, _email, _stage, _verdict, f in stalled:
            print(f"- `{str(uid)[:8]}` - {f.get('skills', 0)} skills, "
                  f"{f.get('cv_chars', 0)} CV chars extracted, then stopped")
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
            # Name the cheap cause first. "The judge never ran" sounds like a
            # product fault; the usual truth is that no LLM key is set, so the
            # judge could not make one call. Reading the second as the first is
            # what cost a week of eval data (issue #238).
            warnings.append(
                f"{who} has no AI verdicts - the judge never ran for them. "
                "CHECK THE CONFIG BEFORE THE PRODUCT: with none of "
                "OPENAI/GEMINI/GROQ/CEREBRAS_API_KEY set, this is exactly what "
                "a missing credential looks like from the database")

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
