"""One-time: empty the "Added by you" box for a user whose entries are NOT his.

WHY THIS IS CLEANUP, NOT DATA DELETION.

An old extraction bug wrote CV content into ``preferences.additional_skills``
(the "Added by you" box). The sanitizer in ``save_profile`` removes entries that
are verbatim duplicates of an extracted skill, which took one live profile from
131 -> 103 -> 24. The residual 24 survive only because they are not EXACT
matches — they are the SPLIT HALVES of skills the CV holds joined:

    cv.skills            "Audio Processing"  "Transfer Learning"  "CI/CD Pipelines"
    additional_skills    "Audio" "Processing" "Transfer" "Learning" "CI" "CD Pipelines"

plus a location ("Remote", from a position location "United Kingdom (Remote)")
and a past job title ("ML Engineer Intern", a substring of "AI/ML Engineer
Intern"). The mechanism, the provenance, and the owner's own testimony ("did you
add that or is it just populating on its own?" — he typed none of them) all
agree: this is bug output, not user input.

WHY NOT A CLEVER RULE INSTEAD. A "drop anything that is a sub-phrase of an
extracted skill" heuristic would catch these, but it is NOT zero-loss: if
``cv.skills`` held "Docker Compose" and the user genuinely typed "Docker", it
would delete a real, distinct skill. It would also be a permanent heuristic
guarding a box that extraction can no longer write to — cleverness defending
against a threat that is already dead. A targeted one-time reset is the honest
tool for a one-time mess.

SAFETY.
  * Writes via ``save_profile``, which appends a version snapshot first — the
    previous list stays in ``user_profile_versions``, so this is reversible.
  * Requires an explicit --email. Refuses to run across all users: the
    justification is one user's testimony, and nobody else has given it.
  * Does NOT touch cv_data. Every skill remains visible under its real source
    (CV / LinkedIn / GitHub) and keeps feeding matching from there.
  * Idempotent — an already-empty box is a no-op.

Usage:
    railway run -s Postgres bash -c \
      'export DATABASE_URL="$DATABASE_PUBLIC_URL"; \
       python scripts/reset_polluted_additional_skills.py --email X --apply'
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def main(email: str, apply: bool) -> int:
    import psycopg

    from src.services.profile.models import CVData, UserPreferences, UserProfile
    from src.services.profile.storage import _filter_fields, save_profile

    dsn = os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("set DATABASE_PUBLIC_URL or DATABASE_URL")
    conn = psycopg.connect(dsn)
    cur = conn.cursor()
    cur.execute(
        "SELECT u.id, p.cv_data, p.preferences FROM user_profiles p "
        "JOIN users u ON u.id = p.user_id WHERE u.email = %s",
        (email,),
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        raise SystemExit(f"no profile for {email}")

    uid, cv_json, pref_json = row
    cv = CVData(**_filter_fields(json.loads(cv_json), CVData))
    prefs = UserPreferences(**_filter_fields(json.loads(pref_json), UserPreferences))
    before = list(prefs.additional_skills or [])
    if not before:
        print("already empty — nothing to do")
        return 0

    # Show the evidence, so the operator sees WHAT is being dropped and can
    # confirm each entry is reachable elsewhere before agreeing to the write.
    extracted = set()
    for f in ("skills", "linkedin_skills", "github_llm_skills", "github_skills_inferred"):
        for s in getattr(cv, f, []) or []:
            if isinstance(s, str):
                extracted.add(s.strip().lower())
    print(f"{email}: additional_skills = {len(before)}")
    for s in before:
        low = s.strip().lower()
        covered = [e for e in extracted if low in e and low != e]
        note = f"fragment of: {covered[0]}" if covered else "no joined form found"
        print(f"  - {s:<28} ({note})")

    if not apply:
        print(f"\nDRY RUN — would clear all {len(before)}. Re-run with --apply.")
        return 0

    prefs.additional_skills = []
    save_profile(
        UserProfile(cv_data=cv, preferences=prefs),
        uid,
        "additional_skills_pollution_reset",
    )
    print(f"\nCLEARED {len(before)} entries. Previous list is preserved in "
          "user_profile_versions (rollback available).")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", required=True, help="the ONE user to reset")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    raise SystemExit(main(a.email, a.apply))
