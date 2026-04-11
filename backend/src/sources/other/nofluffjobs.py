import logging
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource, _is_uk_or_remote

logger = logging.getLogger("job360.sources.nofluffjobs")

_MAX_RESULTS = 200

# NoFluffJobs API endpoints to try (the public API is unofficial and may change)
_API_URLS = [
    "https://nofluffjobs.com/api/posting",
    "https://nofluffjobs.com/api/search/posting",
]


class NoFluffJobsSource(BaseJobSource):
    name = "nofluffjobs"
    category = "other"

    async def fetch_jobs(self) -> list[Job]:
        data = None
        for url in _API_URLS:
            data = await self._get_json(url)
            if data and isinstance(data, (list, dict)):
                break

        if not data:
            logger.info("NoFluffJobs: API unavailable, skipping")
            return []

        # Handle both list and dict responses
        postings = data if isinstance(data, list) else data.get("postings", [])
        if not isinstance(postings, list):
            return []

        jobs = []
        for item in postings:
            title = item.get("title", "")
            name = item.get("name", "")
            # Some responses use "name" instead of "title"
            title = title or name

            category = item.get("category", "")
            technology = " ".join(item.get("technology", []) if isinstance(item.get("technology"), list) else [])
            text = f"{title} {category} {technology}".lower()

            # Location handling
            location_obj = item.get("location", {})
            if isinstance(location_obj, dict):
                places = location_obj.get("places", [])
                location = ", ".join(
                    p.get("city", "") for p in places if isinstance(p, dict)
                ) if places else ""
            elif isinstance(location_obj, str):
                location = location_obj
            else:
                location = ""

            remote = item.get("remote", False)
            if remote:
                location = f"{location}, Remote".strip(", ") if location else "Remote"

            # Skip bare "Remote" or empty location — NoFluffJobs is Polish-focused
            if not location or location.strip().lower() == "remote":
                continue

            if not _is_uk_or_remote(location):
                continue

            # Build apply URL from posting ID
            posting_id = item.get("id", "") or item.get("url", "")
            apply_url = f"https://nofluffjobs.com/job/{posting_id}" if posting_id else ""

            date_found = item.get("posted") or item.get("renewed") or datetime.now(timezone.utc).isoformat()

            # Salary
            salary_obj = item.get("salary", {})
            salary_min = None
            salary_max = None
            if isinstance(salary_obj, dict):
                salary_min = salary_obj.get("from")
                salary_max = salary_obj.get("to")
                if salary_min is not None:
                    try:
                        salary_min = float(salary_min)
                    except (ValueError, TypeError):
                        salary_min = None
                if salary_max is not None:
                    try:
                        salary_max = float(salary_max)
                    except (ValueError, TypeError):
                        salary_max = None

            jobs.append(Job(
                title=title,
                company=item.get("company", ""),
                location=location,
                apply_url=apply_url,
                source=self.name,
                date_found=date_found,
                salary_min=salary_min,
                salary_max=salary_max,
            ))

            if len(jobs) >= _MAX_RESULTS:
                logger.info("NoFluffJobs: hit cap of %s results", _MAX_RESULTS)
                break

        logger.info("NoFluffJobs: found %s relevant jobs", len(jobs))
        return jobs
