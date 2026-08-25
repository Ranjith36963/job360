import logging
from datetime import datetime, timezone
from typing import Any, Optional

from src.core.settings import USER_AGENT
from src.models import Job
from src.sources.base import BaseJobSource, _is_uk_or_remote
from src.utils.dates import normalize_posted_at

logger = logging.getLogger("job360.sources.remoteok")


def _clean_salary(raw: Any) -> Optional[float]:
    """RemoteOK uses `0` as the empty sentinel for salary_min/salary_max, not
    null — verified against a live response: 99 of 100 jobs had salary_min ==
    salary_max == 0. Passed straight through, that gave 99% of jobs a literal
    $0 salary. Treat 0 (and any other falsy value) the same as missing data.
    """
    if raw in (None, "", 0, "0"):
        return None
    try:
        num = float(raw)
    except (TypeError, ValueError):
        return None
    return num if num else None


class RemoteOKSource(BaseJobSource):
    name = "remoteok"
    category = "free_json"

    async def fetch_jobs(self) -> list[Job]:
        jobs = []
        headers = {"User-Agent": USER_AGENT}
        data = await self._get_json("https://remoteok.com/api", headers=headers)
        if not data or not isinstance(data, list):
            return []
        # Skip first element (metadata/legal notice)
        for item in data[1:]:
            if not isinstance(item, dict):
                continue
            now_iso = datetime.now(timezone.utc).isoformat()
            raw_date = item.get("date")
            posted_at, confidence = normalize_posted_at(raw_date)

            # The API's real `location` field is filled on 99/100 live jobs
            # with real values (e.g. "Leeds, ", "New York, ") — the old
            # hardcoded "Remote" threw that signal away entirely. Strip
            # trailing comma/whitespace noise and fall back to "Remote" only
            # when the field is genuinely empty.
            raw_location = str(item.get("location") or "").strip().rstrip(", ").strip()
            location = raw_location if raw_location else "Remote"

            # `tags` (94% fill, 94/100 sampled) is RemoteOK's own skill tag
            # list -- the job's own vocabulary, no guessing. `job_type` was
            # checked live and is 0% filled (never present) -- not mapped.
            jobs.append(Job(
                title=item.get("position", ""),
                company=item.get("company", ""),
                location=location,
                salary_min=_clean_salary(item.get("salary_min")),
                salary_max=_clean_salary(item.get("salary_max")),
                description=item.get("description", ""),
                apply_url=item.get("url", ""),
                source=self.name,
                date_found=now_iso,
                posted_at=posted_at,
                date_confidence=confidence,
                date_posted_raw=raw_date,
                source_tags=item.get("tags") or [],
            ))
        jobs = [j for j in jobs if _is_uk_or_remote(j.location)]
        logger.info("RemoteOK: found %s relevant jobs", len(jobs))
        return jobs
