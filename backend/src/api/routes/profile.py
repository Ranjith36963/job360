"""Profile routes for Job360 FastAPI backend."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict
from typing import Any, cast

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile

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
from src.core.settings import PROFILE_EXTRACT_MAX_PER_HOUR
from src.services.auth import rate_limit as auth_rate_limit
from src.services.profile.cv_parser import extract_text
from src.services.profile.github_enricher import (
    enrich_cv_from_github,
    fetch_github_profile,
    normalize_github_username,
)
from src.services.profile.linkedin_parser import (
    _extract_text as extract_linkedin_text,
)
from src.services.profile.linkedin_parser import (
    _looks_like_linkedin,
)
from src.services.profile.models import UserPreferences, UserProfile
from src.services.profile.storage import (
    list_profile_versions,
    load_profile,
    restore_profile_version,
    save_profile,
)
from src.services.profile.two_pass import reset_cv_owned_fields, run_two_pass_extraction

router = APIRouter(tags=["profile"])

# Logger under the "job360" namespace so setup_logging()'s handlers (stdout +
# file + JSON) actually emit these records. A bare __name__ logger lands on the
# root logger, which has no job360 handler, so its INFO lines vanish.
logger = logging.getLogger("job360.api.profile")

# FIX 2 — keep a strong reference to every background re-score task so the
# GC cannot collect it before it finishes (asyncio.create_task returns a weak
# ref; without this set the task can be garbage-collected mid-run).
_rescore_bg_tasks: set[Any] = set()


async def _maybe_trigger_rescore(user_id: str) -> None:
    """Fire-and-forget: schedule a background re-score if the profile content changed.

    FIX 2 — changed to ``async def`` so it can safely be awaited from async
    route handlers.  Uses ``asyncio.create_task`` (mirror of
    search.py:79 / CLAUDE.md Task-7 spec) so the heavy re-score runs in the
    background without blocking the HTTP response.  Task reference is pinned
    to ``_rescore_bg_tasks`` to prevent GC loss.
    Never lets scheduling errors propagate — the profile save must never 500.
    Lazy imports keep the hot GET/POST paths import-cycle-free (rule #16).
    """
    try:
        from src.services.profile.storage import (  # noqa: PLC0415
            profile_content_changed_since_previous,
        )

        if not profile_content_changed_since_previous(user_id):
            return

        import asyncio  # noqa: PLC0415

        # Extraction now happens INLINE in the upload routes (one combined pass),
        # so the background job only needs to re-SCORE the feed against the
        # already-extracted profile — it must NOT re-extract, or we'd be doing
        # the work twice again (the very redundancy this merge removed).
        from src.services.rescore import rescore_user_feed  # noqa: PLC0415

        task = asyncio.create_task(rescore_user_feed(user_id))
        _rescore_bg_tasks.add(task)
        task.add_done_callback(_rescore_bg_tasks.discard)
        logger.info("rescore: background re-score scheduled for user %s", user_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "rescore: failed to schedule background re-score for user %s: %s",
            user_id,
            exc,
        )


def _build_profile_response(profile: UserProfile) -> ProfileResponse:
    summary = ProfileSummary(
        is_complete=profile.is_complete,
        job_titles=profile.cv_data.job_titles,
        skills_count=len(profile.cv_data.skills),
        cv_length=len(profile.cv_data.raw_text),
        # Any merged LinkedIn signal counts — skills OR positions. Mirrors the
        # upload route's own `merged = skills or positions`; checking only
        # linkedin_skills left has_linkedin=False after a successful upload that
        # yielded positions but no detected skills.
        has_linkedin=bool(profile.cv_data.linkedin_skills or profile.cv_data.linkedin_positions),
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

    # Skills grouped by SOURCE for the source-based profile view. Maps each
    # provenance source label to a user-facing bucket; a skill with multiple
    # sources appears under each of its buckets.
    _SOURCE_BUCKET = {  # noqa: N806 — constant-style lookup table, intentionally uppercase
        "cv_explicit": "cv",
        "linkedin": "linkedin",
        "github_llm": "github",
        "github_lang": "github",
        "github_dep": "github",
        "user_declared": "preferences",
        "about_me_llm": "preferences",
    }
    skills_by_source: dict[str, list[str]] = {
        "cv": [], "linkedin": [], "github": [], "preferences": [],
    }
    _seen_in_bucket: dict[str, set[str]] = {k: set() for k in skills_by_source}
    for skill, sources in skill_provenance.items():
        for src in sources:
            bucket = _SOURCE_BUCKET.get(src)
            if bucket and skill.lower() not in _seen_in_bucket[bucket]:
                skills_by_source[bucket].append(skill)
                _seen_in_bucket[bucket].add(skill.lower())
    # AI suggestions — computed once at extraction, stored on CVData.
    ai_suggestions: list[str] = list(getattr(cv, "suggested_skills", []) or [])

    # Step-1.5 S3-E — LinkedIn sub-sections + GitHub temporal map.
    linkedin_subsections: dict[str, list[dict[str, Any]]] = {
        "languages": list(getattr(cv, "linkedin_languages", []) or []),
        "projects": list(getattr(cv, "linkedin_projects", []) or []),
        "volunteer": list(getattr(cv, "linkedin_volunteer", []) or []),
        "courses": list(getattr(cv, "linkedin_courses", []) or []),
    }
    github_temporal: dict[str, dict[str, Any]] = {
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
        skills_by_source=skills_by_source,
        ai_suggestions=ai_suggestions,
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
async def get_profile(user: CurrentUser = Depends(require_user)) -> ProfileResponse:  # noqa: B008 — FastAPI dependency-injection idiom
    """Return the caller's profile summary.

    Per-user storage landed in Batch 3.5.2 — each user has their own row
    in ``user_profiles`` keyed by ``user.id``. No more silent overwrites
    between authenticated users.
    """
    profile = load_profile(user.id)
    if profile is None:
        raise HTTPException(status_code=404, detail="No profile found")
    return _build_profile_response(profile)


# ── Shared profile-input helpers — the upload pipeline in ONE place ──
# Each of the four inputs has its OWN route (CV / Preferences / LinkedIn /
# GitHub). CV + Preferences ALSO share the combined /profile route for backward
# compatibility with the existing frontend form. Every route funnels through
# _extract_save_trigger so the single-extraction pipeline
# (run_two_pass_extraction -> save one version -> schedule re-score) lives in
# exactly one spot and can never drift between routes.

def _capture_cv_raw(content: bytes, filename: str | None, profile: UserProfile) -> None:
    """Validate the upload and store the RAW CV text on the profile.

    Skill extraction is NOT done here — that happens once, later, in
    run_two_pass_extraction. Raises HTTPException on size (413) / type (415) /
    empty-text (503).
    """
    # V-04 — size cap (10 MB)
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File exceeds the 10 MB limit")
    # V-04 — allowlist by filename extension. The client-supplied content_type
    # is unreliable (browsers send application/octet-stream for PDFs), so the
    # extension is the signal we trust.
    suffix = os.path.splitext(filename or "")[1].lower()
    if suffix not in {".pdf", ".docx"}:
        raise HTTPException(status_code=415, detail="Only PDF or DOCX files are accepted")
    tmp_path = save_upload_to_temp(content, suffix)
    try:
        raw_text = extract_text(tmp_path)
        if not raw_text or not raw_text.strip():
            raise HTTPException(
                status_code=503,
                detail="Could not extract any text from the CV file",
            )
        # A NEW CV replaces the old one — it does not merge with it.
        #
        # This used to set raw_text alone, leaving every extracted field from the
        # PREVIOUS CV in place. The enhance merge then fills empty scalars only
        # and unions the lists, so uploading a different person's CV produced one
        # profile carrying the FIRST person's name/headline/location/summary and
        # BOTH people's skills (measured in prod: 104 skills -> 152, name
        # unchanged). Tailored CVs are generated from this profile, so that put
        # the wrong name on a document headed to an employer.
        #
        # ORDER IS THE SAFETY GUARD: every rejection above (413/415/503) raises
        # BEFORE this line, so an unreadable or oversized upload leaves the
        # existing profile completely untouched. We only discard the old CV once
        # the new one is known to be readable.
        reset_cv_owned_fields(profile.cv_data)
        profile.cv_data.raw_text = raw_text
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _apply_preferences(preferences_json: str, profile: UserProfile) -> None:
    """Parse the preferences JSON form and set it on the profile.

    Fields the form does NOT carry (``github_username`` — set by the separate
    GitHub route — plus ``preferred_workplace`` / ``needs_visa``) fall back to the
    EXISTING preferences so a routine preferences save never silently wipes them.
    """
    pref_dict = json.loads(preferences_json)
    existing = profile.preferences or UserPreferences()
    profile.preferences = UserPreferences(
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
        github_username=pref_dict.get("github_username", existing.github_username),
        preferred_workplace=pref_dict.get("preferred_workplace", existing.preferred_workplace),
        needs_visa=pref_dict.get("needs_visa", existing.needs_visa),
    )


async def _extract_save_trigger(profile: UserProfile, user_id: str) -> None:
    """Shared pipeline tail: ONE extraction, save one version, schedule re-score.

    Used by every profile-input route so the merged single-extraction flow is
    defined once.

    COST CAP (docs/fable/08 "Cost economics — NOT audited"):
    Each call fans out to 4+ paid LLM passes, and ANY profile change re-runs ALL
    of them from stored data. Nothing bounded how often a user could trigger that
    — five routes reach this function, and a user editing their profile in a loop
    was an unbounded spend vector.

    This became real rather than theoretical on 2026-07-19: `openai` had never
    actually been installed in production (the import raised, a broad `except`
    swallowed it, and every parse silently fell back to a free tier). Declaring
    the dependency turned the primary PAID provider on for the first time — so the
    uncapped loop that previously cost nothing now bills.

    Reuses the existing limiter rather than inventing a counter: it already
    supports a shared Redis backend (RATE_LIMIT_REDIS), so the cap holds across
    replicas instead of being multiplied by replica count.
    """
    if PROFILE_EXTRACT_MAX_PER_HOUR > 0 and not auth_rate_limit.check_and_record(
        f"profile_extract:{user_id}",
        max_in_window=PROFILE_EXTRACT_MAX_PER_HOUR,
        window_seconds=3600,
    ):
        logger.warning(
            "profile_extract_rate_limited",
            extra={"event": "profile_extract_rate_limited", "user_id": user_id},
        )
        raise HTTPException(
            status_code=429,
            detail=(
                f"Too many profile updates in the last hour "
                f"(limit {PROFILE_EXTRACT_MAX_PER_HOUR}). Each update re-runs AI "
                "extraction over your CV, LinkedIn and GitHub — please wait a "
                "few minutes and try again."
            ),
        )
    await run_two_pass_extraction(profile)
    save_profile(profile, user_id)
    await _maybe_trigger_rescore(user_id)


@router.post("/profile/cv", response_model=ProfileResponse)
async def upload_cv(
    cv: UploadFile = File(...),  # noqa: B008 — FastAPI dependency-injection idiom
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> ProfileResponse:
    """Set the caller's CV (PDF/DOCX) — one input, one dedicated route."""
    profile = load_profile(user.id) or UserProfile()
    # Bounded read: cap memory even for a malicious oversized upload.
    content = await cv.read(10 * 1024 * 1024 + 1)
    _capture_cv_raw(content, cv.filename, profile)
    await _extract_save_trigger(profile, user.id)
    return _build_profile_response(profile)


@router.post("/profile/preferences", response_model=ProfileResponse)
async def upsert_preferences(
    preferences: str = Form(...),  # noqa: B008 — FastAPI dependency-injection idiom
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> ProfileResponse:
    """Set the caller's preferences form — one input, one dedicated route."""
    profile = load_profile(user.id) or UserProfile()
    _apply_preferences(preferences, profile)
    await _extract_save_trigger(profile, user.id)
    return _build_profile_response(profile)


@router.post("/profile", response_model=ProfileResponse)
async def upsert_profile(
    cv: UploadFile = File(None),  # noqa: B008 — FastAPI dependency-injection idiom
    preferences: str = Form(None),  # noqa: B008 — FastAPI dependency-injection idiom
    user: CurrentUser = Depends(require_user),  # noqa: B008 — FastAPI dependency-injection idiom
) -> ProfileResponse:
    """Combined CV + preferences (backward-compatible with the frontend form).

    The dedicated single-input routes are POST /profile/cv and
    POST /profile/preferences; this endpoint accepts both together.
    """
    profile = load_profile(user.id) or UserProfile()
    if cv is not None:
        content = await cv.read(10 * 1024 * 1024 + 1)
        _capture_cv_raw(content, cv.filename, profile)
    if preferences is not None:
        _apply_preferences(preferences, profile)
    await _extract_save_trigger(profile, user.id)
    return _build_profile_response(profile)


@router.post("/profile/linkedin", response_model=LinkedInResponse)
async def upload_linkedin(
    file: UploadFile = File(...),  # noqa: B008 — FastAPI dependency-injection idiom
    user: CurrentUser = Depends(require_user),  # noqa: B008 — FastAPI dependency-injection idiom
) -> LinkedInResponse:
    """Enrich user profile with a LinkedIn 'Save to PDF' profile export."""
    # Bounded read — see the CV endpoint: caps memory for oversized uploads.
    content = await file.read(10 * 1024 * 1024 + 1)

    # V-04 — size cap (10 MB)
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File exceeds the 10 MB limit")

    # V-04 — MIME / extension allowlist (LinkedIn must be PDF only)
    suffix = os.path.splitext(file.filename or ".pdf")[1].lower() or ".pdf"
    _ctype = (file.content_type or "").lower()
    if suffix != ".pdf" or _ctype not in {"application/pdf", ""}:
        raise HTTPException(status_code=415, detail="Only PDF or DOCX files are accepted")
    tmp_path = save_upload_to_temp(content, suffix)
    try:
        # Capture RAW LinkedIn text only; the single extractor below turns it
        # into skills/positions (deterministic + LLM).
        text = extract_linkedin_text(tmp_path)
        merged = bool(text) and _looks_like_linkedin(text)
        if merged:
            profile = load_profile(user.id) or UserProfile()
            profile.cv_data.linkedin_raw_text = text
            await _extract_save_trigger(profile, user.id)
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
) -> GitHubResponse:
    """Enrich the caller's profile with GitHub public data.

    Accepts a full profile URL or @handle, not just a bare username —
    ``normalize_github_username`` reduces it to the handle before lookup.
    """
    clean_username = normalize_github_username(username)
    github_data = await fetch_github_profile(clean_username)
    profile = load_profile(user.id) or UserProfile()
    # enrich_cv_from_github captures the RAW GitHub signals (repos_brief,
    # languages, deterministic frameworks). The single extractor below then adds
    # the GitHub LLM pass and re-runs the others from stored raw.
    profile.cv_data = enrich_cv_from_github(profile.cv_data, github_data)
    profile.preferences.github_username = clean_username
    await _extract_save_trigger(profile, user.id)
    return GitHubResponse(ok=True, merged=True)


# ── Step-1.5 S3-A,B,C — profile version + JSON Resume endpoints. ──


@router.get("/profile/versions", response_model=ProfileVersionsListResponse)
async def list_versions(
    limit: int = Query(20, ge=1, le=100),
    user: CurrentUser = Depends(require_user),  # noqa: B008 — FastAPI dependency-injection idiom
) -> ProfileVersionsListResponse:
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
) -> ProfileResponse:
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


def _get_profile_version_for_user(version_id: int, user_id: str) -> dict[str, Any] | None:
    """Fetch a single profile version row scoped to ``user_id``.

    Returns None when the version does not exist or belongs to another user
    (existence-hiding pattern per Batch 3.5.1 / rule #12).

    Uses the same DB path as ``storage.save_profile`` by delegating to
    ``list_profile_versions`` with a high limit and filtering by id. This
    keeps both reads and writes on the same SQLite file even when tests
    monkeypatch ``settings.DB_PATH`` after ``storage.py`` has already imported
    its own ``DB_PATH`` reference.
    """
    # list_profile_versions is scoped to user_id and uses the same DB path as
    # save_profile — safe against cross-tenant leaks.
    rows = list_profile_versions(user_id, limit=100)
    for row in rows:
        if row["id"] == version_id:
            return {
                "id": row["id"],
                "created_at": row["created_at"],
                "source_action": row["source_action"],
                "profile_json": row.get("cv_data"),  # already parsed dict; serialise below
                "preferences_json": row.get("preferences"),
            }
    return None


@router.get("/profile/versions/{version_id1}/diff/{version_id2}")
async def diff_profile_versions(
    version_id1: int,
    version_id2: int,
    user: CurrentUser = Depends(require_user),  # noqa: B008 — rule #12
) -> dict[str, Any]:
    """Return per-field differences between two profile versions, scoped to caller.

    Step-3 B-10. Compares cv_data JSON blobs from both versions. Only fields
    that differ are included in ``changes``. Returns 404 when either version is
    missing or belongs to another user (existence-hiding).
    """
    import json  # noqa: PLC0415 — stdlib

    v1 = _get_profile_version_for_user(version_id1, user.id)
    v2 = _get_profile_version_for_user(version_id2, user.id)
    if not v1:
        raise HTTPException(status_code=404, detail=f"Version {version_id1} not found")
    if not v2:
        raise HTTPException(status_code=404, detail=f"Version {version_id2} not found")

    # Diff cv_data (most interesting for users)
    def _parse(raw: object) -> dict[str, Any]:
        if isinstance(raw, str):
            try:
                return cast(dict[str, Any], json.loads(raw))
            except json.JSONDecodeError:
                return {}
        if isinstance(raw, dict):
            return raw
        return {}

    d1 = _parse(v1.get("profile_json"))
    d2 = _parse(v2.get("profile_json"))

    changes: dict[str, dict[str, Any]] = {}
    for k in set(d1.keys()) | set(d2.keys()):
        old_val = d1.get(k)
        new_val = d2.get(k)
        if old_val != new_val:
            changes[k] = {"from": old_val, "to": new_val}

    return {
        "version_id1": version_id1,
        "version_id2": version_id2,
        "changes": changes,
        "changed_fields": list(changes.keys()),
    }


@router.get("/profile/json-resume", response_model=JsonResumeResponse)
async def get_json_resume(
    user: CurrentUser = Depends(require_user),  # noqa: B008 — FastAPI dependency-injection idiom
) -> JsonResumeResponse:
    """Step-1.5 S3-C — export the caller's CV as a JSON Resume document
    (https://jsonresume.org/schema/). Wraps the existing
    ``CVData.to_json_resume()`` helper Batch 1.8 shipped — additive,
    read-only, no rename of internal fields. 404 when the caller has no
    profile row yet."""
    profile = load_profile(user.id)
    if profile is None:
        raise HTTPException(status_code=404, detail="No profile found")
    return JsonResumeResponse(resume=profile.cv_data.to_json_resume())
