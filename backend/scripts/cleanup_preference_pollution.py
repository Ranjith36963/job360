"""One-time cleanup: scrub extraction pollution from stored user preferences.

WHY. Found on a live smoke test 2026-08-08: a real user's `additional_skills`
held 131 entries, 28 of them non-skills (CV job titles, companies, locations,
date ranges, whole experience-bullet sentences), and `target_job_titles` held
his PAST roles instead of target roles. An older extraction merge dumped CV
content into the preference boxes. The current merge is clean and
`_apply_preferences` now runs `sanitize_preferences` on every save — so new
saves self-heal — but existing rows only heal when the user next saves. This
scrubs them now.

RUN ORDER MATTERS. Deploy the sanitizer-in-save-path fix FIRST. Otherwise the
user's open browser tab autosaves the old chips back over this cleanup minutes
later (the frontend round-trips the loaded preferences).

SAFE BY DESIGN.
  * Uses the SAME `sanitize_preferences` the save path uses — zero-loss (drops
    only CV-duplicate content + structural non-skills, never a real skill).
  * Writes via `save_profile`, which appends a version snapshot → built-in
    rollback if anything looks wrong.
  * Then re-scores the feed so ranking reflects the cleaned preferences.
  * Does NOT route through the extraction trigger (that burns paid LLM calls).

Idempotent — a clean profile is a no-op. Pass --apply to write; default dry run.
Usage:  railway run -s Postgres python scripts/cleanup_preference_pollution.py [--email X] [--apply]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

if os.name == "nt":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


async def main(email: str | None, apply: bool) -> int:
    import psycopg

    from src.services.profile.models import CVData, UserPreferences
    from src.services.profile.preferences import sanitize_preferences
    from src.services.profile.storage import _filter_fields, save_profile

    dsn = os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("set DATABASE_PUBLIC_URL or DATABASE_URL")
    conn = psycopg.connect(dsn)
    cur = conn.cursor()
    if email:
        cur.execute(
            "SELECT u.id, u.email, p.cv_data, p.preferences FROM user_profiles p "
            "JOIN users u ON u.id = p.user_id WHERE u.email = %s", (email,)
        )
    else:
        cur.execute(
            "SELECT u.id, u.email, p.cv_data, p.preferences FROM user_profiles p "
            "JOIN users u ON u.id = p.user_id"
        )
    rows = cur.fetchall()
    conn.close()

    changed = 0
    for uid, mail, cv_json, pref_json in rows:
        cv = CVData(**_filter_fields(json.loads(cv_json), CVData))
        prefs = UserPreferences(**_filter_fields(json.loads(pref_json), UserPreferences))
        before_add, before_titles = len(prefs.additional_skills), len(prefs.target_job_titles)
        clean = sanitize_preferences(prefs, cv)
        drop_add = before_add - len(clean.additional_skills)
        drop_title = before_titles - len(clean.target_job_titles)
        if not drop_add and not drop_title:
            continue
        changed += 1
        print(f"{mail}: additional_skills {before_add}->{len(clean.additional_skills)} "
              f"(-{drop_add}), target_titles {before_titles}->{len(clean.target_job_titles)} "
              f"(-{drop_title})")
        if apply:
            from src.services.profile.models import UserProfile
            save_profile(
                UserProfile(cv_data=cv, preferences=clean),
                uid, "preference_pollution_cleanup",
            )

    if not apply:
        print(f"\nDRY RUN — {changed} profile(s) would be cleaned. Re-run with --apply.")
        return 0

    print(f"\nCLEANED {changed} profile(s). Re-scoring their feeds...")
    from src.services.rescore import rescore_user_feed
    # Re-score so ranking reflects the cleaned preferences. Best-effort per user.
    for uid, mail, _cv, _pr in rows:
        try:
            await rescore_user_feed(uid)
        except Exception as exc:  # noqa: BLE001 — one user's rescore must not abort the rest
            print(f"  rescore failed for {mail}: {type(exc).__name__}: {str(exc)[:80]}")
    print("done.")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", default=None, help="clean one user; omit for all")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    raise SystemExit(asyncio.run(main(a.email, a.apply)))
