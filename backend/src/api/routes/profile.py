"""Profile routes for Job360 FastAPI backend."""
from __future__ import annotations

import json
import os
from dataclasses import asdict

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from src.api.dependencies import get_db, save_upload_to_temp
from src.api.models import CVDetail, GitHubResponse, LinkedInResponse, ProfileResponse, ProfileSummary
from src.services.profile.cv_parser import parse_cv_async
from src.services.profile.github_enricher import enrich_cv_from_github, fetch_github_profile
from src.services.profile.linkedin_parser import enrich_cv_from_linkedin, parse_linkedin_zip
from src.services.profile.models import UserPreferences, UserProfile
from src.services.profile.preferences import merge_cv_and_preferences
from src.services.profile.storage import load_profile, save_profile

router = APIRouter(tags=["profile"])


def _build_profile_response(profile: UserProfile) -> ProfileResponse:
    summary = ProfileSummary(
        is_complete=profile.is_complete,
        job_titles=profile.cv_data.job_titles,
        skills_count=len(profile.cv_data.skills),
        cv_length=len(profile.cv_data.raw_text),
        has_linkedin=bool(profile.cv_data.linkedin_skills),
        has_github=bool(profile.cv_data.github_languages),
        education=profile.cv_data.education,
        experience_level=profile.preferences.experience_level,
    )
    cv = profile.cv_data
    cv_detail = CVDetail(
        raw_text=cv.raw_text,
        skills=cv.skills,
        job_titles=cv.job_titles,
        companies=getattr(cv, "companies", []),
        education=cv.education,
        certifications=cv.certifications,
        summary_text=cv.summary,
        experience_text=getattr(cv, "experience_text", ""),
        name=getattr(cv, "name", ""),
        headline=getattr(cv, "headline", ""),
        location=getattr(cv, "location", ""),
        achievements=getattr(cv, "achievements", []),
        highlights=cv.highlights if hasattr(cv, "highlights") else cv.skills,
    )
    return ProfileResponse(
        summary=summary,
        preferences=asdict(profile.preferences),
        cv_detail=cv_detail if cv.raw_text else None,
    )


@router.get("/profile", response_model=ProfileResponse)
async def get_profile():
    """Return the current user profile summary."""
    profile = load_profile()
    if profile is None:
        raise HTTPException(status_code=404, detail="No profile found")
    return _build_profile_response(profile)


@router.post("/profile", response_model=ProfileResponse)
async def upsert_profile(
    cv: UploadFile = File(None),
    preferences: str = Form(None),
):
    """Create or update the user profile with CV and/or preferences."""
    profile = load_profile() or UserProfile()

    # Parse CV if provided
    if cv is not None:
        content = await cv.read()
        suffix = os.path.splitext(cv.filename or ".pdf")[1] or ".pdf"
        tmp_path = save_upload_to_temp(content, suffix)
        try:
            try:
                cv_data = await parse_cv_async(tmp_path)
            except RuntimeError as e:
                raise HTTPException(status_code=503, detail=str(e))
            profile.cv_data = cv_data
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    # Parse preferences if provided
    if preferences is not None:
        pref_dict = json.loads(preferences)
        prefs = UserPreferences(
            target_job_titles=pref_dict.get("target_job_titles", []),
            additional_skills=pref_dict.get("additional_skills", []),
            excluded_skills=pref_dict.get("excluded_skills", []),
            preferred_locations=pref_dict.get("preferred_locations", []),
            industries=pref_dict.get("industries", []),
            salary_min=pref_dict.get("salary_min"),
            salary_max=pref_dict.get("salary_max"),
            work_arrangement=pref_dict.get("work_arrangement", ""),
            experience_level=pref_dict.get("experience_level", ""),
            negative_keywords=pref_dict.get("negative_keywords", []),
            about_me=pref_dict.get("about_me", ""),
            github_username=pref_dict.get("github_username", ""),
        )
        profile.preferences = prefs

    # Merge CV skills/titles into preferences if CV has data
    if profile.cv_data.skills or profile.cv_data.job_titles:
        merged_prefs = merge_cv_and_preferences(
            profile.cv_data.skills,
            profile.cv_data.job_titles,
            profile.preferences,
        )
        profile.preferences = merged_prefs

    save_profile(profile)
    return _build_profile_response(profile)


@router.post("/profile/linkedin", response_model=LinkedInResponse)
async def upload_linkedin(file: UploadFile = File(...)):
    """Enrich user profile with LinkedIn data export ZIP."""
    content = await file.read()
    tmp_path = save_upload_to_temp(content, ".zip")
    try:
        linkedin_data = parse_linkedin_zip(tmp_path)
        profile = load_profile() or UserProfile()
        profile.cv_data = enrich_cv_from_linkedin(profile.cv_data, linkedin_data)
        save_profile(profile)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
    return LinkedInResponse(ok=True, merged=True)


@router.post("/profile/github", response_model=GitHubResponse)
async def upload_github(username: str = Form(...)):
    """Enrich user profile with GitHub public data."""
    github_data = await fetch_github_profile(username)
    profile = load_profile() or UserProfile()
    profile.cv_data = enrich_cv_from_github(profile.cv_data, github_data)
    profile.preferences.github_username = username
    save_profile(profile)
    return GitHubResponse(ok=True, merged=True)
