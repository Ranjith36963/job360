"""Prep the two real profiles (eval-ranjith, eval-pavan) for the engine eval.

Profiles are already saved (scripts.build_real_profiles). For each: keyword-score
the catalog, sample 8 high / 8 mid / 8 low, write the user's feed, emit a grading
sheet. Run: python -m scripts.prep_two_real
"""
from __future__ import annotations

import io
import json
import logging
import sqlite3

logging.disable(logging.WARNING)

UIDS = ["eval-ranjith", "eval-pavan"]
SAMPLE_PER_BAND = 8
SHEET = r"C:/Users/Ranjith/AppData/Local/Temp/two_real_grade_sheet.txt"
IDS_OUT = r"C:/Users/Ranjith/AppData/Local/Temp/two_real_sample_ids.json"


def main() -> None:
    from src.core.settings import DB_PATH
    from src.models import Job
    from src.services.profile.keyword_generator import generate_search_config
    from src.services.profile.storage import current_profile_version_id, load_profile
    from src.services.skill_matcher import JobScorer

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    catalog = [
        dict(r)
        for r in conn.execute(
            "SELECT id,title,company,location,description,date_found FROM jobs"
        )
    ]

    all_ids: dict = {}
    sheet = io.open(SHEET, "w", encoding="utf-8")
    for uid in UIDS:
        profile = load_profile(uid)
        cfg = generate_search_config(profile)
        scorer = JobScorer(cfg, user_preferences=None)

        scored = []
        for r in catalog:
            j = Job(title=r["title"] or "", company=r["company"] or "c", apply_url="u",
                    source="s", date_found=r["date_found"] or "2026-01-01",
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

        ver = current_profile_version_id(uid)
        conn.execute("DELETE FROM user_feed WHERE user_id=?", (uid,))
        for (r, kw) in sample:
            conn.execute(
                "INSERT INTO user_feed(user_id,job_id,score,bucket,status,profile_version) "
                "VALUES (?,?,?,?,'active',?)",
                (uid, r["id"], int(kw), "top", ver),
            )
        conn.commit()

        cv = profile.cv_data
        p = profile.preferences
        sheet.write(f"\n########## {cv.name} ({uid}) ##########\n")
        sheet.write(f"Titles: {', '.join((cv.job_titles or [])[:6])}\n")
        sheet.write(f"Skills: {', '.join((cv.skills or [])[:18])}\n")
        sheet.write(
            f"PREFS: level={p.experience_level}, salary={p.salary_min}-{p.salary_max}, "
            f"workplace={p.preferred_workplace}, visa={p.needs_visa}\n"
        )
        sheet.write("Grade 0-100 fit (domain/role, seniority, skills, location/remote+visa, salary).\n\n")
        for (r, kw) in sample:
            desc = (r["description"] or "").replace("\n", " ")[:300]
            sheet.write(f"== {r['id']} | kw={kw} == {r['title']} | {r['company']} | {r['location']}\n{desc}\n\n")
        print(f"{uid}: sampled {len(sample)} (hi={len(hi)} mid={len(mid)} lo={len(lo)})")

    sheet.close()
    json.dump(all_ids, open(IDS_OUT, "w"))
    conn.close()
    print(f"sheet -> {SHEET}")


if __name__ == "__main__":
    main()
