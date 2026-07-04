"""Validate and normalize user preferences form data."""

from __future__ import annotations

import logging
import re

from src.services.profile.models import UserPreferences

logger = logging.getLogger("job360.profile.preferences")


# ── Preferences deterministic pass (Pass 1) — STRUCTURE only, NO LLM ─
#
# Structural label words a user might type in their free-text blurb to introduce
# an explicit list (e.g. "Skills: X, Y" / "Tech stack: A, B"). These are
# DOCUMENT-STRUCTURE markers — the same kind the CV deterministic pass keys off
# ("Skills"/"Summary" headings) — NOT a skill vocabulary. CLAUDE.md rule #28 bans
# hardcoded *skill* lists, not section labels. Free prose with no such marker
# yields nothing here; the LLM pass (llm_infer_from_about_me) handles prose.
_ABOUT_ME_SKILL_MARKERS = (
    "skills", "technical skills", "core skills", "key skills",
    "technologies", "tech stack", "stack", "tools", "tooling",
    "proficient in", "experienced with", "expertise", "specialties",
    "specialities", "competencies", "core competencies",
)
_ABOUT_ME_SPLIT_RE = re.compile(r"[,;|/•·‧]+|\s+and\s+")


def deterministic_about_me_fields(about_me: str) -> list[str]:
    """Pass 1 for preferences over ``about_me`` — STRUCTURE only, NO LLM.

    Pulls items the user explicitly listed after a structural label such as
    ``"Skills:"`` / ``"Technologies:"``. No skill vocabulary and no semantic
    guessing (CLAUDE.md rule #28) — a free-prose blurb with no such marker
    returns ``[]``; the LLM pass (``llm_infer_from_about_me``) mines prose.

    Mirrors the CV/LinkedIn deterministic passes so every input has a real,
    independent deterministic half that feeds the same merge.
    """
    if not about_me or not about_me.strip():
        return []
    out: list[str] = []
    seen: set[str] = set()
    for raw_line in about_me.splitlines():
        line = raw_line.strip()
        if ":" not in line:
            continue
        label, _, rest = line.partition(":")
        if label.strip().lower() not in _ABOUT_ME_SKILL_MARKERS:
            continue
        for tok in _ABOUT_ME_SPLIT_RE.split(rest):
            t = tok.strip().strip(".").strip()
            if t and len(t) <= 40 and t.lower() not in seen:
                out.append(t)
                seen.add(t.lower())
    return out


# ── Preferences LLM pass (Pass 2) — mine free-text about_me ─────────

_ABOUT_ME_SYSTEM = (
    "You read a job-seeker's free-text 'about me' blurb and name the concrete "
    "professional skills it implies. You return JSON only and never invent "
    "skills the text does not support."
)

_ABOUT_ME_PROMPT = """Read this job-seeker's free-text "about me" and extract the concrete
professional SKILLS it implies — tools, methods, domains, specialities.

Return JSON: {{"skills": ["Skill One", "Skill Two", ...]}}

Rules:
- Only skills the text actually supports. Do not invent.
- Individual items, not categories.
- Extract BOTH named tools AND the higher-level capabilities they imply —
  "production GenAI systems" → "Production GenAI"; "cloud-scale deployments" →
  "Cloud Deployment"; "vector databases (ChromaDB, FAISS)" → "Vector Databases",
  "ChromaDB", "FAISS".
- Domain-agnostic: technical OR non-technical (e.g. "Stakeholder Management", "HIPAA Compliance", "Welding").

ABOUT ME:
---
{about_me}
---"""


# NOTE (CLAUDE.md rule #28): the deterministic about_me skill scanner that used
# to live here was removed — it relied on hardcoded skill-term vocabularies.
# Mining the free-text about_me for skills is the LLM's job (llm_infer_from_about_me).


async def llm_infer_from_about_me(about_me: str) -> list[str]:
    """Pass 2 for preferences — mine the free-text ``about_me`` for skills the
    structured form never captured.

    Returns ``[]`` (never raises) on blank input or provider failure. Blank
    input never calls the LLM (cost guard).
    """
    if not about_me or not about_me.strip():
        return []
    prompt = _ABOUT_ME_PROMPT.format(about_me=about_me.strip())
    try:
        from src.services.profile.llm_provider import llm_extract  # noqa: PLC0415
        result = await llm_extract(prompt, system=_ABOUT_ME_SYSTEM)
    except Exception as e:  # noqa: BLE001 — never crash the pass
        logger.warning("about_me LLM skill inference failed: %s", e)
        return []

    raw = result.get("skills") if isinstance(result, dict) else None
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for s in raw:
        if isinstance(s, str) and s.strip() and s.strip().lower() not in seen:
            out.append(s.strip())
            seen.add(s.strip().lower())
    return out


def _split_and_clean(value: str) -> list[str]:
    """Split a comma-separated string and strip whitespace."""
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def validate_preferences(data: dict) -> UserPreferences:
    """Convert a raw form dict into a validated UserPreferences."""
    # Handle both list and comma-separated string inputs
    def to_list(key: str) -> list[str]:
        val = data.get(key, [])
        if isinstance(val, str):
            return _split_and_clean(val)
        if isinstance(val, list):
            return [str(v).strip() for v in val if str(v).strip()]
        return []

    return UserPreferences(
        target_job_titles=to_list("target_job_titles"),
        additional_skills=to_list("additional_skills"),
        excluded_skills=to_list("excluded_skills"),
        preferred_locations=to_list("preferred_locations"),
        industries=to_list("industries"),
        salary_min=data.get("salary_min"),
        salary_max=data.get("salary_max"),
        work_arrangement=data.get("work_arrangement", ""),
        experience_level=data.get("experience_level", ""),
        negative_keywords=to_list("negative_keywords"),
        about_me=data.get("about_me", ""),
    )


def merge_cv_and_preferences(
    cv_skills: list[str],
    cv_titles: list[str],
    prefs: UserPreferences,
) -> UserPreferences:
    """Merge CV-extracted data with user preferences. Preferences take priority."""
    # Combine titles: user prefs first, then CV-extracted
    merged_titles = list(prefs.target_job_titles)
    seen_titles = {t.lower() for t in merged_titles}
    for title in cv_titles:
        if title.lower() not in seen_titles:
            merged_titles.append(title)
            seen_titles.add(title.lower())

    # additional_skills = the user's OWN extras only ("Skills beyond what your CV
    # contains"). It must NOT absorb cv_skills: the tiering scores every
    # additional_skills item as user_declared (3.0), so folding the whole CV in
    # here forced every skill into the Primary tier (Secondary/Tertiary always
    # empty) and stuffed the preferences box with the entire CV. cv_skills already
    # live on cv.skills and are read independently by the tiering
    # (collect_evidence_from_profile) and domain_classifier. Dedup the user's own
    # list, minus excluded. (cv_skills stays in the signature for API stability.)
    excluded = {s.lower() for s in prefs.excluded_skills}
    merged_skills = []
    seen_skills = set()
    for skill in list(prefs.additional_skills):
        key = skill.lower()
        if key not in seen_skills and key not in excluded:
            merged_skills.append(skill)
            seen_skills.add(key)

    return UserPreferences(
        target_job_titles=merged_titles,
        additional_skills=merged_skills,
        excluded_skills=prefs.excluded_skills,
        preferred_locations=prefs.preferred_locations,
        industries=prefs.industries,
        salary_min=prefs.salary_min,
        salary_max=prefs.salary_max,
        work_arrangement=prefs.work_arrangement,
        experience_level=prefs.experience_level,
        negative_keywords=prefs.negative_keywords,
        about_me=prefs.about_me,
        github_username=prefs.github_username,
    )
