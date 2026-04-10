import logging
import os
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource, _is_uk_or_remote

logger = logging.getLogger("job360.sources.eightykhours")

# 80,000 Hours uses Algolia for their job board (jobs.80000hours.org)
# These are public search-only keys; overridable via env vars.
_ALGOLIA_APP_ID = os.getenv("EIGHTYKHOURS_ALGOLIA_APP_ID", "W6KM1UDIB3")
_ALGOLIA_API_KEY = os.getenv("EIGHTYKHOURS_ALGOLIA_API_KEY", "d1d7f2c8696e7b36837d5ed337c4a319")
_ALGOLIA_INDEX = "jobs_prod"
_ALGOLIA_URL = f"https://{_ALGOLIA_APP_ID}-dsn.algolia.net/1/indexes/{_ALGOLIA_INDEX}/query"

class EightyKHoursSource(BaseJobSource):
    """80,000 Hours — high-impact AI safety jobs via Algolia API."""
    name = "eightykhours"
    category = "scraper"

    async def fetch_jobs(self) -> list[Job]:
        jobs = []
        seen_ids = set()
        headers = {
            "X-Algolia-Application-Id": _ALGOLIA_APP_ID,
            "X-Algolia-API-Key": _ALGOLIA_API_KEY,
            "Content-Type": "application/json",
        }

        queries = self.search_queries
        if not queries:
            logger.info("80,000 Hours: no search queries in profile, skipping")
            return []

        for query in queries:
            body = {
                "query": query,
                "hitsPerPage": 50,
            }
            data = await self._post_json(_ALGOLIA_URL, body=body, headers=headers)
            if not data or "hits" not in data:
                continue

            for hit in data["hits"]:
                obj_id = hit.get("objectID", "")
                if obj_id in seen_ids:
                    continue
                seen_ids.add(obj_id)

                title = hit.get("title", "") or hit.get("job_title", "")
                company = hit.get("company_name", "") or hit.get("organisation_name", "") or "Unknown"
                locations = hit.get("locations", [])
                if isinstance(locations, list):
                    location = ", ".join(
                        loc.get("name", "") if isinstance(loc, dict) else str(loc)
                        for loc in locations
                    )
                else:
                    location = str(locations) if locations else ""

                if not _is_uk_or_remote(location):
                    continue

                # Build apply URL
                slug = hit.get("id_external_80_000_hours", "") or hit.get("url_external", "") or obj_id
                if slug.startswith("http"):
                    apply_url = slug
                else:
                    apply_url = f"https://jobs.80000hours.org/jobs/{obj_id}"

                description = hit.get("description_short", "") or hit.get("description", "") or title

                date_found = hit.get("date_published", "") or datetime.now(timezone.utc).isoformat()

                jobs.append(Job(
                    title=title,
                    company=company,
                    location=location or "Remote",
                    description=description[:5000],
                    apply_url=apply_url,
                    source=self.name,
                    date_found=date_found,
                ))

        logger.info("80,000 Hours: found %s relevant jobs", len(jobs))
        return jobs
