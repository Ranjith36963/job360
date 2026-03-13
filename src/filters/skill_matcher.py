"""Skill matcher — scores jobs against the active user profile.

When a CV profile exists, scoring is personalised to THAT user's skills,
titles, and locations.  Without a CV, falls back to the default AI/ML
keywords in config/keywords.py.

The scoring engine is domain-agnostic: it derives all matching terms
from the active profile rather than using hardcoded domain words.
"""

import re
from datetime import datetime, timezone

from src.models import Job
from src.config.keywords import (
    JOB_TITLES,
    LOCATIONS,
    PRIMARY_SKILLS,
    SECONDARY_SKILLS,
    TERTIARY_SKILLS,
    VISA_KEYWORDS,
)
from src.cv_parser import load_profile

# Weights for scoring components (total = 100)
TITLE_WEIGHT = 20
SKILL_WEIGHT = 45
LOCATION_WEIGHT = 25
RECENCY_WEIGHT = 10

# Points per skill match
PRIMARY_POINTS = 3
SECONDARY_POINTS = 2
TERTIARY_POINTS = 1
SKILL_CAP = SKILL_WEIGHT

_cached_profile: dict | None = None


def _load_active_profile() -> dict:
    """Return the active keyword profile (CV-based or default)."""
    global _cached_profile
    if _cached_profile is not None:
        return _cached_profile

    profile = load_profile()
    if profile:
        _cached_profile = profile
    else:
        _cached_profile = {
            "job_titles": JOB_TITLES,
            "primary_skills": PRIMARY_SKILLS,
            "secondary_skills": SECONDARY_SKILLS,
            "tertiary_skills": TERTIARY_SKILLS,
            "locations": LOCATIONS,
        }
    return _cached_profile


def reload_profile() -> None:
    """Clear cached profile so next scoring call reloads from disk."""
    global _cached_profile
    _cached_profile = None


def _text_contains(text: str, term: str) -> bool:
    return term.lower() in text


def _build_title_keywords(profile: dict) -> set[str]:
    """Derive domain-relevant words from the profile's job titles and skills.

    Instead of a hardcoded set like {'ai', 'ml', ...}, we extract meaningful
    words from whatever titles and top skills the profile contains.
    """
    words: set[str] = set()
    # Words from job titles
    for title in profile.get("job_titles", []):
        for w in re.findall(r'\w+', title.lower()):
            if len(w) > 1:  # skip single chars
                words.add(w)
    # Top-skill single words (for partial title matching)
    for skill in profile.get("primary_skills", [])[:10]:
        for w in re.findall(r'\w+', skill.lower()):
            if len(w) > 2:  # skip very short
                words.add(w)
    # Remove noise words
    words -= {"and", "the", "for", "with", "from", "our", "you", "are", "has"}
    return words


def _title_score(job_title: str, profile: dict | None = None) -> int:
    if profile is None:
        profile = _load_active_profile()

    title_lower = job_title.lower()

    # Exact or substring match against profile titles
    for target in profile.get("job_titles", []):
        if target.lower() == title_lower:
            return TITLE_WEIGHT
        if target.lower() in title_lower or title_lower in target.lower():
            return TITLE_WEIGHT // 2

    # Partial keyword overlap — derived from the profile, not hardcoded
    title_words = set(re.findall(r'\w+', title_lower))
    domain_words = _build_title_keywords(profile)
    overlap = title_words & domain_words
    if overlap:
        return min(len(overlap) * 5, TITLE_WEIGHT // 2)
    return 0


def _skill_score(text: str, profile: dict | None = None) -> int:
    if profile is None:
        profile = _load_active_profile()

    text_lower = text.lower()
    points = 0
    for skill in profile.get("primary_skills", []):
        if _text_contains(text_lower, skill.lower()):
            points += PRIMARY_POINTS
    for skill in profile.get("secondary_skills", []):
        if _text_contains(text_lower, skill.lower()):
            points += SECONDARY_POINTS
    for skill in profile.get("tertiary_skills", []):
        if _text_contains(text_lower, skill.lower()):
            points += TERTIARY_POINTS
    return min(points, SKILL_CAP)


def _location_score(location: str, profile: dict | None = None) -> int:
    if profile is None:
        profile = _load_active_profile()

    loc_lower = location.lower()
    for target in profile.get("locations", []):
        if target.lower() in loc_lower:
            return LOCATION_WEIGHT
    if "remote" in loc_lower:
        return LOCATION_WEIGHT - 2
    return 0


def _recency_score(date_found: str) -> int:
    """Score based on job posting age. Recent jobs score higher."""
    if not date_found:
        return 0
    try:
        posted = datetime.fromisoformat(date_found)
        if posted.tzinfo is None:
            posted = posted.replace(tzinfo=timezone.utc)
        days_old = (datetime.now(timezone.utc) - posted).days
    except (ValueError, TypeError):
        return 0
    if days_old <= 1:
        return RECENCY_WEIGHT
    if days_old <= 3:
        return 8
    if days_old <= 5:
        return 6
    if days_old <= 7:
        return 4
    return 0


def score_job(job: Job, profile: dict | None = None) -> int:
    """Score a job against a profile.

    Parameters
    ----------
    job : Job
        The job to score.
    profile : dict, optional
        An explicit profile to score against.  When ``None`` (the default),
        the globally cached profile (CV-based or default) is used.
    """
    if profile is None:
        profile = _load_active_profile()

    text = f"{job.title} {job.description}"
    title_pts = _title_score(job.title, profile)
    skill_pts = _skill_score(text, profile)
    location_pts = _location_score(job.location, profile)
    recency_pts = _recency_score(job.date_found)
    total = title_pts + skill_pts + location_pts + recency_pts
    return min(max(total, 0), 100)


def check_visa_flag(job: Job) -> bool:
    text = f"{job.title} {job.description}".lower()
    return any(kw.lower() in text for kw in VISA_KEYWORDS)
