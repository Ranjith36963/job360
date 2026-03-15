"""Skill matcher — scores jobs against the active user profile.

The active profile is built by merging up to THREE input layers:
1. **CV** — proven strengths extracted from the user's resume
2. **Preferences** — additional titles, skills, locations the user wants
3. **LinkedIn export** — full professional history (optional)

When none of these exist, falls back to the default keywords in
config/keywords.py.

The scoring engine is domain-agnostic: it derives all matching terms
from the merged profile rather than using hardcoded domain words.
"""

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
from src.cv_parser import load_profile
from src.preferences import load_preferences

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


def _unique_list(items: list) -> list:
    """Deduplicate a list while preserving order (case-insensitive)."""
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        key = item.lower().strip()
        if key and key not in seen:
            seen.add(key)
            result.append(item.strip())
    return result


def _merge_profile_and_preferences(cv_profile: dict | None, prefs: dict | None) -> dict:
    """Merge CV profile + user preferences into a single unified profile.

    Priority: CV skills keep their tier. Preference skills are added as
    secondary (they're things the user CAN do but hasn't proven on CV).
    Preference job titles and locations are appended to CV-extracted ones.
    """
    base = {
        "job_titles": [],
        "primary_skills": [],
        "secondary_skills": [],
        "tertiary_skills": [],
        "locations": [],
    }

    # Layer 1: CV profile (proven strengths)
    if cv_profile:
        base["job_titles"] = list(cv_profile.get("job_titles", []))
        base["primary_skills"] = list(cv_profile.get("primary_skills", []))
        base["secondary_skills"] = list(cv_profile.get("secondary_skills", []))
        base["tertiary_skills"] = list(cv_profile.get("tertiary_skills", []))
        base["locations"] = list(cv_profile.get("locations", []))

    # Layer 2: User preferences (what they WANT and CAN do)
    if prefs:
        # Preference titles go after CV titles
        base["job_titles"].extend(prefs.get("job_titles", []))
        # Preference skills → secondary (user says they can do it, not proven on CV)
        base["secondary_skills"].extend(prefs.get("skills", []))
        # Preference locations
        base["locations"].extend(prefs.get("locations", []))
        # Extract skill-like terms from projects, certifications, about_me
        extra_text = " ".join([
            prefs.get("about_me", ""),
            " ".join(prefs.get("projects", [])),
            " ".join(prefs.get("certifications", [])),
        ])
        if extra_text.strip():
            # Add these as tertiary (background signal, not primary skills)
            from src.cv_parser import _find_skills_in_text
            extra_skills = _find_skills_in_text(extra_text)
            existing = {s.lower() for s in base["primary_skills"] + base["secondary_skills"] + base["tertiary_skills"]}
            for skill in extra_skills:
                if skill.lower() not in existing:
                    base["tertiary_skills"].append(skill)

    # Layer 3: LinkedIn data (stored in preferences under "linkedin" key)
    linkedin = prefs.get("linkedin", {}) if prefs else {}
    if linkedin:
        base["job_titles"].extend(linkedin.get("job_titles", []))
        # LinkedIn skills → secondary
        base["secondary_skills"].extend(linkedin.get("skills", []))
        base["locations"].extend(linkedin.get("locations", []))
        # Certifications/projects → mine for skill terms as tertiary
        extra = " ".join(linkedin.get("certifications", []) + linkedin.get("projects", []))
        if extra.strip():
            from src.cv_parser import _find_skills_in_text
            cert_skills = _find_skills_in_text(extra)
            existing = {s.lower() for s in base["primary_skills"] + base["secondary_skills"] + base["tertiary_skills"]}
            for skill in cert_skills:
                if skill.lower() not in existing:
                    base["tertiary_skills"].append(skill)

    # Deduplicate all lists
    base["job_titles"] = _unique_list(base["job_titles"])
    base["primary_skills"] = _unique_list(base["primary_skills"])
    base["secondary_skills"] = _unique_list(base["secondary_skills"])
    base["tertiary_skills"] = _unique_list(base["tertiary_skills"])
    base["locations"] = _unique_list(base["locations"])

    # Remove secondary/tertiary skills that are already primary (avoid double-counting)
    primary_lower = {s.lower() for s in base["primary_skills"]}
    base["secondary_skills"] = [s for s in base["secondary_skills"] if s.lower() not in primary_lower]
    secondary_lower = primary_lower | {s.lower() for s in base["secondary_skills"]}
    base["tertiary_skills"] = [s for s in base["tertiary_skills"] if s.lower() not in secondary_lower]

    return base


def _load_active_profile() -> dict:
    """Return the active keyword profile (merged from CV + preferences + LinkedIn)."""
    global _cached_profile
    if _cached_profile is not None:
        return _cached_profile

    cv_profile = load_profile()
    prefs = load_preferences()

    if cv_profile or prefs:
        _cached_profile = _merge_profile_and_preferences(cv_profile, prefs)
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
    """Check if term appears as a whole word in text."""
    return bool(_word_boundary_pattern(term).search(text))


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
    # Check remote terms first
    for term in REMOTE_TERMS:
        if term in loc_lower:
            return LOCATION_WEIGHT - 2
    # Apply aliases then check against profile locations
    normalized = loc_lower
    for alias, canonical in LOCATION_ALIASES.items():
        if alias in normalized:
            normalized = normalized.replace(alias, canonical)
    for target in profile.get("locations", []):
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
    penalty = _negative_penalty(job.title)
    foreign_penalty = _foreign_location_penalty(job.location)
    total = title_pts + skill_pts + location_pts + recency_pts - penalty - foreign_penalty
    return min(max(total, 0), 100)


def check_visa_flag(job: Job) -> bool:
    text = f"{job.title} {job.description}".lower()
    return any(kw.lower() in text for kw in VISA_KEYWORDS)


# ---------------------------------------------------------------------------
# Profile-driven query helpers — used by job sources
# ---------------------------------------------------------------------------

def get_search_queries(limit: int = 5) -> list[str]:
    """Return job title search queries derived from the active profile."""
    profile = _load_active_profile()
    return list(profile.get("job_titles", []))[:limit]


def get_search_locations() -> list[str]:
    """Return locations derived from the active profile."""
    profile = _load_active_profile()
    return list(profile.get("locations", []))


def get_relevance_keywords() -> list[str]:
    """Build relevance keywords from the active profile's skills and titles.

    These replace the old hardcoded RELEVANCE_KEYWORDS so that sources
    filter for jobs relevant to THIS user's profile, not just AI/ML.
    """
    profile = _load_active_profile()
    keywords: set[str] = set()
    # Add all skill names (lowercased)
    for key in ("primary_skills", "secondary_skills", "tertiary_skills"):
        for skill in profile.get(key, []):
            keywords.add(skill.lower())
    # Add words from job titles
    for title in profile.get("job_titles", []):
        for word in title.lower().split():
            if len(word) > 2:
                keywords.add(word)
    return sorted(keywords)


def get_search_tags() -> str:
    """Build comma-separated tags for tag-based APIs (e.g. Jobicy).

    Derived from the profile's primary skills and job titles.
    """
    profile = _load_active_profile()
    tags: list[str] = []
    for skill in profile.get("primary_skills", [])[:5]:
        tags.append(skill.lower().replace(" ", "-"))
    for title in profile.get("job_titles", [])[:3]:
        # Extract key words, skip generic words
        for word in title.lower().split():
            if word not in ("engineer", "developer", "senior", "junior", "lead"):
                if len(word) > 2 and word not in tags:
                    tags.append(word)
    return ",".join(tags[:10])
