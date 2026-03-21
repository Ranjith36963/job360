from difflib import SequenceMatcher

from src.models import Job

# Minimum description similarity ratio for pass-2 dedup
_DESC_SIMILARITY_THRESHOLD = 0.85
# Minimum description length to attempt similarity comparison
_MIN_DESC_LEN = 50


def _normalize_title(title: str) -> str:
    """Normalize a job title for dedup grouping.

    Strips trailing job codes and parentheticals but preserves seniority
    prefixes — "Senior Data Engineer" and "Junior Data Engineer" are
    distinct roles and must NOT collapse into the same dedup bucket.
    """
    from src.models import _TRAILING_CODE_RE, _PAREN_RE
    t = title.strip()
    t = _TRAILING_CODE_RE.sub('', t)
    t = _PAREN_RE.sub('', t)
    return t.strip().lower()


def _completeness(job: Job) -> int:
    score = 0
    if job.salary_min is not None:
        score += 10
    if job.salary_max is not None:
        score += 10
    if job.description:
        score += min(len(job.description) // 50, 20)
    if job.location:
        score += 5
    return score


def _description_similar(a: str, b: str) -> bool:
    """Return True if two descriptions are similar enough to be duplicates."""
    if len(a) < _MIN_DESC_LEN or len(b) < _MIN_DESC_LEN:
        return False
    return SequenceMatcher(None, a, b).ratio() >= _DESC_SIMILARITY_THRESHOLD


def deduplicate(jobs: list[Job]) -> list[Job]:
    if not jobs:
        return []

    # Pass 1: Group by normalized (company, title)
    groups: dict[tuple[str, str], list[Job]] = {}
    for job in jobs:
        company, _ = job.normalized_key()
        title = _normalize_title(job.title)
        key = (company, title)
        groups.setdefault(key, []).append(job)
    pass1: list[Job] = []
    for group in groups.values():
        best = max(group, key=lambda j: (j.match_score, _completeness(j)))
        pass1.append(best)

    # Pass 2: Same company + similar description → merge
    company_groups: dict[str, list[Job]] = {}
    for job in pass1:
        company, _ = job.normalized_key()
        company_groups.setdefault(company, []).append(job)

    result: list[Job] = []
    for company_jobs in company_groups.values():
        if len(company_jobs) <= 1:
            result.extend(company_jobs)
            continue
        # Compare descriptions within same company
        kept: list[Job] = []
        for job in company_jobs:
            is_dup = False
            for existing in kept:
                if _description_similar(job.description, existing.description):
                    # Keep the one with higher score / more data
                    if (job.match_score, _completeness(job)) > (
                        existing.match_score, _completeness(existing)
                    ):
                        kept.remove(existing)
                        kept.append(job)
                    is_dup = True
                    break
            if not is_dup:
                kept.append(job)
        result.extend(kept)

    return result
