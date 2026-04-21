import re
from datetime import datetime, timezone
from functools import lru_cache

from src.models import Job
from src.core.keywords import (
    JOB_TITLES,
    LOCATIONS,
    PRIMARY_SKILLS,
    SECONDARY_SKILLS,
    TERTIARY_SKILLS,
    VISA_KEYWORDS,
    NEGATIVE_TITLE_KEYWORDS,
)
from src.core.settings import (
    TARGET_SALARY_MIN,
    TARGET_SALARY_MAX,
    MIN_TITLE_GATE,
    MIN_SKILL_GATE,
)
from src.core.skill_synonyms import aliases_for

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
    # Canadian provinces that overlap with UK city names
    "ontario", "quebec",
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


_VISA_NEGATIONS = (
    "no sponsorship", "not sponsor", "cannot sponsor",
    "unable to sponsor", "don't sponsor", "do not sponsor",
    "without sponsorship",
    "company-sponsored", "employer-sponsored",
)


def _has_visa_keyword(text: str, keywords: list) -> bool:
    """Check for visa keywords while respecting negation phrases."""
    text_lower = text.lower()
    if any(neg in text_lower for neg in _VISA_NEGATIONS):
        return False
    return any(kw.lower() in text_lower for kw in keywords)


@lru_cache(maxsize=512)
def _word_boundary_pattern(term: str) -> re.Pattern:
    """Build a compiled word-boundary regex for a skill term."""
    return re.compile(r'\b' + re.escape(term) + r'\b', re.IGNORECASE)


def _text_contains(text: str, term: str) -> bool:
    """Check if term appears as a whole word in text."""
    return bool(_word_boundary_pattern(term).search(text))


def _text_contains_skill(text: str, skill: str) -> bool:
    """Pillar 2 Batch 2.3 — skill-aware text search.

    Expands the search to the skill's canonical form plus every known alias
    (see src.core.skill_synonyms). Still uses word-boundary matching so
    "ai" in "sustain" does not false-match. Unknown skills pass through
    unchanged (aliases_for returns just the lower-cased canonical), so
    this path is a superset of the legacy `_text_contains` behaviour.
    """
    for alias in aliases_for(skill):
        if _word_boundary_pattern(alias).search(text):
            return True
    return False


def _title_score(job_title: str) -> int:
    """Score a job title against the default JOB_TITLES list.

    Module-level fallback for when no user profile exists. Returns 0 for any
    title not in JOB_TITLES — no hardcoded domain biases. Users MUST provide
    a profile (CV or preferences) to get meaningful title scoring.

    Production path uses JobScorer(config)._title_score() which is dynamic.
    """
    title_lower = job_title.lower()
    for target in JOB_TITLES:
        if target.lower() == title_lower:
            return TITLE_WEIGHT
        if target.lower() in title_lower or title_lower in target.lower():
            return TITLE_WEIGHT // 2
    return 0


def _skill_score(text: str) -> int:
    points = 0
    for skill in PRIMARY_SKILLS:
        if _text_contains_skill(text, skill):
            points += PRIMARY_POINTS
    for skill in SECONDARY_SKILLS:
        if _text_contains_skill(text, skill):
            points += SECONDARY_POINTS
    for skill in TERTIARY_SKILLS:
        if _text_contains_skill(text, skill):
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
    """Score based on job posting age. Recent jobs score higher.

    Legacy helper — accepts a single date string. For the 5-column date model,
    prefer `recency_score_for_job(job)` which honours `posted_at` +
    `date_confidence` so fabricated dates no longer inflate freshness.
    """
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


_TRUSTWORTHY_CONFIDENCE = frozenset({"high", "medium", "repost_backdated"})


def recency_score_for_job(job: Job) -> int:
    """Pillar 3 Batch 1 recency scorer driven by the 5-column date model.

    Precedence (per pillar_3_batch_1.md §1):
      1. 'fabricated' confidence → 0 (never inflates)
      2. posted_at + trustworthy confidence → full recency band
      3. posted_at + low confidence → falls back to date_found capped at 60%
      4. no posted_at + date_found → 60% of band (discovery ≠ posting)
      5. neither → 0 points, no penalty
    """
    if job.date_confidence == "fabricated":
        return 0
    if job.posted_at and job.date_confidence in _TRUSTWORTHY_CONFIDENCE:
        return _recency_score(job.posted_at)
    if job.date_found:
        raw = _recency_score(job.date_found)
        return int(raw * 0.6)
    return 0


def _negative_penalty(job_title: str) -> int:
    """Return penalty points if the title matches a negative keyword."""
    for kw in NEGATIVE_TITLE_KEYWORDS:
        if _text_contains(job_title, kw.strip()):
            return 30
    return 0


def _foreign_location_penalty(location: str) -> int:
    """Return penalty if the location indicates a non-UK job."""
    if not location:
        return 0  # Unknown location — might be UK, don't penalise
    loc_lower = location.lower()
    # Check foreign indicators FIRST — catches "London, Ontario" etc.
    for indicator in FOREIGN_INDICATORS:
        if indicator in loc_lower:
            return 15
    # Then check UK / remote terms — if present, no penalty
    for term in UK_TERMS:
        if term in loc_lower:
            return 0
    for term in REMOTE_TERMS:
        if term in loc_lower:
            return 0
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


def _gate_suppressed_score(title_pts: int, skill_pts: int) -> int | None:
    """Pillar 2 Batch 2.2 gate: if either title or skill component fails the
    gate (fraction-of-max ≥ MIN_TITLE_GATE / MIN_SKILL_GATE), return the
    suppressed score `max(10, (title+skill)*0.25)`. Otherwise return None to
    signal the caller to compute the full linear score.
    """
    title_threshold = MIN_TITLE_GATE * TITLE_WEIGHT
    skill_threshold = MIN_SKILL_GATE * SKILL_WEIGHT
    if title_pts < title_threshold or skill_pts < skill_threshold:
        suppressed_linear = (title_pts + skill_pts) * 0.25
        return max(10, int(suppressed_linear))
    return None


def score_job(job: Job) -> int:
    text = f"{job.title} {job.description}"
    title_pts = _title_score(job.title)
    skill_pts = _skill_score(text)
    suppressed = _gate_suppressed_score(title_pts, skill_pts)
    if suppressed is not None:
        return suppressed
    location_pts = _location_score(job.location)
    recency_pts = recency_score_for_job(job)
    penalty = _negative_penalty(job.title)
    foreign_penalty = _foreign_location_penalty(job.location)
    total = title_pts + skill_pts + location_pts + recency_pts - penalty - foreign_penalty
    return min(max(total, 0), 100)


def check_visa_flag(job: Job) -> bool:
    text = f"{job.title} {job.description}"
    return _has_visa_keyword(text, VISA_KEYWORDS)


# ---------------------------------------------------------------------------
# Dynamic scorer — uses SearchConfig instead of hard-coded keywords
# ---------------------------------------------------------------------------


class JobScorer:
    """Score jobs using dynamic keyword sets from a SearchConfig."""

    def __init__(self, config):
        """Accept a SearchConfig (from src.services.profile.models)."""
        self._config = config

    def _title_score(self, job_title: str) -> int:
        title_lower = job_title.lower()
        for target in self._config.job_titles:
            if target.lower() == title_lower:
                return TITLE_WEIGHT
            if target.lower() in title_lower or title_lower in target.lower():
                return TITLE_WEIGHT // 2
        # Partial keyword overlap using dynamic domain words
        title_words = set(re.findall(r'\w+', title_lower))
        core_overlap = title_words & self._config.core_domain_words
        if not core_overlap:
            return 0
        support_overlap = title_words & self._config.supporting_role_words
        return min(len(core_overlap) * 5 + len(support_overlap) * 3, TITLE_WEIGHT // 2)

    def _skill_score(self, text: str) -> int:
        points = 0
        for skill in self._config.primary_skills:
            if _text_contains_skill(text, skill):
                points += PRIMARY_POINTS
        for skill in self._config.secondary_skills:
            if _text_contains_skill(text, skill):
                points += SECONDARY_POINTS
        for skill in self._config.tertiary_skills:
            if _text_contains_skill(text, skill):
                points += TERTIARY_POINTS
        return min(points, SKILL_CAP)

    def _negative_penalty(self, job_title: str) -> int:
        for kw in self._config.negative_title_keywords:
            if _text_contains(job_title, kw.strip()):
                return 30
        return 0

    def score(self, job: Job) -> int:
        text = f"{job.title} {job.description}"
        title_pts = self._title_score(job.title)
        skill_pts = self._skill_score(text)
        suppressed = _gate_suppressed_score(title_pts, skill_pts)
        if suppressed is not None:
            return suppressed
        location_pts = _location_score(job.location)
        recency_pts = recency_score_for_job(job)
        penalty = self._negative_penalty(job.title)
        foreign_penalty = _foreign_location_penalty(job.location)
        total = title_pts + skill_pts + location_pts + recency_pts - penalty - foreign_penalty
        return min(max(total, 0), 100)

    def check_visa_flag(self, job: Job) -> bool:
        text = f"{job.title} {job.description}"
        return _has_visa_keyword(text, self._config.visa_keywords)
