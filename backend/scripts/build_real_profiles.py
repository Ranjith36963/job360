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
        "uid": "eval-crajappa",  # Senior Security Analyst (SOC/SIEM), 5yr, India -> UK/remote
        "cv": f"{ROOT}/User_info/CV/CRajappa_SOCL2-5Years-1.pdf",
        "linkedin": None,
        "github": None,
        "prefs": dict(experience_level="senior", work_arrangement="remote",
                      salary_min=50000, salary_max=85000,
                      needs_visa=True),
    },
    {
        "uid": "eval-rohith",  # Senior Data & Backend Engineer, 7yr, London
        "cv": f"{ROOT}/User_info/CV/Rohith_Govindarajan_v7.pdf",
        "linkedin": f"{ROOT}/User_info/Linkedin_pdf/rohith_Profile .pdf",
        "github": None,
        "prefs": dict(experience_level="senior", work_arrangement="hybrid",
                      salary_min=70000, salary_max=110000,
                      needs_visa=False),
    },
    {
        "uid": "eval-sofia",  # Cyber Security Analyst (student/junior), pen-test, UK
        "cv": f"{ROOT}/User_info/CV/Sofia Shaji Lekha.pdf",
        "linkedin": f"{ROOT}/User_info/Linkedin_pdf/Sofia LinkedIn.pdf",
        "github": "sofiashajilekha",
        "prefs": dict(experience_level="junior", work_arrangement="remote",
                      salary_min=30000, salary_max=50000,
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

    if spec.get("linkedin"):
        try:
            ld = parse_linkedin_pdf(spec["linkedin"])
            cv = enrich_cv_from_linkedin(cv, ld)
            note += f" | +LinkedIn ({len(ld.get('positions', []))} positions)"
        except Exception as e:  # noqa: BLE001
            note += f" | LinkedIn skip ({type(e).__name__})"
    else:
        note += " | no LinkedIn"

    if spec.get("github"):
        try:
            gh = asyncio.run(fetch_github_profile(spec["github"]))
            cv = enrich_cv_from_github(cv, gh)
            note += f" | +GitHub ({len(gh.get('repositories', []))} repos)"
        except Exception as e:  # noqa: BLE001
            note += f" | GitHub skip ({type(e).__name__})"
    else:
        note += " | no GitHub"

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
