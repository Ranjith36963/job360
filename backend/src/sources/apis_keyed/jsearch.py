import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Optional, cast

import aiohttp

from src.models import Job
from src.services.profile.models import SearchConfig
from src.sources.base import BaseJobSource, _is_uk_or_remote
from src.utils.dates import normalize_posted_at

logger = logging.getLogger("job360.sources.jsearch")

# JSearch's canonical home is now OpenWeb Ninja's own infrastructure; RapidAPI
# is an alternate channel. Keys issued by the OpenWeb Ninja portal ONLY work
# against this host — the old jsearch.p.rapidapi.com host returns 403 for them.
#
# Use `/search`, NOT `/search-v2`: both return HTTP 200, but v2 wraps results in
# a dict while `/search` returns `data` as a flat list of job objects — the
# shape this module's parser (and the RapidAPI channel) expects. Verified live
# 2026-08-11: /search -> data=list[10 dicts], /search-v2 -> data=dict.
_JSEARCH_URL = "https://api.openwebninja.com/jsearch/search"


class JSearchSource(BaseJobSource):
    name = "jsearch"
    category = "keyed_api"

    def __init__(self, session: aiohttp.ClientSession, api_key: str = "", search_config: Optional[SearchConfig] = None):
        super().__init__(session, search_config=search_config)
        self._api_key = api_key

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    async def fetch_jobs(self) -> list[Job]:
        if not self.is_configured:
            logger.warning("JSearch: no API key, skipping")
            return []
        jobs = []
        consecutive_failures = 0
        # JSearch moved to OpenWeb Ninja's own infrastructure (verified live
        # 2026-08-11). RapidAPI is now just an alternate distribution channel —
        # identical endpoints, params and response shape, but a DIFFERENT host
        # and auth header. A key issued by the OpenWeb Ninja portal returns 403
        # against the old rapidapi host, which is what this source was doing.
        # `x-api-key` is ignored by RapidAPI and vice-versa, so sending both
        # keeps either style of key working without needing to detect which.
        headers = {
            "x-api-key": self._api_key,
            "X-RapidAPI-Key": self._api_key,
            "X-RapidAPI-Host": "jsearch.p.rapidapi.com",
        }
        # Bounded to 4 queries: JSearch has 3.0s rate limit + 2.0s sleep between
        # calls = ~20s base, plus retries. Source timeout is 120s (main.py).
        queries = self.search_queries[:4]
        if not queries:
            logger.info("JSearch: no search queries in profile, skipping")
            return []
        for i, query in enumerate(queries):
            if i > 0:
                await asyncio.sleep(2.0)
            params = {
                "query": query,
                "page": "1",
                "num_pages": "1",
                "date_posted": "week",
                # WHY THIS MATTERS: JSearch indexes Google for Jobs worldwide
                # and its `country` parameter defaults to the US. We never sent
                # it, so every request asked a US-scoped index for UK-shaped
                # queries — which is why American postings leaked into a
                # UK-only product and why the UK supply came back thin. "uk" is
                # a documented country code (openwebninja.com/api/jsearch).
                #
                # This is a catalog-scope decision, not a user filter: a job the
                # user cannot take because it is abroad is a defect at
                # ingestion (rule #30). Nothing here is inferred about the user.
                "country": "uk",
            }
            data = await self._get_json(
                _JSEARCH_URL,
                params=params,
                headers=headers,
            )
            if not data or "data" not in data:
                consecutive_failures += 1
                if consecutive_failures >= 2:
                    logger.warning("JSearch: %s consecutive failures, stopping early", consecutive_failures)
                    break
                continue
            consecutive_failures = 0
            for item in cast(dict[str, Any], data)["data"]:
                title = item.get("job_title", "")
                description = item.get("job_description", "")
                location_parts = [
                    item.get("job_city", ""),
                    item.get("job_country", ""),
                ]
                location = ", ".join(p for p in location_parts if p)

                if not _is_uk_or_remote(location):
                    continue

                now_iso = datetime.now(timezone.utc).isoformat()
                raw_posted = item.get("job_posted_at_datetime_utc")
                posted_at, confidence = normalize_posted_at(raw_posted)

                jobs.append(Job(
                    title=title,
                    company=item.get("employer_name", ""),
                    location=location,
                    salary_min=item.get("job_min_salary"),
                    salary_max=item.get("job_max_salary"),
                    description=description[:5000],
                    apply_url=item.get("job_apply_link", ""),
                    source=self.name,
                    date_found=now_iso,
                    posted_at=posted_at,
                    date_confidence=confidence,
                    date_posted_raw=raw_posted,
                ))
        logger.info("JSearch: found %s jobs", len(jobs))
        return jobs
