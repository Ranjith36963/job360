"""The standing accuracy instrument (PR-C of the eval loop, 2026-08-06).

Ground-truth audit of the ranking: the LLM judge READS the jobs we show
(top window) and a sample of the jobs we buried, against the user's real
profile. Prints the scoreboard and exits non-zero when accuracy regresses
below the alarm floors — so a scheduled run can open an issue (the Law: an
artifact with no notifier dies).

History this instrument already earned: the manual version measured
top-100 strong precision at 39% (28% junk), drove two fix iterations
(scorer_version, Verified Window + gem rescue), and re-measured 66% then 78%
with precision@25 at 100%. This script makes that loop permanent.

Usage:
    python scripts/eval_ranking.py                 # biggest-feed user
    python scripts/eval_ranking.py --user <id>
    python scripts/eval_ranking.py --top 100 --buried 60

Read-only on the DB; LLM cost ~= (top-unjudged? no: judges ALL sampled rows
independently of cached verdicts) top+buried calls on the shared free chain.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

if os.name == "nt":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Alarm floors — tuned to fire on REGRESSION from the measured 2026-08-06
# state (78% strong @100, 6 deep-bench gems), not on day-to-day noise.
MIN_STRONG_100 = int(os.getenv("EVAL_MIN_STRONG_100", "60"))
MAX_GEMS = int(os.getenv("EVAL_MAX_GEMS", "10"))

STRONG = 65
GOOD = 50
GEM = 70


async def main(user_id: str | None, top_n: int, buried_n: int) -> int:
    import psycopg

    from src.models import Job
    from src.services.llm_matcher import match_job, profile_to_matcher_text
    from src.services.profile.models import CVData, UserPreferences, UserProfile
    from src.services.profile.storage import _filter_fields

    dsn = os.environ.get("DATABASE_URL") or ""
    conn = psycopg.connect(dsn)
    cur = conn.cursor()

    if not user_id:
        cur.execute(
            "SELECT f.user_id FROM user_feed f "
            "JOIN user_profiles p ON p.user_id = f.user_id "
            "WHERE f.status = 'active' GROUP BY f.user_id "
            "ORDER BY count(*) DESC LIMIT 1"
        )
        row = cur.fetchone()
        if not row:
            print("no user with an active feed + profile — nothing to audit")
            return 0
        user_id = row[0]
    print(f"auditing user: {user_id}")

    cur.execute("SELECT cv_data, preferences FROM user_profiles WHERE user_id=%s", (user_id,))
    row = cur.fetchone()
    profile = UserProfile(
        cv_data=CVData(**_filter_fields(json.loads(row[0]), CVData)),
        preferences=UserPreferences(**_filter_fields(json.loads(row[1]), UserPreferences)),
    )
    ptxt = profile_to_matcher_text(profile)

    base = (
        "SELECT j.id, j.title, j.company, j.location, j.description, "
        "j.salary_min, j.salary_max, f.score, f.llm_fit_score "
        "FROM user_feed f JOIN jobs j ON j.id = f.job_id "
        "WHERE f.user_id = %s AND f.status = 'active' "
        "AND (j.staleness_state IS NULL OR j.staleness_state = 'active') "
    )
    cur.execute(base + "ORDER BY COALESCE(f.llm_fit_score, f.score) DESC LIMIT %s",
                (user_id, top_n))
    top = [("top", r) for r in cur.fetchall()]

    random.seed(360)
    cur.execute(base + "ORDER BY COALESCE(f.llm_fit_score, f.score) DESC OFFSET %s LIMIT 900",
                (user_id, top_n))
    mid_all = cur.fetchall()
    buried = [("buried", r) for r in random.sample(mid_all, min(buried_n, len(mid_all)))]
    conn.close()

    work = top + buried
    unjudged_in_window = sum(1 for _, r in top if r[8] is None)
    print(f"judging: top={len(top)} buried={len(buried)} "
          f"(unjudged-in-window before audit: {unjudged_in_window})")

    sem = asyncio.Semaphore(3)
    results: list[dict] = []

    async def judge(bucket: str, r) -> dict:
        job = Job(title=r[1] or "", company=r[2] or "", apply_url="", source="",
                  date_found="", location=r[3] or "", description=(r[4] or "")[:6000],
                  salary_min=r[5], salary_max=r[6])
        job.id = r[0]
        async with sem:
            try:
                v = await match_job(ptxt, job, None)
                return {"bucket": bucket, "id": r[0], "title": r[1],
                        "kw": r[7], "fit": v.fit_score}
            except Exception as e:  # noqa: BLE001 — one failure must not kill the audit
                return {"bucket": bucket, "id": r[0], "title": r[1], "error": str(e)[:80]}

    for i in range(0, len(work), 30):
        chunk = work[i:i + 30]
        results.extend(await asyncio.gather(*[judge(b, r) for b, r in chunk]))
        print(f"progress {min(i + 30, len(work))}/{len(work)}", flush=True)

    ok = [r for r in results if "fit" in r]
    errs = [r for r in results if "error" in r]
    if errs:
        # Drill finding (2026-08-06, run 31107748440): 160/160 judge calls
        # failed INSTANTLY and the script said only "no judgments" — the
        # per-call errors were swallowed, so the alarm issue couldn't name
        # the cause. Surface a sample of distinct error strings.
        distinct = list({r["error"] for r in errs})[:5]
        print(f"judge errors: {len(errs)}/{len(results)} — sample causes:")
        for e in distinct:
            print(f"  ERROR: {e}")
    top_ok = [r for r in ok if r["bucket"] == "top"]
    buried_ok = [r for r in ok if r["bucket"] == "buried"]
    if not top_ok:
        print("audit produced no judgments — treating as failure")
        return 2

    strong100 = round(100 * sum(1 for r in top_ok if r["fit"] >= STRONG) / len(top_ok))
    good100 = round(100 * sum(1 for r in top_ok if r["fit"] >= GOOD) / len(top_ok))
    p25 = top_ok[:25]
    strong25 = round(100 * sum(1 for r in p25 if r["fit"] >= STRONG) / max(len(p25), 1))
    gems = [r for r in buried_ok if r["fit"] >= GEM]

    print("\n===== ACCURACY SCOREBOARD =====")
    print(f"precision@25 strong : {strong25}%")
    print(f"precision@100 strong: {strong100}%   (alarm floor {MIN_STRONG_100}%)")
    print(f"precision@100 good  : {good100}%")
    print(f"buried gems (fit>={GEM}): {len(gems)}/{len(buried_ok)}   (alarm ceiling {MAX_GEMS})")
    print(f"unjudged in shown window: {unjudged_in_window}")
    for g in gems[:8]:
        print(f"  gem: fit={g['fit']} kw={g['kw']} {str(g['title'])[:50]}")

    failed = strong100 < MIN_STRONG_100 or len(gems) > MAX_GEMS
    print(f"\nverdict: {'REGRESSED — investigate' if failed else 'within benchmark'}")
    return 2 if failed else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", default=None)
    ap.add_argument("--top", type=int, default=100)
    ap.add_argument("--buried", type=int, default=60)
    args = ap.parse_args()
    raise SystemExit(asyncio.run(main(args.user, args.top, args.buried)))
