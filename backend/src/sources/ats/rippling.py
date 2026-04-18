"""Rippling ATS public postings.

Endpoint pattern: https://ats.rippling.com/api/board/{slug}/jobs
No documented rate limit — polled on the 60s ATS tier; slugs are bounded
(5 stub entries in RIPPLING_COMPANIES).
"""
import logging
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource, _is_uk_or_remote
from src.core.companies import RIPPLING_COMPANIES, COMPANY_NAME_OVERRIDES

logger = logging.getLogger("job360.sources.rippling")


class RipplingSource(BaseJobSource):
    name = "rippling"
    category = "ats"

    def __init__(
        self,
        session: aiohttp.ClientSession,
        companies: list[str] | None = None,
        search_config=None,
    ):
        super().__init__(session, search_config=search_config)
        self._companies = companies if companies is not None else RIPPLING_COMPANIES

    async def fetch_jobs(self) -> list[Job]:
        results: list[Job] = []
        for slug in self._companies:
            url = f"https://ats.rippling.com/api/board/{slug}/jobs"
            data = await self._get_json(url)
            if not data or "jobs" not in data:
                continue
            company_name = COMPANY_NAME_OVERRIDES.get(
                slug, slug.replace("-", " ").title()
            )
            for item in data["jobs"]:
                title = item.get("name") or item.get("title") or ""
                locations = item.get("locations") or []
                if isinstance(locations, list) and locations:
                    location = " ".join(
                        loc.get("name") if isinstance(loc, dict) else str(loc)
                        for loc in locations
                    )
                else:
                    location = item.get("location") or ""
                if not _is_uk_or_remote(location):
                    continue

                raw_created = item.get("createdAt") or item.get("created_at")
                posted_at = raw_created if raw_created else None
                confidence = "high" if raw_created else "low"

                now_iso = datetime.now(timezone.utc).isoformat()
                results.append(Job(
                    title=title,
                    company=company_name,
                    location=location,
                    description=(item.get("description") or "")[:5000],
                    apply_url=item.get("hostedUrl") or item.get("url") or "",
                    source=self.name,
                    date_found=now_iso,
                    posted_at=posted_at,
                    date_confidence=confidence,
                    date_posted_raw=raw_created,
                ))

        logger.info(
            "Rippling: found %s relevant jobs across %s companies",
            len(results),
            len(self._companies),
        )
        return results
