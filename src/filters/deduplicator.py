from src.models import Job


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
        key = job.normalized_key()
        groups.setdefault(key, []).append(job)
    result = []
    for group in groups.values():
        best = max(group, key=lambda j: (j.match_score, _completeness(j)))
        result.append(best)
    return result
