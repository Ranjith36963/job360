"""Comeet ATS public postings.

Endpoint pattern: https://www.comeet.co/careers-api/2.0/company/{slug}/positions
Response is a flat JSON array of positions. No documented rate limit — 60s
ATS tier with polite bounded slug list (5 stubs in COMEET_COMPANIES).
"""
import logging
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource, _is_uk_or_remote
from src.core.companies import COMEET_COMPANIES, COMPANY_NAME_OVERRIDES

logger = logging.getLogger("job360.sources.comeet")


class ComeetSource(BaseJobSource):
    name = "comeet"
    category = "ats"

    def __init__(
        self,
        session: aiohttp.ClientSession,
        companies: list[str] | None = None,
        search_config=None,
    ):
        super().__init__(session, search_config=search_config)
        self._companies = companies if companies is not None else COMEET_COMPANIES

    async def fetch_jobs(self) -> list[Job]:
        results: list[Job] = []
        for slug in self._companies:
            url = f"https://www.comeet.co/careers-api/2.0/company/{slug}/positions"
            data = await self._get_json(url, params={"ui_aware": "true"})
            if not data:
                continue
            # Comeet returns a flat array; some wrappers nest it under "positions"
            positions = data if isinstance(data, list) else data.get("positions", [])
            if not isinstance(positions, list):
                continue

            company_name = COMPANY_NAME_OVERRIDES.get(
                slug, slug.replace("-", " ").title()
            )
            for item in positions:
                title = item.get("name") or item.get("position_name") or ""
                location = (
                    item.get("location", {}).get("name")
                    if isinstance(item.get("location"), dict)
                    else (item.get("location") or "")
                )
                if not _is_uk_or_remote(location or ""):
                    continue

                raw_updated = item.get("time_updated") or item.get("timeUpdated")
                raw_posted = item.get("time_created") or raw_updated
                posted_at = raw_posted if raw_posted else None
                confidence = "high" if raw_posted else "low"

                now_iso = datetime.now(timezone.utc).isoformat()
                results.append(Job(
                    title=title,
                    company=company_name,
                    location=location or "Remote",
                    description=(item.get("description") or "")[:5000],
                    apply_url=item.get("url_comeet")
                              or item.get("url")
                              or item.get("apply_url")
                              or "",
                    source=self.name,
                    date_found=now_iso,
                    posted_at=posted_at,
                    date_confidence=confidence,
                    date_posted_raw=raw_posted,
                ))

        logger.info(
            "Comeet: found %s relevant jobs across %s companies",
            len(results),
            len(self._companies),
        )
        return results
