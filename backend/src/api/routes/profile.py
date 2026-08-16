"""Profile routes for Job360 FastAPI backend."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import asdict
from datetime import datetime, timezone
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
from src.services.profile.models import CVData, UserPreferences, UserProfile
from src.services.profile.preferences import sanitize_preferences
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


def _rescore_finished(task: Any) -> None:
    """Discard the task reference AND say something when it failed.

    The previous callback was ``_rescore_bg_tasks.discard`` — it dropped the
    reference and never touched ``task.exception()``. asyncio only reports an
    unretrieved exception when the task object is garbage-collected, and by then
    the message is detached from the user it belonged to. The surrounding
    ``except`` covers SCHEDULING, not execution, so a re-score that started and
    then died left the feed permanently stale with nothing in the logs.

    Measured in production 2026-08-11: 9,708 user_feed rows carry a
    profile_version older than their user's current one, and all 9,708 point at
    jobs still in the catalog (0 orphans) — so every one of them was reachable
    and simply never got re-scored. One user sits at profile_version 10 while
    their current version is 15: five profile changes, no completed re-score,
    no error anywhere.

    This does not FIX that (see the note in _maybe_trigger_rescore); it makes the
    failure visible, which is the difference between a bug you can find and one
    you cannot.
    """
    _rescore_bg_tasks.discard(task)
    if task.cancelled():
        logger.warning("rescore: background re-score was CANCELLED — feed left stale")
        return
    exc = task.exception()
    if exc is not None:
        logger.error(
            "rescore: background re-score FAILED, feed left on a stale "
            "profile_version: %r",
            exc,
            exc_info=exc,
        )


async def _maybe_trigger_rescore(user_id: str) -> None:
    """Fire-and-forget: schedule a background re-score if the profile content changed.

    FIX 2 — changed to ``async def`` so it can safely be awaited from async
    route handlers.  Uses ``asyncio.create_task`` (mirror of
    search.py:79 / CLAUDE.md Task-7 spec) so the heavy re-score runs in the
    background without blocking the HTTP response.  Task reference is pinned
    to ``_rescore_bg_tasks`` to prevent GC loss.
    Never lets scheduling errors propagate — the profile save must never 500.
    Lazy imports keep the hot GET/POST paths import-cycle-free (rule #16).

    KNOWN LIMITATION — this is fire-and-forget IN THE WEB PROCESS, and that is
    why issue #271 exists. There is no queue entry, no retry and no ledger:
    ``grep -c rescore src/workers/tasks.py`` is 0, so nothing outside this
    process ever knows a re-score was owed. Any restart kills an in-flight run,
    and `main` auto-deploys on every merge, so deploys alone can drop them.
    The durable fix is an ARQ job (the worker and Redis already exist) with a
    retry and a completion record — a change to how production schedules work,
    so it is deliberately NOT bundled with the logging fix below.
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
        task.add_done_callback(_rescore_finished)
        logger.info("rescore: background re-score scheduled for user %s", user_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "rescore: failed to schedule background re-score for user %s: %s",
            user_id,
            exc,
        )


def _build_profile_response(profile: UserProfile, user_id: str) -> ProfileResponse:
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
        # Upload receipts (2026-08-08) — getattr so a CVData loaded from a row
        # saved before these fields existed still renders.
        cv_filename=getattr(profile.cv_data, "cv_filename", "") or "",
        cv_uploaded_at=getattr(profile.cv_data, "cv_uploaded_at", "") or "",
        linkedin_filename=getattr(profile.cv_data, "linkedin_filename", "") or "",
        linkedin_uploaded_at=getattr(profile.cv_data, "linkedin_uploaded_at", "") or "",
        github_username=profile.preferences.github_username or "",
        github_connected_at=getattr(profile.cv_data, "github_connected_at", "") or "",
        github_repo_count=len(getattr(profile.cv_data, "github_repos_brief", []) or []),
    )
    cv = profile.cv_data
    cv_detail = CVDetail(
        extraction_score=getattr(cv, "extraction_score", {}) or {},
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
        cv_positions=getattr(cv, "cv_positions", []) or [],
        cv_projects=getattr(cv, "cv_projects", []) or [],
        cv_experience_level=getattr(cv, "cv_experience_level", "") or "",
        cv_right_to_work=getattr(cv, "cv_right_to_work", "") or "",
        highlights=cv.highlights if hasattr(cv, "highlights") else cv.skills,
    )

    # Step-1.5 S1.5-F — evidence-based tiering. Walk the profile, build
    # per-skill evidence rows, then split into primary/secondary/tertiary
    # by accumulated weight. Empty dict if the helper raises (e.g. brand
    # new profile with no fields populated).
    skill_tiers: dict[str, list[str]] = {}
    skill_provenance: dict[str, list[str]] = {}
    try:
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
        # LinkedIn work history — parsed and stored since Batch 1.5 but never
        # exposed here, so it drove only the has_linkedin boolean and the user
        # could never SEE their LinkedIn experience. Same "stored but not shown"
        # gap as cv_positions, closed 2026-08-08.
        "positions": list(getattr(cv, "linkedin_positions", []) or []),
        "languages": list(getattr(cv, "linkedin_languages", []) or []),
        "projects": list(getattr(cv, "linkedin_projects", []) or []),
        "volunteer": list(getattr(cv, "linkedin_volunteer", []) or []),
        "courses": list(getattr(cv, "linkedin_courses", []) or []),
        # 2026-08-09 — seven sections the parser split and nobody read, plus
        # the Contact block. Same "stored but not shown" gap that hid 92
        # GitHub signals, closed on the other input.
        "honors": list(getattr(cv, "linkedin_honors", []) or []),
        "publications": list(getattr(cv, "linkedin_publications", []) or []),
        "patents": list(getattr(cv, "linkedin_patents", []) or []),
        "organizations": list(getattr(cv, "linkedin_organizations", []) or []),
        "test_scores": list(getattr(cv, "linkedin_test_scores", []) or []),
        "recommendations": list(getattr(cv, "linkedin_recommendations", []) or []),
        "interests": [
            {"name": i} for i in (getattr(cv, "linkedin_interests", []) or [])
        ],
        "contact": [getattr(cv, "linkedin_contact", {}) or {}]
        if getattr(cv, "linkedin_contact", None)
        else [],
        # The About section. It reaches the judge and the vector, and would
        # otherwise be the one thing on this profile that is extracted, stored
        # and MATCHED ON while being invisible to the person it describes — the
        # third way a shelf goes dark, after a broken extractor and a dropping
        # merge. Shown as a single row so it renders like every other section.
        "summary": [{"text": (getattr(cv, "linkedin_summary", "") or "").strip()}]
        if (getattr(cv, "linkedin_summary", "") or "").strip()
        else [],
        "headline": [{"text": (getattr(cv, "linkedin_headline", "") or "").strip()}]
        if (getattr(cv, "linkedin_headline", "") or "").strip()
        else [],
    }
    github_temporal: dict[str, dict[str, Any]] = {
        "languages": dict(getattr(cv, "github_languages", {}) or {}),
        "topics": {t: 1 for t in (getattr(cv, "github_topics", []) or [])},
    }
    # EVERYTHING ELSE GitHub gave us. Measured on a live profile 2026-08-09:
    # languages (14) and topics (10) reached the screen, while 49 frameworks,
    # 13 repos and 30 LLM-read skills sat in the database appearing NOWHERE —
    # 92 pieces of signal stored and shown to nobody, the same shape as
    # cv_positions and the upload receipts before them.
    #
    # This is the input where it matters most: GitHub evidence outranks a CV
    # claim. A CV says "FastAPI"; a requirements.txt in shipped code PROVES it,
    # which is why skill_tiering weights github_dep/github_llm (1.5) above
    # github_lang (1.0). That evidence was already scoring the user's matches —
    # they simply could not see it.
    github_detail: dict[str, Any] = {
        "username": profile.preferences.github_username or "",
        "connected_at": getattr(cv, "github_connected_at", "") or "",
        "repos": list(getattr(cv, "github_repos_brief", []) or []),
        "frameworks": list(getattr(cv, "github_frameworks", []) or []),
        "skills_inferred": list(getattr(cv, "github_skills_inferred", []) or []),
        "llm_skills": list(getattr(cv, "github_llm_skills", []) or []),
        "identity": dict(getattr(cv, "github_identity", {}) or {}),
        "bio": getattr(cv, "github_bio", "") or "",
        "profile_readme": getattr(cv, "github_profile_readme", "") or "",
    }

    # Step-1.5 S3-E — current_version_id surfaces the newest snapshot id
    # from user_profile_versions. Best-effort: a stale DB without 0007
    # migration just returns None.
    current_version_id: int | None = None
    try:
        versions = list_profile_versions(user_id, limit=1)
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
        github_detail=github_detail,
        current_version_id=current_version_id,
    )


# ``_user_id_for(profile)`` used to live here. It did
# ``getattr(profile, "user_id", None)`` against a ``UserProfile``, which has no
# such field and never had one, and nothing in the codebase ever stamped it — so
# it returned None every time and fell through to DEFAULT_TENANT_ID. Every
# user's ``current_version_id`` was therefore looked up against the default
# tenant instead of themselves: a cross-tenant read that could not fail loudly,
# because getattr-with-a-default cannot. All six callers already had the
# authenticated ``user.id`` in scope, so the fix was to pass it.


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
    return _build_profile_response(profile, user.id)


# ── Shared profile-input helpers — the upload pipeline in ONE place ──
# Each of the four inputs has its OWN route (CV / Preferences / LinkedIn /
# GitHub). CV + Preferences ALSO share the combined /profile route for backward
# compatibility with the existing frontend form. Every route funnels through
# _extract_save_trigger so the single-extraction pipeline
# (run_two_pass_extraction -> save one version -> schedule re-score) lives in
# exactly one spot and can never drift between routes.

async def _capture_cv_raw(content: bytes, filename: str | None, profile: UserProfile) -> None:
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
        # OFF THE EVENT LOOP. pdfplumber is synchronous and CPU-bound, so calling
        # it directly here froze the whole server for the duration of the parse.
        # MEASURED in a browser run: while one CV uploaded, /api/health went from
        # 27 ms to 1.4 s, then 1.6 s, then a 5 s timeout, then an outright
        # connection failure. The user's own profile poll was reset by the
        # server, and the page showed "Something went wrong - API error 500"
        # while their upload was in fact succeeding. One person uploading a CV
        # degraded the API for everyone on that worker.
        #
        # Same fix, same reason as the search freeze (PR #123): the work is
        # unchanged, it simply runs in a worker thread so the loop keeps serving.
        raw_text = await asyncio.to_thread(extract_text, tmp_path)
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
        # Upload receipt — WHAT was uploaded and WHEN. Set here, right where the
        # new CV replaces the old one, so a re-upload always names the CURRENT
        # file (a stale name would be worse than none: it would confirm the
        # wrong document). Only the base name is kept — a browser can send a
        # path-ish value, and the directory part is neither useful nor ours.
        profile.cv_data.cv_filename = os.path.basename(filename or "")[:255]
        profile.cv_data.cv_uploaded_at = datetime.now(timezone.utc).isoformat()
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _normalize_work_arrangement(value: object) -> str:
    """Store "I don't mind" as SILENCE, not as the literal string "any".

    The preferences select seeds itself at ``"any"`` and posts that value, so
    "any" reached the database as though the user had stated a constraint. Two
    consumers then read it RAW, not through the ``preferred_workplace`` property
    that maps it to None:

      * the LLM judge builds its prompt with ``if v:`` — a non-empty string — so
        every judged job was told ``work_arrangement=any``, spending real tokens
        to state a preference the user explicitly declined to make;
      * the semantic vector appended "any" as a candidate token.

    The property protects the keyword scorer, but a property cannot protect a
    reader that bypasses it. Normalising on SAVE fixes it once, at the only
    point where the sentinel can enter — and it holds for the CLI and any older
    client too, which a frontend-side fix would not.

    Rule #29: an unstated preference is silence. "Any" IS unstated.
    """
    text = value.strip().lower() if isinstance(value, str) else ""
    return text if text in {"remote", "hybrid", "onsite"} else ""


# The closed set this route will accept as a STATED experience level — the
# exact five options the preferences form's Select offers (PreferencesForm.tsx:
# entry/mid/senior/lead/executive). Anything outside it is not a real answer a
# user could have picked through this form.
#
# scoring_dimensions._USER_EXPERIENCE_RANK recognises a much wider set
# ("junior", "graduate", "sr", "head", "vp", ...) — that map exists to score
# whatever string ends up in this field, including values from older data or
# the CV-inferred fallback, not to define what THIS route should accept as a
# freshly typed preference. Widening this set to match would let garbage like
# "graduate" (not offered anywhere in the current UI) reach the DB looking
# like a deliberate choice, which is the same failure this function exists to
# stop.
_VALID_EXPERIENCE_LEVELS = {"entry", "mid", "senior", "lead", "executive"}


def _normalize_experience_level(value: object) -> str:
    """Store an unrecognised experience level as SILENCE, not as a stated preference.

    Mirrors ``_normalize_work_arrangement`` immediately above — same shape,
    same seam. ``resolve_experience_level`` (scoring_dimensions.py) says
    "typed always wins": a value in this field skips the CV-inferred fallback
    entirely and drives ``seniority_score`` at full weight (up to 8 points,
    either direction, on EVERY job) — the identical amplifier "any" had for
    ``work_arrangement``. Before this function existed, ``_apply_preferences``
    stored whatever the client posted with no check at all, so a typo, a stale
    client sending a value the UI no longer offers, or a future free-text
    field would reach the judge prompt and the semantic vector as though the
    user had stated it.

    NOT a fix for "a brand-new account saves 'mid' it never chose" — that
    defect is the FRONTEND seeding a real, still-selectable dropdown value
    (`prefsFromRaw`'s missing-key fallback) as if it were a default, fixed in
    PreferencesForm.tsx/PreferencesForm.experience.test.tsx. "mid" stays in
    ``_VALID_EXPERIENCE_LEVELS`` and round-trips normally here: it is a real
    choice a user can make, so silencing it on the backend would drop that
    choice for everyone who actually picks "Mid" — trading one data-loss bug
    for a worse one.

    Rule #29: an unstated preference is silence. An unrecognised string IS
    unstated — the user cannot have meant something the form never offered.
    """
    text = value.strip().lower() if isinstance(value, str) else ""
    return text if text in _VALID_EXPERIENCE_LEVELS else ""


def _apply_preferences(preferences_json: str, profile: UserProfile) -> None:
    """Parse the preferences JSON form and set it on the profile.

    Fields the form does NOT carry (``github_username`` — set by the separate
    GitHub route — plus ``needs_visa``, ``work_arrangement`` and
    ``experience_level``) fall back to the EXISTING preferences so a routine
    preferences save never silently wipes them.
    """
    pref_dict = json.loads(preferences_json)
    existing = profile.preferences or UserPreferences()

    # The work_arrangement -> preferred_workplace bridge used to live here.
    #
    # It existed because the form wrote one field and the scorer read another,
    # so the workplace dimension was dead for every user until it was built.
    # A bridge keeps two boxes holding one answer, and they drift: cli.py's
    # setup-profile writes work_arrangement and has never written the other, so
    # that entry point produced a divergent profile from the day it was written.
    # preferred_workplace is now a derived @property on UserPreferences, so
    # there is exactly one stored answer and the two cannot disagree.
    profile.preferences = UserPreferences(
        target_job_titles=pref_dict.get("target_job_titles", []),
        additional_skills=pref_dict.get("additional_skills", []),
        excluded_skills=pref_dict.get("excluded_skills", []),
        preferred_locations=pref_dict.get("preferred_locations", []),
        industries=pref_dict.get("industries", []),
        salary_min=pref_dict.get("salary_min"),
        salary_max=pref_dict.get("salary_max"),
        # An OMITTED key keeps the stored value; an explicit "" still clears it.
        # The old default of "" meant any partial save wiped the answer, and
        # the damage was hidden by the preferred_workplace fallback that this
        # change removes — so without this line the workplace dimension would
        # go dark again, which is the exact regression the bridge was added for.
        work_arrangement=_normalize_work_arrangement(
            pref_dict.get("work_arrangement", existing.work_arrangement)
        ),
        # Same partial-save shape as work_arrangement just above: an OMITTED
        # key keeps the stored value, an explicit "" clears it. The old
        # default of "" made no such distinction, so saving any OTHER field
        # on the form (salary, locations, about_me...) silently wiped a
        # previously-chosen experience level on every routine save.
        experience_level=_normalize_experience_level(
            pref_dict.get("experience_level", existing.experience_level)
        ),
        negative_keywords=pref_dict.get("negative_keywords", []),
        about_me=pref_dict.get("about_me", ""),
        # normalize_github_username here too, not only in the dedicated GitHub
        # route: a raw value reaching this fallback path stored "https:" for a
        # real user (2026-08-08), which silently broke GitHub enrichment. A
        # value that doesn't reduce to a valid handle normalizes to "" — better
        # an empty handle than a poisoned one.
        github_username=normalize_github_username(
            pref_dict.get("github_username") or existing.github_username or ""
        ),
        needs_visa=pref_dict.get("needs_visa", existing.needs_visa),
    )
    # Scrub extraction pollution before it is stored. The frontend autosaves the
    # loaded preference chips straight back, so a profile whose additional_skills
    # / target_job_titles were polluted by an older extraction merge would keep
    # re-saving that junk on every touch. Running the sanitizer HERE means every
    # save self-heals — including a stale open tab autosaving the old chips.
    # Zero-loss by construction: it only drops CV-duplicate content and
    # structural non-skills (dates, sentences, verb-led clauses), never a real
    # skill. See sanitize_preferences for the rules.
    profile.preferences = sanitize_preferences(profile.preferences, profile.cv_data)


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
    await _capture_cv_raw(content, cv.filename, profile)
    await _extract_save_trigger(profile, user.id)
    return _build_profile_response(profile, user.id)


@router.post("/profile/preferences", response_model=ProfileResponse)
async def upsert_preferences(
    preferences: str = Form(...),  # noqa: B008 — FastAPI dependency-injection idiom
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> ProfileResponse:
    """Set the caller's preferences form — one input, one dedicated route."""
    profile = load_profile(user.id) or UserProfile()
    _apply_preferences(preferences, profile)
    await _extract_save_trigger(profile, user.id)
    return _build_profile_response(profile, user.id)


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
        await _capture_cv_raw(content, cv.filename, profile)
    if preferences is not None:
        _apply_preferences(preferences, profile)
    await _extract_save_trigger(profile, user.id)
    return _build_profile_response(profile, user.id)


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
        # Off the event loop for the same reason as the CV path above:
        # pdfplumber is synchronous, and blocking here froze the API for
        # every other request while one person enriched their profile.
        text = await asyncio.to_thread(extract_linkedin_text, tmp_path)
        merged = bool(text) and _looks_like_linkedin(text)
        if merged:
            profile = load_profile(user.id) or UserProfile()
            profile.cv_data.linkedin_raw_text = text
            # Upload receipt — see the CV path. Recorded only on a MERGED
            # upload: a PDF we could not read as LinkedIn changes nothing, so
            # claiming a successful upload would be a lie on screen.
            profile.cv_data.linkedin_filename = os.path.basename(file.filename or "")[:255]
            profile.cv_data.linkedin_uploaded_at = datetime.now(timezone.utc).isoformat()
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

    FAILS LOUDLY (2026-08-08). This route used to return ``ok=True, merged=True``
    unconditionally, so a handle that reduced to nothing still produced a green
    "GitHub profile enriched" toast — and, once receipts shipped, a "GitHub
    connected" row on the profile page — while zero repos were read. Measured on
    a live profile: ``github_connected_at`` stamped, ``github_username`` empty,
    0 repos. A receipt that confirms something that did not happen is worse than
    no receipt at all.

    A handle that does not reduce to a valid GitHub username is a 400 with
    wording the person can act on. ``normalize_github_username`` accepts a bare
    handle, an @handle, a profile URL, and a ``<user>.github.io`` Pages URL
    (whose subdomain IS the username — a CV lists that under "Portfolio", so it
    is exactly what people paste here).
    """
    clean_username = normalize_github_username(username)
    if not clean_username:
        # Name the mistake when we can recognise it. A LinkedIn URL in the
        # GitHub box is the single most likely wrong paste — both boxes live in
        # the same "Enrich Your Profile" card, and a CV lists both links side by
        # side. Telling that user "that does not look like a GitHub username" is
        # true and useless: it does not say where their LinkedIn DOES go, and
        # LinkedIn cannot be read from a link at all (measured: linkedin.com
        # answers a server with HTTP 999 and a sign-in wall, no profile data),
        # which is exactly why the PDF export exists.
        if re.search(r"linkedin\.com", username or "", re.IGNORECASE):
            raise HTTPException(
                status_code=400,
                detail=(
                    "That is a LinkedIn URL, and this box is for GitHub. "
                    "LinkedIn cannot be read from a link — use \"Enrich "
                    "LinkedIn\" above and upload the PDF (on LinkedIn: your "
                    "profile -> More -> Save to PDF)."
                ),
            )
        raise HTTPException(
            status_code=400,
            detail=(
                "That does not look like a GitHub username. Enter your handle "
                "(e.g. octocat), your profile URL (github.com/octocat), or your "
                "GitHub Pages site (octocat.github.io)."
            ),
        )
    github_data = await fetch_github_profile(clean_username)
    profile = load_profile(user.id) or UserProfile()
    # enrich_cv_from_github captures the RAW GitHub signals (repos_brief,
    # languages, deterministic frameworks). The single extractor below then adds
    # the GitHub LLM pass and re-runs the others from stored raw.
    profile.cv_data = enrich_cv_from_github(profile.cv_data, github_data)
    profile.preferences.github_username = clean_username
    # Did the lookup actually YIELD anything? A handle that is well-formed but
    # does not exist (or a rate-limited/unreachable GitHub) returns an empty
    # payload, and enrich_cv_from_github then merges nothing.
    merged = bool(
        getattr(profile.cv_data, "github_repos_brief", None)
        or getattr(profile.cv_data, "github_languages", None)
        or getattr(profile.cv_data, "github_bio", "")
    )
    if merged:
        # Connection receipt — GitHub has no file, so the handle + when we read
        # it is the receipt (repo count comes from len(github_repos_brief)).
        # Stamped ONLY on a lookup that produced data: the receipt must never
        # confirm a connection that did not happen.
        profile.cv_data.github_connected_at = datetime.now(timezone.utc).isoformat()
    else:
        # Nothing came back — do not leave a handle behind claiming otherwise.
        profile.preferences.github_username = ""
        raise HTTPException(
            status_code=404,
            detail=(
                f"GitHub has no public data for '{clean_username}'. Check the "
                "spelling, and note that private repositories are not visible."
            ),
        )
    await _extract_save_trigger(profile, user.id)
    return GitHubResponse(ok=True, merged=merged)


# ── Clearing an input ────────────────────────────────────────────────────────
#
# WHY THIS EXISTS. You cannot trust a test you cannot reset. Every contamination
# bug found on this profile (another person's GitHub, then their LinkedIn, then
# their education/certs/titles merged into the CV fields) was hard to see
# precisely because stale data lingered and every upload MERGED into it. Without
# a reset, "upload a CV and check what came out" is never a clean experiment —
# you are always reading the sum of every previous attempt.
#
# Each scope clears the fields that input OWNS, and nothing else, so clearing
# LinkedIn cannot take the CV with it. Two details matter:
#   * the matching ``llm_input_hashes`` entry is dropped, or a later re-upload of
#     the SAME file would be treated as "already read" and skipped; and
#   * every clear goes through ``save_profile``, which snapshots first — so a
#     clear is undoable from the History drawer, which is what makes an
#     irreversible-sounding button safe.

_CLEAR_SCOPES = ("cv", "linkedin", "github", "preferences", "all")


def _clear_cv(cv: CVData) -> None:
    """Everything the CV owns, including its receipt and quality verdict."""
    reset_cv_owned_fields(cv)  # the canonical "what the CV owns" list
    cv.raw_text = ""
    cv.cv_filename = ""
    cv.cv_uploaded_at = ""
    cv.extraction_score = {}
    cv.llm_input_hashes.pop("cv", None)


def _clear_prefixed(cv: CVData, prefix: str) -> None:
    """Reset every CVData field owned by one input, found BY PREFIX.

    WHY NOT A HAND-WRITTEN LIST. This was 18 named assignments, and it had
    already fallen two fields behind the dataclass: `linkedin_summary` and
    `linkedin_headline` were added, wired into the LLM judge and the semantic
    vector, and never added here. Measured on the shipped code — after
    "Clear LinkedIn", `linkedin_skills` and `linkedin_positions` were empty
    while `linkedin_summary` still held the previous person's About paragraph
    and `linkedin_headline` their tagline, both of which `profile_to_matcher_text`
    then sent to the judge for every job.

    Same failure as `reset_cv_owned_fields`, same root cause, third occurrence
    across the file. Ownership is a PREFIX fact, so read it off the dataclass
    and the list cannot fall behind again.

    Each field goes back to what a fresh `CVData()` has, so the type is handled
    for free — a `dict` shelf added tomorrow resets to `{}` without anyone
    remembering it exists.
    """
    import copy
    import dataclasses

    pristine = CVData()
    for f in dataclasses.fields(CVData):
        if not f.name.startswith(prefix):
            continue
        default = getattr(pristine, f.name)
        current = getattr(cv, f.name)
        # In place for collections — same reason as reset_cv_owned_fields: a
        # rebind orphans any reference taken before the call.
        if isinstance(current, (list, dict)) and isinstance(default, (list, dict)):
            current.clear()
        else:
            setattr(cv, f.name, copy.deepcopy(default))


def _clear_linkedin(cv: CVData) -> None:
    _clear_prefixed(cv, "linkedin_")
    cv.llm_input_hashes.pop("linkedin", None)


def _clear_github(cv: CVData, prefs: UserPreferences) -> None:
    _clear_prefixed(cv, "github_")
    cv.llm_input_hashes.pop("github", None)
    # Lives on UserPreferences, not CVData, so the prefix sweep cannot reach it.
    prefs.github_username = ""  # the handle belongs to this section


@router.post("/profile/clear", response_model=ProfileResponse)
async def clear_profile_section(
    section: str = Form(...),  # noqa: B008 — FastAPI dependency-injection idiom
    user: CurrentUser = Depends(require_user),  # noqa: B008
) -> ProfileResponse:
    """Empty ONE input (or the whole profile), so the next upload starts clean.

    ``section``: cv | linkedin | github | preferences | all.

    Deliberately does NOT re-run extraction: there is nothing to extract, and a
    paid LLM round-trip to rebuild an empty profile would be waste. The stored
    snapshot taken by ``save_profile`` is what makes this reversible.
    """
    if section not in _CLEAR_SCOPES:
        raise HTTPException(
            status_code=400,
            detail=f"section must be one of: {', '.join(_CLEAR_SCOPES)}",
        )
    profile = load_profile(user.id)
    if profile is None:
        raise HTTPException(status_code=404, detail="No profile to clear")

    cv = profile.cv_data
    prefs = profile.preferences or UserPreferences()

    if section in ("cv", "all"):
        _clear_cv(cv)
    if section in ("linkedin", "all"):
        _clear_linkedin(cv)
    if section in ("github", "all"):
        _clear_github(cv, prefs)
    if section in ("preferences", "all"):
        # A fresh preferences object — but the GitHub handle is owned by the
        # GitHub section, so clearing PREFERENCES alone must not disconnect it.
        keep_handle = "" if section == "all" else prefs.github_username
        prefs = UserPreferences(github_username=keep_handle)
    if section == "all":
        # about_me-derived skills live on the CV object but are owned by the
        # preferences the user typed, so a full clear takes them too.
        cv.about_me_inferred_skills = []
        cv.llm_input_hashes.pop("about_me", None)

    profile.preferences = prefs
    save_profile(profile, user.id, f"clear_{section}")
    logger.info(
        "profile_cleared", extra={"event": "profile_cleared", "section": section}
    )
    return _build_profile_response(profile, user.id)


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
    return _build_profile_response(restored, user.id)


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
