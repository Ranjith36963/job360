"""Prep step for the multi-profile eval: for each fake profile, set it up under
its own user id, score the shared catalog live (keyword), sample ~24 jobs across
the relevance range, write them to that user's feed, and emit a grading sheet.

Run: python scripts/prep_fake_profiles.py
Then: grade each sheet -> golds, judge each feed, run scripts/score_fake_profiles.py
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import sqlite3

logging.disable(logging.WARNING)

SAMPLE_PER_BAND = 8  # 8 high + 8 mid + 8 low = 24 per profile
SHEET = r"C:/Users/Ranjith/AppData/Local/Temp/fake_grade_sheets.txt"
IDS_OUT = r"C:/Users/Ranjith/AppData/Local/Temp/fake_sample_ids.json"


async def _main() -> None:
    from scripts.fake_profiles import PROFILES, build_user_profile
    from src.core.settings import DB_PATH
    from src.models import Job
    from src.services.profile.keyword_generator import generate_search_config
    from src.services.profile.storage import current_profile_version_id, save_profile
    from src.services.skill_matcher import JobScorer

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    catalog = [dict(r) for r in conn.execute(
        "SELECT id,title,company,location,description,salary_min,salary_max,date_found,"
        "posted_at,date_confidence FROM jobs"
    )]

    all_ids: dict = {}
    sheet = io.open(SHEET, "w", encoding="utf-8")
    for spec in PROFILES:
        uid = f"fake-{spec['id']}"
        profile = build_user_profile(spec)
        save_profile(profile, uid, source_action="user_edit")
        cfg = generate_search_config(profile)
        scorer = JobScorer(cfg, user_preferences=None)  # keyword-only

        scored = []
        for r in catalog:
            j = Job(title=r["title"] or "", company=r["company"] or "c",
                    apply_url="u", source="s", date_found=r["date_found"] or "2026-01-01",
                    location=r["location"] or "", description=r["description"] or "")
            scored.append((r, scorer.score(j).match_score))
        scored.sort(key=lambda x: -x[1])
        hi = [x for x in scored if x[1] >= 40]
        mid = [x for x in scored if 20 <= x[1] < 40]
        lo = [x for x in scored if x[1] < 20]

        def pick(lst, n):
            if len(lst) <= n:
                return lst
            step = len(lst) / n
            return [lst[int(i * step)] for i in range(n)]

        sample = pick(hi, SAMPLE_PER_BAND) + pick(mid, SAMPLE_PER_BAND) + pick(lo, SAMPLE_PER_BAND)
        ids = [r["id"] for r, _ in sample]
        all_ids[uid] = ids

        # Write a clean per-user feed (replace).
        ver = current_profile_version_id(uid)
        conn.execute("DELETE FROM user_feed WHERE user_id=?", (uid,))
        for (r, kw) in sample:
            conn.execute(
                "INSERT INTO user_feed(user_id,job_id,score,bucket,status,profile_version) "
                "VALUES (?,?,?,?,'active',?)",
                (uid, r["id"], int(kw), "top", ver),
            )
        conn.commit()

        sheet.write(f"\n########## PROFILE: {spec['role']} (user {uid}) ##########\n")
        sheet.write(f"CV: {spec['headline']}. Skills: {', '.join(spec['skills'][:14])}.\n")
        p = spec["prefs"]
        sheet.write(f"PREFS: {p['experience_level']}, GBP {p['salary_min']}-{p['salary_max']}, "
                    f"{p['workplace']}, visa={p['needs_visa']}.\n")
        sheet.write("Grade 0-100 fit (domain/role, seniority, skills, location/remote+visa, salary).\n\n")
        for (r, kw) in sample:
            desc = (r["description"] or "").replace("\n", " ")[:300]
            sheet.write(f"== {r['id']} | kw={kw} == {r['title']} | {r['company']} | {r['location']}\n{desc}\n\n")
        print(f"{spec['role']:<28} sampled {len(sample)} (hi={len(hi)} mid={len(mid)} lo={len(lo)})")

    sheet.close()
    json.dump(all_ids, open(IDS_OUT, "w"))
    conn.close()
    print(f"\nGrading sheet -> {SHEET}\nSample ids -> {IDS_OUT}")


if __name__ == "__main__":
    asyncio.run(_main())
