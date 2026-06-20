"""Prep the REAL user's profile for an engine eval — non-destructively.

Clones the real profile to an isolated ``eval-real`` user (so the real user's
own 100-job feed/dashboard is never touched), scores the whole catalog with
keyword-only JobScorer, samples 8 high / 8 mid / 8 low jobs across the
relevance range, writes them to the eval-real feed, and emits a grading sheet.

Run: python -m scripts.prep_real_profile
Then: grade the sheet -> real_gold.json, judge the feed, run scripts.score_real_profile
"""
from __future__ import annotations

import io
import json
import logging
import sqlite3

logging.disable(logging.WARNING)

REAL_UID = "6da07533affa4a0b9562996fae8f13ef"  # Ranjith — Full-Stack/Backend Engineer
EVAL_UID = "eval-real"
SAMPLE_PER_BAND = 8
SHEET = r"C:/Users/Ranjith/AppData/Local/Temp/real_grade_sheet.txt"
IDS_OUT = r"C:/Users/Ranjith/AppData/Local/Temp/real_sample_ids.json"


def main() -> None:
    from src.core.settings import DB_PATH
    from src.models import Job
    from src.services.profile.keyword_generator import generate_search_config
    from src.services.profile.storage import (
        current_profile_version_id,
        load_profile,
        save_profile,
    )
    from src.services.skill_matcher import JobScorer

    profile = load_profile(REAL_UID)
    if profile is None:
        raise SystemExit(f"no profile for real user {REAL_UID}")
    save_profile(profile, EVAL_UID, source_action="user_edit")

    cfg = generate_search_config(profile)
    scorer = JobScorer(cfg, user_preferences=None)  # keyword-only banding

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    catalog = [
        dict(r)
        for r in conn.execute(
            "SELECT id,title,company,location,description,date_found FROM jobs"
        )
    ]

    scored = []
    for r in catalog:
        j = Job(
            title=r["title"] or "",
            company=r["company"] or "c",
            apply_url="u",
            source="s",
            date_found=r["date_found"] or "2026-01-01",
            location=r["location"] or "",
            description=r["description"] or "",
        )
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

    ver = current_profile_version_id(EVAL_UID)
    conn.execute("DELETE FROM user_feed WHERE user_id=?", (EVAL_UID,))
    for (r, kw) in sample:
        conn.execute(
            "INSERT INTO user_feed(user_id,job_id,score,bucket,status,profile_version) "
            "VALUES (?,?,?,?,'active',?)",
            (EVAL_UID, r["id"], int(kw), "top", ver),
        )
    conn.commit()

    cv = profile.cv_data
    sheet = io.open(SHEET, "w", encoding="utf-8")
    sheet.write(f"########## REAL PROFILE: {cv.name} (user {EVAL_UID}) ##########\n")
    sheet.write(f"Titles: {', '.join((cv.job_titles or [])[:6])}\n")
    sheet.write(f"Skills: {', '.join((cv.skills or [])[:20])}\n")
    p = profile.preferences
    sheet.write(
        f"PREFS: level={getattr(p,'experience_level',None)}, "
        f"salary={getattr(p,'salary_min',None)}-{getattr(p,'salary_max',None)}, "
        f"workplace={getattr(p,'preferred_workplace',None)}, visa={getattr(p,'needs_visa',None)}\n"
    )
    sheet.write("Grade 0-100 fit (domain/role, seniority, skills, location/remote+visa, salary).\n\n")
    for (r, kw) in sample:
        desc = (r["description"] or "").replace("\n", " ")[:320]
        sheet.write(f"== {r['id']} | kw={kw} == {r['title']} | {r['company']} | {r['location']}\n{desc}\n\n")
    sheet.close()

    json.dump({EVAL_UID: ids}, open(IDS_OUT, "w"))
    conn.close()
    print(f"sampled {len(sample)} (hi={len(hi)} mid={len(mid)} lo={len(lo)})")
    print(f"grading sheet -> {SHEET}")
    print(f"sample ids -> {IDS_OUT}")


if __name__ == "__main__":
    main()
