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

# Weights for scoring components (total = 100)
TITLE_WEIGHT = 40
SKILL_WEIGHT = 40
LOCATION_WEIGHT = 10
RECENCY_WEIGHT = 10

# Points per skill match
PRIMARY_POINTS = 3
SECONDARY_POINTS = 2
TERTIARY_POINTS = 1
SKILL_CAP = SKILL_WEIGHT


def _text_contains(text: str, term: str) -> bool:
    return term.lower() in text


def _title_score(job_title: str) -> int:
    title_lower = job_title.lower()
    for target in JOB_TITLES:
        if target.lower() == title_lower:
            return TITLE_WEIGHT
        if target.lower() in title_lower or title_lower in target.lower():
            return TITLE_WEIGHT // 2
    # Check for partial keyword overlap
    title_words = set(re.findall(r'\w+', title_lower))
    ai_ml_words = {"ai", "ml", "machine", "learning", "deep", "nlp", "data", "scientist", "engineer", "genai", "llm", "rag", "computer", "vision", "mlops"}
    overlap = title_words & ai_ml_words
    if overlap:
        return min(len(overlap) * 5, TITLE_WEIGHT // 2)
    return 0


def _skill_score(text: str) -> int:
    text_lower = text.lower()
    points = 0
    for skill in PRIMARY_SKILLS:
        if _text_contains(text_lower, skill.lower()):
            points += PRIMARY_POINTS
    for skill in SECONDARY_SKILLS:
        if _text_contains(text_lower, skill.lower()):
            points += SECONDARY_POINTS
    for skill in TERTIARY_SKILLS:
        if _text_contains(text_lower, skill.lower()):
            points += TERTIARY_POINTS
    return min(points, SKILL_CAP)


def _location_score(location: str) -> int:
    loc_lower = location.lower()
    for target in LOCATIONS:
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


def score_job(job: Job) -> int:
    text = f"{job.title} {job.description}"
    title_pts = _title_score(job.title)
    skill_pts = _skill_score(text)
    location_pts = _location_score(job.location)
    recency_pts = _recency_score(job.date_found)
    total = title_pts + skill_pts + location_pts + recency_pts
    return min(max(total, 0), 100)


def check_visa_flag(job: Job) -> bool:
    text = f"{job.title} {job.description}".lower()
    return any(kw.lower() in text for kw in VISA_KEYWORDS)
