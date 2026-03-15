import logging
from datetime import datetime, timezone, timedelta
import re

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource
from src.config.keywords import JOB_TITLES, RELEVANCE_KEYWORDS

logger = logging.getLogger("job360.sources.google_jobs")

# Use top 8 job titles to stay within 100 req/month free tier (~3 runs/week)
GOOGLE_JOBS_QUERIES = JOB_TITLES[:8]

_DAYS_RE = re.compile(r"(\d+)\s+days?\s+ago", re.IGNORECASE)
_HOURS_RE = re.compile(r"(\d+)\s+hours?\s+ago", re.IGNORECASE)


def _parse_posted_at(text: str) -> str:
    """Convert SerpApi 'X days ago' / 'X hours ago' to ISO date string."""
    if not text:
        return datetime.now(timezone.utc).isoformat()
    lower = text.lower()
    m = _DAYS_RE.search(lower)
    if m:
        days = int(m.group(1))
        return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    m = _HOURS_RE.search(lower)
    if m:
        return datetime.now(timezone.utc).isoformat()
    return datetime.now(timezone.utc).isoformat()


class GoogleJobsSource(BaseJobSource):
    name = "google_jobs"

    def __init__(self, session: aiohttp.ClientSession, api_key: str = ""):
        super().__init__(session)
        self._api_key = api_key

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    async def fetch_jobs(self) -> list[Job]:
        if not self.is_configured:
            logger.info("GoogleJobs: no SERPAPI_KEY, skipping")
            return []

        jobs = []
        seen_keys = set()

        for query in GOOGLE_JOBS_QUERIES:
            params = {
                "engine": "google_jobs",
                "q": query,
                "location": "United Kingdom",
                "api_key": self._api_key,
            }
            data = await self._get_json(
                "https://serpapi.com/search",
                params=params,
            )
            if not data or "jobs_results" not in data:
                continue

            for item in data["jobs_results"]:
                title = item.get("title", "")
                company = item.get("company_name", "")
                description = item.get("description", "")
                text = f"{title} {description}".lower()

                if not any(kw in text for kw in RELEVANCE_KEYWORDS):
                    continue

                # Deduplicate within this source
                dedup_key = (company.lower(), title.lower())
                if dedup_key in seen_keys:
                    continue
                seen_keys.add(dedup_key)

                location = item.get("location", "")

                # Extract apply link
                apply_options = item.get("apply_options", [])
                apply_url = apply_options[0].get("link", "") if apply_options else ""

                # Extract date
                extensions = item.get("detected_extensions", {})
                posted_at = extensions.get("posted_at", "")
                date_found = _parse_posted_at(posted_at)

                # Extract salary if available
                salary_str = extensions.get("salary", "")
                salary_min = None
                salary_max = None
                if salary_str and "–" in salary_str or "-" in salary_str:
                    parts = salary_str.replace(",", "").replace("£", "").replace("$", "").replace("K", "000")
                    parts = re.split(r"[–\-]", parts)
                    try:
                        salary_min = float(re.sub(r"[^\d.]", "", parts[0].strip()))
                        salary_max = float(re.sub(r"[^\d.]", "", parts[1].strip()))
                    except (ValueError, IndexError):
                        pass

                jobs.append(Job(
                    title=title,
                    company=company,
                    location=location,
                    description=description[:5000],
                    apply_url=apply_url,
                    source=self.name,
                    date_found=date_found,
                    salary_min=salary_min,
                    salary_max=salary_max,
                ))

        logger.info(f"GoogleJobs: found {len(jobs)} relevant jobs")
        return jobs
