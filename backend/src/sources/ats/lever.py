import logging
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource, _is_uk_or_remote
from src.core.companies import LEVER_COMPANIES, COMPANY_NAME_OVERRIDES

logger = logging.getLogger("job360.sources.lever")


class LeverSource(BaseJobSource):
    name = "lever"
    category = "ats"

    def __init__(self, session: aiohttp.ClientSession, companies: list[str] | None = None, search_config=None):
        super().__init__(session, search_config=search_config)
        self._companies = companies if companies is not None else LEVER_COMPANIES

    async def fetch_jobs(self) -> list[Job]:
        jobs = []
        for slug in self._companies:
            url = f"https://api.lever.co/v0/postings/{slug}"
            params = {"mode": "json"}
            data = await self._get_json(url, params=params)
            if not data or not isinstance(data, list):
                continue
            company_name = COMPANY_NAME_OVERRIDES.get(slug, slug.replace("-", " ").title())
            for item in data:
                title = item.get("text", "")
                desc = item.get("descriptionPlain", item.get("description", ""))
                categories = item.get("categories", {})
                location = categories.get("location", "") if isinstance(categories, dict) else ""
                if not _is_uk_or_remote(location):
                    continue
                # Lever createdAt is milliseconds since epoch — real posting date.
                now_iso = datetime.now(timezone.utc).isoformat()
                created_at = item.get("createdAt")
                if created_at and isinstance(created_at, (int, float)):
                    posted_at = datetime.fromtimestamp(created_at / 1000, tz=timezone.utc).isoformat()
                    confidence = "high"
                else:
                    posted_at = None
                    confidence = "low"
                jobs.append(Job(
                    title=title,
                    company=company_name,
                    location=location,
                    description=desc[:5000],
                    apply_url=item.get("hostedUrl", ""),
                    source=self.name,
                    date_found=now_iso,
                    posted_at=posted_at,
                    date_confidence=confidence,
                    date_posted_raw=str(created_at) if created_at else None,
                ))
        logger.info("Lever: found %s relevant jobs across %s companies", len(jobs), len(self._companies))
        return jobs
