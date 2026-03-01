import re
from datetime import datetime, timezone
from functools import lru_cache

from src.models import Job
from src.config.keywords import (
    JOB_TITLES,
    LOCATIONS,
    PRIMARY_SKILLS,
    SECONDARY_SKILLS,
    TERTIARY_SKILLS,
    VISA_KEYWORDS,
    NEGATIVE_TITLE_KEYWORDS,
)
from src.config.settings import TARGET_SALARY_MIN, TARGET_SALARY_MAX

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

# Location aliases — map variants to canonical form
LOCATION_ALIASES = {
    "greater london": "london",
    "city of london": "london",
    "england": "uk",
    "scotland": "uk",
    "wales": "uk",
    "gb": "uk",
    "great britain": "uk",
    "united kingdom": "uk",
}

REMOTE_TERMS = {"remote", "anywhere", "work from home", "wfh"}

# Countries, major non-UK cities, and US state abbreviations that indicate non-UK jobs
FOREIGN_INDICATORS = {
    # Countries
    "united states", "usa", "canada", "australia", "india", "germany", "france",
    "spain", "italy", "netherlands", "sweden", "norway", "denmark", "finland",
    "switzerland", "austria", "belgium", "ireland", "singapore", "japan",
    "china", "brazil", "mexico", "south korea", "israel", "poland", "portugal",
    "czech", "romania", "turkey", "south africa", "new zealand", "philippines",
    # Major non-UK cities
    "new york", "san francisco", "los angeles", "chicago", "seattle", "austin",
    "boston", "denver", "toronto", "vancouver", "montreal", "sydney", "melbourne",
    "berlin", "munich", "paris", "amsterdam", "stockholm", "copenhagen", "oslo",
    "helsinki", "zurich", "dubai", "bangalore", "hyderabad", "mumbai", "pune",
    "tokyo", "tel aviv",
    # US state abbreviations (with comma prefix to avoid false matches)
    ", ca", ", ny", ", tx", ", wa", ", ma", ", co", ", il", ", ga", ", nc", ", va",
    ", fl", ", pa", ", oh", ", nj", ", or", ", az", ", mn", ", ct", ", md",
}

# Terms that confirm UK / remote (checked before foreign indicators)
UK_TERMS = {
    "uk", "united kingdom", "london", "manchester", "birmingham", "bristol",
    "cambridge", "oxford", "edinburgh", "glasgow", "belfast", "leeds",
    "liverpool", "nottingham", "sheffield", "southampton", "reading",
    "hatfield", "hertfordshire", "england", "scotland", "wales",
    "greater london", "city of london", "gb", "great britain",
}

# Experience level patterns
_EXPERIENCE_PATTERNS = {
    "intern": re.compile(r'\b(intern|internship)\b', re.IGNORECASE),
    "junior": re.compile(r'\b(junior|jr|graduate|entry[\s-]?level)\b', re.IGNORECASE),
    "mid": re.compile(r'\b(mid[\s-]?level|intermediate)\b', re.IGNORECASE),
    "senior": re.compile(r'\b(senior|sr)\b', re.IGNORECASE),
    "lead": re.compile(r'\b(lead|team\s*lead)\b', re.IGNORECASE),
    "staff": re.compile(r'\bstaff\b', re.IGNORECASE),
    "principal": re.compile(r'\bprincipal\b', re.IGNORECASE),
    "head": re.compile(r'\b(head\s+of|director|vp)\b', re.IGNORECASE),
}


@lru_cache(maxsize=512)
def _word_boundary_pattern(term: str) -> re.Pattern:
    """Build a compiled word-boundary regex for a skill term."""
    return re.compile(r'\b' + re.escape(term) + r'\b', re.IGNORECASE)


def _text_contains(text: str, term: str) -> bool:
    """Check if term appears as a whole word in text."""
    return bool(_word_boundary_pattern(term).search(text))


def _title_score(job_title: str) -> int:
    title_lower = job_title.lower()
    for target in JOB_TITLES:
        if target.lower() == title_lower:
            return TITLE_WEIGHT
        if target.lower() in title_lower or title_lower in target.lower():
            return TITLE_WEIGHT // 2
    # Check for partial keyword overlap
    title_words = set(re.findall(r'\w+', title_lower))
    ai_ml_words = {
        "ai", "ml", "machine", "learning", "deep", "nlp", "data",
        "scientist", "engineer", "genai", "llm", "rag", "computer",
        "vision", "mlops", "neural", "transformer", "generative",
        "research", "applied", "platform", "infrastructure",
        "conversational", "robotics", "alignment",
    }
    overlap = title_words & ai_ml_words
    if overlap:
        return min(len(overlap) * 5, TITLE_WEIGHT // 2)
    return 0


def _skill_score(text: str) -> int:
    points = 0
    for skill in PRIMARY_SKILLS:
        if _text_contains(text, skill):
            points += PRIMARY_POINTS
    for skill in SECONDARY_SKILLS:
        if _text_contains(text, skill):
            points += SECONDARY_POINTS
    for skill in TERTIARY_SKILLS:
        if _text_contains(text, skill):
            points += TERTIARY_POINTS
    return min(points, SKILL_CAP)


def _location_score(location: str) -> int:
    loc_lower = location.lower()
    # Check remote terms first
    for term in REMOTE_TERMS:
        if term in loc_lower:
            return LOCATION_WEIGHT - 2
    # Apply aliases then check against LOCATIONS
    normalized = loc_lower
    for alias, canonical in LOCATION_ALIASES.items():
        if alias in normalized:
            normalized = normalized.replace(alias, canonical)
    for target in LOCATIONS:
        target_lower = target.lower()
        if target_lower in ("remote", "hybrid"):
            continue
        if target_lower in loc_lower or target_lower in normalized:
            return LOCATION_WEIGHT
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


def _negative_penalty(job_title: str) -> int:
    """Return penalty points if the title matches a negative keyword."""
    title_lower = job_title.lower()
    for kw in NEGATIVE_TITLE_KEYWORDS:
        if kw in title_lower:
            return 30
    return 0


def _foreign_location_penalty(location: str) -> int:
    """Return penalty if the location indicates a non-UK job."""
    if not location:
        return 0  # Unknown location — might be UK, don't penalise
    loc_lower = location.lower()
    # Check UK / remote terms first — if present, no penalty
    for term in UK_TERMS:
        if term in loc_lower:
            return 0
    for term in REMOTE_TERMS:
        if term in loc_lower:
            return 0
    # Check for foreign indicators
    for indicator in FOREIGN_INDICATORS:
        if indicator in loc_lower:
            return 15
    return 0  # Unknown location — don't penalise


def detect_experience_level(title: str) -> str:
    """Parse a job title and return the experience level string."""
    for level, pattern in _EXPERIENCE_PATTERNS.items():
        if pattern.search(title):
            return level
    return ""


def salary_in_range(job: Job) -> bool:
    """Check if a job's salary overlaps with the target range."""
    if job.salary_min is None and job.salary_max is None:
        return False
    job_min = job.salary_min or 0
    job_max = job.salary_max or float("inf")
    return job_max >= TARGET_SALARY_MIN and job_min <= TARGET_SALARY_MAX


def score_job(job: Job) -> int:
    text = f"{job.title} {job.description}"
    title_pts = _title_score(job.title)
    skill_pts = _skill_score(text)
    location_pts = _location_score(job.location)
    recency_pts = _recency_score(job.date_found)
    penalty = _negative_penalty(job.title)
    foreign_penalty = _foreign_location_penalty(job.location)
    total = title_pts + skill_pts + location_pts + recency_pts - penalty - foreign_penalty
    return min(max(total, 0), 100)


def check_visa_flag(job: Job) -> bool:
    text = f"{job.title} {job.description}".lower()
    return any(kw.lower() in text for kw in VISA_KEYWORDS)
