"""Build TWO real profiles from the User_info CV/LinkedIn/GitHub files and save
each under an isolated eval user (so no real feed is touched).

People:
  * eval-ranjith  — Ranjith (AI/ML Engineer, ~1.5yr, GenAI/RAG)
  * eval-pavan    — Pavan   (AI/ML graduate, CV/NLP, intern/junior)

Run: python -m scripts.build_real_profiles
"""
from __future__ import annotations

import logging

logging.disable(logging.WARNING)

ROOT = ".."  # backend/ -> repo root
PEOPLE = [
    {
        "uid": "eval-ranjith",
        "cv": f"{ROOT}/User_info/CV/RanjithMG-AI-ML.pdf",
        "linkedin": f"{ROOT}/User_info/Linkedin_pdf/Profile.pdf",
        "github": "Ranjith36963",
        "prefs": dict(experience_level="mid", work_arrangement="remote",
                      preferred_workplace="remote", salary_min=40000, salary_max=70000,
                      needs_visa=True),
    },
    {
        "uid": "eval-pavan",
        "cv": f"{ROOT}/User_info/CV/Pavan_Alakunta_CV.pdf",
        "linkedin": f"{ROOT}/User_info/Linkedin_pdf/Profile-pavan.pdf",
        "github": "Pavan09-Is-Here",
        "prefs": dict(experience_level="junior", work_arrangement="remote",
                      preferred_workplace="remote", salary_min=30000, salary_max=55000,
                      needs_visa=True),
    },
]


def build_one(spec) -> None:
    import asyncio

    from src.services.profile.cv_parser import parse_cv
    from src.services.profile.github_enricher import enrich_cv_from_github, fetch_github_profile
    from src.services.profile.linkedin_parser import enrich_cv_from_linkedin, parse_linkedin_pdf
    from src.services.profile.models import UserPreferences, UserProfile
    from src.services.profile.preferences import merge_cv_and_preferences
    from src.services.profile.storage import save_profile

    uid = spec["uid"]
    cv = parse_cv(spec["cv"])
    note = f"{uid}: CV parsed — {len(cv.skills or [])} skills, titles={list(cv.job_titles or [])[:3]}"

    try:
        ld = parse_linkedin_pdf(spec["linkedin"])
        cv = enrich_cv_from_linkedin(cv, ld)
        note += f" | +LinkedIn ({len(ld.get('positions', []))} positions)"
    except Exception as e:  # noqa: BLE001
        note += f" | LinkedIn skip ({type(e).__name__})"

    try:
        gh = asyncio.run(fetch_github_profile(spec["github"]))
        cv = enrich_cv_from_github(cv, gh)
        note += f" | +GitHub ({len(gh.get('repositories', []))} repos)"
    except Exception as e:  # noqa: BLE001
        note += f" | GitHub skip ({type(e).__name__})"

    prefs = UserPreferences(github_username=spec["github"], **spec["prefs"])
    prefs = merge_cv_and_preferences(cv.skills, cv.job_titles, prefs)
    # merge_cv_and_preferences rebuilds a fresh UserPreferences and drops the
    # dimension inputs — re-apply them so E2 (seniority/salary/visa/workplace)
    # can actually score.
    for k, v in spec["prefs"].items():
        setattr(prefs, k, v)
    prefs.github_username = spec["github"]
    profile = UserProfile(cv_data=cv, preferences=prefs)
    save_profile(profile, uid, source_action="user_edit")
    print(note)


def main() -> None:
    for spec in PEOPLE:
        try:
            build_one(spec)
        except Exception as e:  # noqa: BLE001
            print(f"{spec['uid']}: BUILD FAILED — {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
