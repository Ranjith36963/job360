import logging
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource, _is_uk_or_remote
from src.core.companies import ASHBY_COMPANIES, COMPANY_NAME_OVERRIDES

logger = logging.getLogger("job360.sources.ashby")


class AshbySource(BaseJobSource):
    name = "ashby"
    category = "ats"

    def __init__(self, session: aiohttp.ClientSession, companies: list[str] | None = None, search_config=None):
        super().__init__(session, search_config=search_config)
        self._companies = companies if companies is not None else ASHBY_COMPANIES

    async def fetch_jobs(self) -> list[Job]:
        jobs = []
        for slug in self._companies:
            url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
            data = await self._get_json(url)
            if not data or "jobs" not in data:
                continue
            company_name = COMPANY_NAME_OVERRIDES.get(slug, slug.replace("-", " ").title())
            for item in data["jobs"]:
                title = item.get("title", "")
                desc = item.get("descriptionPlain", "")
                location = item.get("location", "")
                if not _is_uk_or_remote(location):
                    continue
                # Ashby publishedAt is the real posting date; updatedAt is a
                # mutation timestamp and must not populate posted_at.
                now_iso = datetime.now(timezone.utc).isoformat()
                raw_published = item.get("publishedAt")
                posted_at = raw_published if raw_published else None
                confidence = "high" if raw_published else "low"
                jobs.append(Job(
                    title=title,
                    company=company_name,
                    location=location,
                    description=desc[:5000],
                    apply_url=item.get("applicationUrl", item.get("jobUrl", "")),
                    source=self.name,
                    date_found=now_iso,
                    posted_at=posted_at,
                    date_confidence=confidence,
                    date_posted_raw=raw_published,
                ))
        logger.info("Ashby: found %s relevant jobs across %s companies", len(jobs), len(self._companies))
        return jobs
