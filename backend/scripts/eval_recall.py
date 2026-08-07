"""RECALL AUDIT — what does the funnel DROP?

Every accuracy number this system has produced (39% -> 66% -> 78% -> 90%
strong-fit precision) measures the DISPLAYED top-100. None of them can see a
strong-fit job that never reached the shelf: scored low by the keyword engine,
cut from the candidate set, never judged, invisible to the precision audit by
construction. A 90%-precise top-100 is perfectly compatible with the catalog
holding 250 strong fits of which the user sees 90.

This script measures the other half. It samples jobs from OUTSIDE the user's
shelf — stratified across the score range so the answer isn't dominated by the
floor — and judges each one with the same production judge the precision audit
uses. One number out: how many strong fits are being dropped.

Why it matters beyond curiosity: semantic search's entire value hypothesis is
recall (surfacing jobs whose vocabulary the keyword scorer misses). If the miss
rate below the shelf is ~0, embeddings are a vanity feature. If it is large,
that is the next 39->90 curve, with evidence attached.

Read-only: no feed writes, no verdict writes.

Usage:
    python scripts/eval_recall.py                # biggest-feed user
    python scripts/eval_recall.py --user <id> --sample 200
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

STRONG = 65
GOOD = 50
# Bands below the shelf, so the sample is not swamped by the floor where
# almost everything is genuinely irrelevant.
BANDS = ((30, 200), (20, 29), (10, 19), (0, 9))


async def main(user_id: str | None, sample_size: int, shelf: int) -> int:
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
            print("no user with a feed + profile")
            return 0
        user_id = row[0]
    print(f"recall audit for user: {user_id}")

    cur.execute("SELECT cv_data, preferences FROM user_profiles WHERE user_id=%s", (user_id,))
    prow = cur.fetchone()
    profile = UserProfile(
        cv_data=CVData(**_filter_fields(json.loads(prow[0]), CVData)),
        preferences=UserPreferences(**_filter_fields(json.loads(prow[1]), UserPreferences)),
    )
    ptxt = profile_to_matcher_text(profile)
    print(f"judge sees {len(ptxt.split())} words of this candidate")

    # The shelf = what the user can actually reach: their top-`shelf` active
    # rows by display rank. Everything else is "dropped" for this measurement,
    # whether it is a low-scored active row or evicted entirely.
    cur.execute(
        "SELECT job_id FROM user_feed WHERE user_id=%s AND status='active' "
        "ORDER BY COALESCE(llm_fit_score, score) DESC LIMIT %s",
        (user_id, shelf),
    )
    on_shelf = {r[0] for r in cur.fetchall()}
    print(f"shelf size: {len(on_shelf)}")

    cur.execute(
        "SELECT j.id, j.title, j.company, j.location, j.description, "
        "       j.salary_min, j.salary_max, COALESCE(f.score, 0) "
        "FROM jobs j LEFT JOIN user_feed f "
        "  ON f.job_id = j.id AND f.user_id = %s "
        "WHERE (j.staleness_state IS NULL OR j.staleness_state = 'active')",
        (user_id,),
    )
    off_shelf = [r for r in cur.fetchall() if r[0] not in on_shelf]
    conn.close()
    print(f"jobs OFF the shelf: {len(off_shelf)}")
    if not off_shelf:
        print("nothing is being dropped — recall is trivially complete")
        return 0

    random.seed(360)
    per_band = max(1, sample_size // len(BANDS))
    picked: list = []
    band_sizes: dict[str, int] = {}
    for lo, hi in BANDS:
        pool = [r for r in off_shelf if lo <= (r[7] or 0) <= hi]
        band_sizes[f"{lo}-{hi}"] = len(pool)
        picked.extend(random.sample(pool, min(per_band, len(pool))))
    print(f"population by band: {band_sizes}")
    print(f"judging {len(picked)} sampled off-shelf jobs", flush=True)

    sem = asyncio.Semaphore(3)
    results: list[dict] = []

    async def judge(r) -> dict:
        job = Job(title=r[1] or "", company=r[2] or "", apply_url="", source="",
                  date_found="", location=r[3] or "", description=(r[4] or "")[:6000],
                  salary_min=r[5], salary_max=r[6])
        job.id = r[0]
        async with sem:
            try:
                v = await match_job(ptxt, job, None)
                return {"id": r[0], "title": r[1], "company": r[2],
                        "kw": r[7], "desc": len(r[4] or ""), "fit": v.fit_score,
                        "verdict": v.verdict}
            except Exception as e:  # noqa: BLE001
                return {"id": r[0], "error": str(e)[:80]}

    for i in range(0, len(picked), 30):
        chunk = picked[i:i + 30]
        results.extend(await asyncio.gather(*[judge(r) for r in chunk]))
        print(f"progress {min(i + 30, len(picked))}/{len(picked)}", flush=True)

    ok = [r for r in results if "fit" in r]
    errs = [r for r in results if "error" in r]
    if errs:
        print(f"judge errors: {len(errs)} (sample: {errs[0].get('error')})")
    if not ok:
        print("no judgments — cannot report recall")
        return 2

    strong = [r for r in ok if r["fit"] >= STRONG]
    good = [r for r in ok if r["fit"] >= GOOD]
    rate = len(strong) / len(ok)

    print("\n===== RECALL SCOREBOARD (jobs the user never sees) =====")
    print(f"sampled off-shelf: {len(ok)}")
    print(f"strong-fit (>={STRONG}): {len(strong)}  ({round(100 * rate)}%)")
    print(f"good-fit  (>={GOOD}): {len(good)}  ({round(100 * len(good) / len(ok))}%)")
    print(f"EXTRAPOLATED strong fits dropped across {len(off_shelf)} off-shelf jobs: "
          f"~{round(rate * len(off_shelf))}")
    print("\nby score band (where the misses live):")
    for lo, hi in BANDS:
        b = [r for r in ok if lo <= (r["kw"] or 0) <= hi]
        if b:
            s = sum(1 for r in b if r["fit"] >= STRONG)
            print(f"  kw {lo}-{hi}: {s}/{len(b)} strong  (avg fit {sum(x['fit'] for x in b) // len(b)})")
    print("\nworst misses (highest fit, lowest keyword score):")
    for r in sorted(strong, key=lambda x: (x["kw"] or 0))[:10]:
        print(f"  fit={r['fit']} kw={r['kw']} desc={r['desc']}c "
              f"{str(r['title'])[:46]} @ {str(r['company'])[:22]}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", default=None)
    ap.add_argument("--sample", type=int, default=160)
    ap.add_argument("--shelf", type=int, default=800)
    args = ap.parse_args()
    raise SystemExit(asyncio.run(main(args.user, args.sample, args.shelf)))
