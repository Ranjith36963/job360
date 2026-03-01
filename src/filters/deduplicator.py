import re

from src.models import Job

# Seniority prefixes to strip for fuzzy title matching
_SENIORITY_RE = re.compile(
    r'^(senior|sr\.?|junior|jr\.?|lead|principal|staff|head\s+of)\s+',
    re.IGNORECASE,
)

# Trailing job codes like "- 12345" or "/ REQ-123"
_TRAILING_CODE_RE = re.compile(r'\s*[-/]\s*[A-Z0-9]{2,}[-_]?\d+\s*$', re.IGNORECASE)

# Parentheticals like "(London)" or "(Remote)"
_PAREN_RE = re.compile(r'\s*\([^)]*\)\s*$')


def _normalize_title(title: str) -> str:
    """Normalize a job title for dedup grouping."""
    t = title.strip()
    t = _TRAILING_CODE_RE.sub('', t)
    t = _PAREN_RE.sub('', t)
    t = _SENIORITY_RE.sub('', t)
    return t.strip().lower()


def _completeness(job: Job) -> int:
    score = 0
    if job.salary_min is not None:
        score += 1
    if job.salary_max is not None:
        score += 1
    if job.description:
        score += len(job.description)
    if job.location:
        score += 1
    return score


def deduplicate(jobs: list[Job]) -> list[Job]:
    if not jobs:
        return []
    groups: dict[tuple[str, str], list[Job]] = {}
    for job in jobs:
        company, _ = job.normalized_key()
        title = _normalize_title(job.title)
        key = (company, title)
        groups.setdefault(key, []).append(job)
    result = []
    for group in groups.values():
        best = max(group, key=lambda j: (j.match_score, _completeness(j)))
        result.append(best)
    return result
