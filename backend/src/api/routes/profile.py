"""Profile routes for Job360 FastAPI backend."""

from __future__ import annotations

import json
import os
from dataclasses import asdict

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from src.api.auth_deps import CurrentUser, require_user
from src.api.dependencies import save_upload_to_temp
from src.api.models import (
    CVDetail,
    GitHubResponse,
    JsonResumeResponse,
    LinkedInResponse,
    ProfileResponse,
    ProfileSummary,
    ProfileVersionsListResponse,
    ProfileVersionSummary,
)
from src.services.profile.cv_parser import parse_cv_async
from src.services.profile.github_enricher import enrich_cv_from_github, fetch_github_profile
from src.services.profile.linkedin_parser import enrich_cv_from_linkedin, parse_linkedin_pdf
from src.services.profile.models import UserPreferences, UserProfile
from src.services.profile.preferences import merge_cv_and_preferences
from src.services.profile.storage import (
    list_profile_versions,
    load_profile,
    restore_profile_version,
    save_profile,
)

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

    # Step-1.5 S1.5-F — evidence-based tiering. Walk the profile, build
    # per-skill evidence rows, then split into primary/secondary/tertiary
    # by accumulated weight. Empty dict if the helper raises (e.g. brand
    # new profile with no fields populated).
    skill_tiers: dict[str, list[str]] = {}
    skill_provenance: dict[str, list[str]] = {}
    try:
        from src.services.profile.skill_entry import (  # noqa: PLC0415 — lazy
            build_skill_entries_from_profile,
        )
        from src.services.profile.skill_tiering import (  # noqa: PLC0415 — lazy
            collect_evidence_from_profile,
            tier_skills_by_evidence,
        )

        evidence = collect_evidence_from_profile(profile)
        primary, secondary, tertiary = tier_skills_by_evidence(evidence)
        skill_tiers = {
            "primary": primary,
            "secondary": secondary,
            "tertiary": tertiary,
        }
        # Step-1.5 S3-E — collect (skill → list[source]) directly from
        # the SkillEvidence rows (which carry the source list per skill).
        # Skip ESCO normalisation here so the route stays cheap on a hot
        # GET; ProfileResponse.skill_esco already carries the URI map.
        skill_provenance = {ev.name: list(set(ev.sources)) for ev in evidence}
        # Side benefit — ensure SkillEntry import path stays exercised
        # so a future refactor that moves the import doesn't silently
        # break the response shape.
        _ = build_skill_entries_from_profile
    except Exception:
        skill_tiers = {}
        skill_provenance = {}

    # Step-1.5 S3-E — LinkedIn sub-sections + GitHub temporal map.
    linkedin_subsections: dict[str, list[dict]] = {
        "languages": list(getattr(cv, "linkedin_languages", []) or []),
        "projects": list(getattr(cv, "linkedin_projects", []) or []),
        "volunteer": list(getattr(cv, "linkedin_volunteer", []) or []),
        "courses": list(getattr(cv, "linkedin_courses", []) or []),
    }
    github_temporal: dict[str, dict] = {
        "languages": dict(getattr(cv, "github_languages", {}) or {}),
        "topics": {t: 1 for t in (getattr(cv, "github_topics", []) or [])},
    }

    # Step-1.5 S3-E — current_version_id surfaces the newest snapshot id
    # from user_profile_versions. Best-effort: a stale DB without 0007
    # migration just returns None.
    current_version_id: int | None = None
    try:
        versions = list_profile_versions(_user_id_for(profile), limit=1)
        if versions:
            current_version_id = versions[0]["id"]
    except Exception:
        current_version_id = None

    return ProfileResponse(
        summary=summary,
        preferences=asdict(profile.preferences),
        cv_detail=cv_detail if cv.raw_text else None,
        skill_tiers=skill_tiers,
        skill_esco=getattr(cv, "cv_skills_esco", {}) or {},
        skill_provenance=skill_provenance,
        linkedin_subsections=linkedin_subsections,
        github_temporal=github_temporal,
        current_version_id=current_version_id,
    )


def _user_id_for(profile: UserProfile) -> str:
    """Pull a user_id off the profile if the caller stamped one; fall back
    to the default tenant. Used only for current_version_id lookup —
    the per-route handlers always pass the authenticated user_id directly."""
    user_id = getattr(profile, "user_id", None)
    if isinstance(user_id, str) and user_id:
        return user_id
    from src.core.tenancy import DEFAULT_TENANT_ID  # noqa: PLC0415

    return DEFAULT_TENANT_ID


@router.get("/profile", response_model=ProfileResponse)
async def get_profile(user: CurrentUser = Depends(require_user)):  # noqa: B008 — FastAPI dependency-injection idiom
    """Return the caller's profile summary.

    Per-user storage landed in Batch 3.5.2 — each user has their own row
    in ``user_profiles`` keyed by ``user.id``. No more silent overwrites
    between authenticated users.
    """
    profile = load_profile(user.id)
    if profile is None:
        raise HTTPException(status_code=404, detail="No profile found")
    return _build_profile_response(profile)


@router.post("/profile", response_model=ProfileResponse)
async def upsert_profile(
    cv: UploadFile = File(None),  # noqa: B008 — FastAPI dependency-injection idiom
    preferences: str = Form(None),  # noqa: B008 — FastAPI dependency-injection idiom
    user: CurrentUser = Depends(require_user),  # noqa: B008 — FastAPI dependency-injection idiom
):
    """Create or update the caller's profile with CV and/or preferences."""
    profile = load_profile(user.id) or UserProfile()

    # Parse CV if provided
    if cv is not None:
        content = await cv.read()
        suffix = os.path.splitext(cv.filename or ".pdf")[1] or ".pdf"
        tmp_path = save_upload_to_temp(content, suffix)
        try:
            try:
                cv_data = await parse_cv_async(tmp_path)
            except RuntimeError as e:
                raise HTTPException(status_code=503, detail=str(e)) from e
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

    save_profile(profile, user.id)
    return _build_profile_response(profile)


@router.post("/profile/linkedin", response_model=LinkedInResponse)
async def upload_linkedin(
    file: UploadFile = File(...),  # noqa: B008 — FastAPI dependency-injection idiom
    user: CurrentUser = Depends(require_user),  # noqa: B008 — FastAPI dependency-injection idiom
):
    """Enrich user profile with a LinkedIn 'Save to PDF' profile export."""
    content = await file.read()
    suffix = os.path.splitext(file.filename or ".pdf")[1].lower() or ".pdf"
    if suffix != ".pdf":
        raise HTTPException(status_code=400, detail="LinkedIn upload must be a PDF (profile → More → Save to PDF).")
    tmp_path = save_upload_to_temp(content, suffix)
    try:
        linkedin_data = parse_linkedin_pdf(tmp_path)
        merged = bool(linkedin_data.get("skills") or linkedin_data.get("positions"))
        if merged:
            profile = load_profile(user.id) or UserProfile()
            profile.cv_data = enrich_cv_from_linkedin(profile.cv_data, linkedin_data)
            save_profile(profile, user.id)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
    return LinkedInResponse(ok=True, merged=merged)


@router.post("/profile/github", response_model=GitHubResponse)
async def upload_github(
    username: str = Form(...),  # noqa: B008 — FastAPI dependency-injection idiom
    user: CurrentUser = Depends(require_user),  # noqa: B008 — FastAPI dependency-injection idiom
):
    """Enrich the caller's profile with GitHub public data."""
    github_data = await fetch_github_profile(username)
    profile = load_profile(user.id) or UserProfile()
    profile.cv_data = enrich_cv_from_github(profile.cv_data, github_data)
    profile.preferences.github_username = username
    save_profile(profile, user.id)
    return GitHubResponse(ok=True, merged=True)


# ── Step-1.5 S3-A,B,C — profile version + JSON Resume endpoints. ──


@router.get("/profile/versions", response_model=ProfileVersionsListResponse)
async def list_versions(
    limit: int = 20,
    user: CurrentUser = Depends(require_user),  # noqa: B008 — FastAPI dependency-injection idiom
):
    """Step-1.5 S3-A — list the most-recent ``user_profile_versions`` rows
    for the caller, newest first. Each save_profile() also writes a
    snapshot here (Pillar 1 Batch 1.8) so the list is non-empty whenever
    the user has at least one profile save.

    Returns 200 with an empty ``versions`` array when the user has no
    profile yet — preferred over 404 because the UI history page can
    render a "no versions yet" state from the empty list.
    """
    rows = list_profile_versions(user.id, limit=limit)
    summaries = [ProfileVersionSummary(**row) for row in rows]
    return ProfileVersionsListResponse(versions=summaries, total=len(summaries))


@router.post(
    "/profile/versions/{version_id}/restore",
    response_model=ProfileResponse,
)
async def restore_version(
    version_id: int,
    user: CurrentUser = Depends(require_user),  # noqa: B008 — FastAPI dependency-injection idiom
):
    """Step-1.5 S3-B — atomic rollback to ``version_id``.

    Tenant-scoped: ``restore_profile_version`` returns ``None`` when the
    version belongs to another user (rule #12 spirit), which surfaces as
    a 404 here — the existence-hiding pattern Batch 3.5.1 introduced for
    similar cross-tenant lookups.
    """
    restored = restore_profile_version(user.id, version_id)
    if restored is None:
        raise HTTPException(status_code=404, detail="Version not found")
    return _build_profile_response(restored)


@router.get("/profile/json-resume", response_model=JsonResumeResponse)
async def get_json_resume(
    user: CurrentUser = Depends(require_user),  # noqa: B008 — FastAPI dependency-injection idiom
):
    """Step-1.5 S3-C — export the caller's CV as a JSON Resume document
    (https://jsonresume.org/schema/). Wraps the existing
    ``CVData.to_json_resume()`` helper Batch 1.8 shipped — additive,
    read-only, no rename of internal fields. 404 when the caller has no
    profile row yet."""
    profile = load_profile(user.id)
    if profile is None:
        raise HTTPException(status_code=404, detail="No profile found")
    return JsonResumeResponse(resume=profile.cv_data.to_json_resume())
