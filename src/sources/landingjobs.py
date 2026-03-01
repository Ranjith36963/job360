import logging
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource
from src.config.keywords import RELEVANCE_KEYWORDS

logger = logging.getLogger("job360.sources.landingjobs")

# Country codes that count as UK/relevant
_UK_CODES = {"GB", "UK"}
_MAX_JOBS = 200


class LandingJobsSource(BaseJobSource):
    name = "landingjobs"

    async def fetch_jobs(self) -> list[Job]:
        jobs = []
        offset = 0
        limit = 50

        while offset < _MAX_JOBS:
            params = {"limit": str(limit), "offset": str(offset)}
            data = await self._get_json(
                "https://landing.jobs/api/v1/jobs.json",
                params=params,
            )
            if not data or not isinstance(data, list) or len(data) == 0:
                break

            for item in data:
                # Filter for UK or remote jobs
                locations = item.get("locations", [])
                is_remote = item.get("remote", False)
                is_uk = any(
                    loc.get("country_code", "").upper() in _UK_CODES
                    for loc in locations
                    if isinstance(loc, dict)
                )
                if not is_uk and not is_remote:
                    continue

                title = item.get("title", "")
                tags = " ".join(item.get("tags", []))
                text = f"{title} {tags}".lower()
                if not any(kw in text for kw in RELEVANCE_KEYWORDS):
                    continue

                # Build location string
                location_parts = []
                for loc in locations:
                    if isinstance(loc, dict):
                        city = loc.get("city", "")
                        country = loc.get("country_code", "")
                        if city:
                            location_parts.append(f"{city}, {country}" if country else city)
                if is_remote:
                    location_parts.append("Remote")
                location = "; ".join(location_parts) if location_parts else ""

                company = str(item.get("company_name", "") or item.get("company_id", ""))
                apply_url = item.get("url", "")
                date_found = item.get("published_at") or datetime.now(timezone.utc).isoformat()

                jobs.append(Job(
                    title=title,
                    company=company,
                    location=location,
                    description=tags,
                    apply_url=apply_url,
                    source=self.name,
                    date_found=date_found,
                ))

            if len(data) < limit:
                break
            offset += limit

        logger.info(f"LandingJobs: found {len(jobs)} relevant UK/remote jobs")
        return jobs
