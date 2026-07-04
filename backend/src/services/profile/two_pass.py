"""Two-pass profile extraction orchestrator.

Goal: every profile input (CV, LinkedIn, GitHub, preferences) goes through a
deterministic pass (plain code) AND an LLM enhance pass, both merged into one
``CVData``. When the user later changes ANY input, both passes re-run from the
STORED raw inputs (``cv.raw_text``, ``cv.linkedin_raw_text``,
``cv.github_repos_brief``, ``preferences.about_me``) — no re-upload, no network
re-fetch — producing a refreshed CVData and a new profile-version id.

Each pass gracefully no-ops when its input is missing or the LLM provider chain
is unavailable, so this is safe to call with partial profiles and offline.

Heavy/LLM imports stay module-local-friendly; the four enhance helpers are
imported at module top so tests can monkeypatch them by name.
"""

from __future__ import annotations

import logging
import re

from src.services.profile.cv_parser import (
    deterministic_cv_fields,
    llm_cv_fields_from_text,
)
from src.services.profile.github_enricher import (
    deterministic_github_fields,
    llm_infer_github_skills,
)
from src.services.profile.linkedin_parser import (
    deterministic_linkedin_fields,
    enrich_cv_from_linkedin,
    llm_linkedin_fields,
    merge_linkedin_fields,
)
from src.services.profile.models import CVData, UserProfile
from src.services.profile.preferences import (
    deterministic_about_me_fields,
    llm_infer_from_about_me,
    merge_cv_and_preferences,
)

logger = logging.getLogger("job360.profile.two_pass")


def _merge_str_list(dst: list[str], src: list[str]) -> None:
    """Append items from ``src`` not already in ``dst`` (case-insensitive),
    preserving ``dst`` order then ``src`` order. Mutates ``dst`` in place."""
    seen = {s.lower() for s in dst}
    for s in src or []:
        if isinstance(s, str) and s.strip() and s.lower() not in seen:
            dst.append(s)
            seen.add(s.lower())


def _norm_for_dedup(s: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace — so 'The Complete
    Python Bootcamp' and 'the complete python bootcamp!' compare equal."""
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def dedup_by_containment(items: list[str]) -> list[str]:
    """Collapse fragments + exact duplicates in a free-text list (certifications,
    education) while preserving order.

    An entry is dropped when its normalized form equals (a later exact dup) or is
    a proper substring of another entry's normalized form — i.e. it's a line-wrap
    fragment of a more complete entry ('The Complete Python Bootcamp' inside 'The
    Complete Python Bootcamp from Zero to Hero in Python (Udemy, 2024)'). Distinct
    entries are always kept. Structural (containment), never a keyword list.
    """
    cleaned = [it for it in items if it and it.strip()]
    norms = [_norm_for_dedup(it) for it in cleaned]
    drop: set[int] = set()
    for i, na in enumerate(norms):
        if not na:
            drop.add(i)
            continue
        for j, nb in enumerate(norms):
            if i == j or j in drop:
                continue
            if na == nb:
                if i > j:          # exact dup — keep the earlier
                    drop.add(i)
                    break
            elif na in nb:         # a fragment of a more complete entry
                drop.add(i)
                break
    return [it for k, it in enumerate(cleaned) if k not in drop]


def _merge_cv_llm_into(cv: CVData, llm_cv: CVData) -> None:
    """Merge the CV-OWNED fields of an LLM result into the live ``cv``.

    Unions the list fields and fills empty scalars. Deliberately does NOT touch
    ``linkedin_*`` / ``github_*`` / ``about_me_inferred_skills`` — those belong
    to the other passes and must survive a CV re-parse.
    """
    _merge_str_list(cv.skills, llm_cv.skills)
    _merge_str_list(cv.job_titles, llm_cv.job_titles)
    _merge_str_list(cv.companies, llm_cv.companies)
    _merge_str_list(cv.education, llm_cv.education)
    _merge_str_list(cv.certifications, llm_cv.certifications)
    _merge_str_list(cv.achievements, llm_cv.achievements)
    _merge_str_list(cv.industries, llm_cv.industries)
    _merge_str_list(cv.cv_languages, llm_cv.cv_languages)
    # Fill empty scalars only — never overwrite a value the user already has.
    if not cv.name and llm_cv.name:
        cv.name = llm_cv.name
    if not cv.headline and llm_cv.headline:
        cv.headline = llm_cv.headline
    if not cv.location and llm_cv.location:
        cv.location = llm_cv.location
    if not cv.summary and llm_cv.summary:
        cv.summary = llm_cv.summary
    if not cv.experience_text and llm_cv.experience_text:
        cv.experience_text = llm_cv.experience_text


async def run_two_pass_extraction(profile: UserProfile) -> UserProfile:
    """Run deterministic + LLM enhance passes over all four inputs, in place.

    Re-runs entirely from data already stored on the profile — never re-reads a
    file or re-hits the GitHub API. Returns the same ``profile`` object for
    convenience. Never raises: a failing LLM pass is logged and skipped.
    """
    cv = profile.cv_data
    prefs = profile.preferences

    # Every input below follows the SAME shape (the user's diagram):
    #     raw ──┬─ deterministic_X(raw) ─▶ det-output ──┐
    #           └─ await llm_X(raw) ──────▶ llm-output ──┴─▶ merge into the
    #                                                        ONE shared CVData
    # The two passes are INDEPENDENT — both read the same raw input, neither
    # feeds the other — so whatever one pass misses the other can still catch.
    # Deterministic = STRUCTURE only (CLAUDE.md rule #28); LLM = meaning.

    # ── ① CV ── raw = cv.raw_text ──────────────────────────────────────
    if cv.raw_text:
        det_cv = deterministic_cv_fields(cv.raw_text)           # det-CV-output
        _merge_str_list(cv.skills, det_cv.get("skills", []))
        if not cv.summary and det_cv.get("summary"):
            cv.summary = det_cv["summary"]
        try:
            llm_cv = await llm_cv_fields_from_text(cv.raw_text)  # llm-CV-output
            _merge_cv_llm_into(cv, llm_cv)                       # → MERGED CV
        except Exception as e:  # noqa: BLE001 — deterministic result still stands
            logger.warning("two_pass: CV LLM pass skipped: %s", e)

    # ── ② LinkedIn ── raw = cv.linkedin_raw_text ───────────────────────
    if cv.linkedin_raw_text:
        det_li = deterministic_linkedin_fields(cv.linkedin_raw_text)  # det-LI-output
        llm_li: dict = {}
        try:
            llm_li = await llm_linkedin_fields(cv.linkedin_raw_text)  # llm-LI-output
        except Exception as e:  # noqa: BLE001 — deterministic result still stands
            logger.warning("two_pass: LinkedIn LLM pass skipped: %s", e)
        merged_li = merge_linkedin_fields(det_li, llm_li)            # → MERGED LI
        enrich_cv_from_linkedin(cv, merged_li)

    # ── ③ GitHub ── raw = cv.github_repos_brief ────────────────────────
    if cv.github_repos_brief:
        det_gh = deterministic_github_fields(cv.github_repos_brief)  # det-GH-output
        _merge_str_list(cv.github_skills_inferred, det_gh)
        try:
            llm_gh = await llm_infer_github_skills(cv.github_repos_brief)  # llm-GH-output
            _merge_str_list(cv.github_llm_skills, llm_gh)                  # → MERGED GH
        except Exception as e:  # noqa: BLE001 — deterministic result still stands
            logger.warning("two_pass: GitHub LLM pass skipped: %s", e)

    # ── ④ Preferences ── raw = prefs.about_me ──────────────────────────
    if prefs.about_me:
        det_pr = deterministic_about_me_fields(prefs.about_me)   # det-PR-output
        _merge_str_list(cv.about_me_inferred_skills, det_pr)
        try:
            llm_pr = await llm_infer_from_about_me(prefs.about_me)  # llm-PR-output
            _merge_str_list(cv.about_me_inferred_skills, llm_pr)    # → MERGED PR
        except Exception as e:  # noqa: BLE001 — deterministic result still stands
            logger.warning("two_pass: about_me LLM pass skipped: %s", e)

    # Collapse line-wrap fragments + cross-source duplicates in the free-text
    # lists so the profile shows each certification / qualification ONCE (a CV +
    # LinkedIn both list the same cert in slightly different words, and PDF wraps
    # split one cert across two lines — both inflated the counts and read as junk).
    cv.certifications = dedup_by_containment(cv.certifications)
    cv.education = dedup_by_containment(cv.education)

    # ── Fold the freshly-extracted CV skills/titles into preferences ──
    # (Was done in the CV upload route; lives here now so the SINGLE extractor
    # owns the whole job and the route doesn't have to extract anything.)
    if cv.skills or cv.job_titles:
        profile.preferences = merge_cv_and_preferences(
            cv.skills, cv.job_titles, prefs
        )

    return profile
