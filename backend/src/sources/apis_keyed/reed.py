import base64
import logging
from datetime import datetime, timezone
from typing import Any, Optional, cast

import aiohttp

from src.core.settings import RATE_LIMITS, SOURCE_FETCH_TIMEOUT
from src.models import Job
from src.services.profile.models import SearchConfig
from src.sources.base import BaseJobSource, _is_uk_or_remote
from src.utils.dates import normalize_posted_at

logger = logging.getLogger("job360.sources.reed")

class ReedSource(BaseJobSource):
    name = "reed"
    category = "keyed_api"

    def __init__(self, session: aiohttp.ClientSession, api_key: str = "", search_config: Optional[SearchConfig] = None):
        super().__init__(session, search_config=search_config)
        self._api_key = api_key

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    async def fetch_jobs(self) -> list[Job]:
        if not self.is_configured:
            logger.warning("Reed: no API key, skipping")
            return []
        jobs = []
        auth = base64.b64encode(f"{self._api_key}:".encode()).decode()
        headers = {"Authorization": f"Basic {auth}"}
        # S2 — size the fan-out from the actual time budget instead of a
        # hand-picked constant. Reed is rate-limited to one request every
        # RATE_LIMITS["reed"]["delay"] seconds, so N requests cost at LEAST
        # N * delay before any network time. At the previous 8 titles x 3
        # locations that was 24 * 2.0s = 48s of pure waiting against a 60s
        # SOURCE_FETCH_TIMEOUT — it did not always blow the ceiling, which is
        # worse than always failing: the source died intermittently, and a
        # timeout looks identical to "Reed had nothing today".
        #
        # Deriving the cap means a later change to the delay or the timeout
        # cannot silently push this back over the edge.
        locations = ["London", "UK", "Remote"]
        _reed_limits = RATE_LIMITS.get("reed")
        _delay = float(_reed_limits["delay"]) if _reed_limits else 2.0
        # Spend at most 60% of the budget on enforced delay, leaving the rest
        # for real HTTP latency and parsing.
        _max_requests = max(1, int((SOURCE_FETCH_TIMEOUT * 0.6) / _delay))
        _max_titles = max(1, _max_requests // len(locations))
        queries = self.job_titles[:_max_titles]
        for query in queries:
            for loc in locations:
                params = {
                    "keywords": query,
                    "locationName": loc,
                    "resultsToTake": 50,
                }
                data = await self._get_json(
                    "https://www.reed.co.uk/api/1.0/search",
                    params=params,
                    headers=headers,
                )
                if not data or "results" not in data:
                    continue
                for item in cast(dict[str, Any], data)["results"]:
                    now_iso = datetime.now(timezone.utc).isoformat()
                    raw_date = item.get("date") or item.get("datePosted")
                    posted_at, confidence = normalize_posted_at(raw_date)

                    jobs.append(Job(
                        title=item.get("jobTitle", ""),
                        company=item.get("employerName", ""),
                        location=item.get("locationName", ""),
                        salary_min=item.get("minimumSalary"),
                        salary_max=item.get("maximumSalary"),
                        description=item.get("jobDescription", ""),
                        apply_url=f"https://www.reed.co.uk/jobs/{item.get('jobId', '')}",
                        source=self.name,
                        date_found=now_iso,
                        posted_at=posted_at,
                        date_confidence=confidence,
                        date_posted_raw=raw_date,
                    ))
        jobs = [j for j in jobs if _is_uk_or_remote(j.location)]
        logger.info("Reed: found %s jobs", len(jobs))
        return jobs
