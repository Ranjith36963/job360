"""GOV.UK Find an Apprenticeship API — QUARANTINED (endpoint retired).

NOTE: As of 2026-06 the keyless v1 API is gone. The old URL
``https://findapprenticeship.service.gov.uk/api/v1/vacancies`` 302s to the
``www.`` host, which serves an HTML "Page not found" page with HTTP 200 —
so JSON parsing raised "Expecting value: line 1 column 1" and burned three
retries per query at WARNING level. The docs page (``/pages/api``) is gone
too. Probed 2026-06-11. The replacement is the DfE Apprenticeships
"Display Adverts" API, which requires registration and a subscription key
(credentials decision — see the M1 mission notes / NEEDS-HUMAN queue).

``fetch_jobs`` therefore makes a single un-retried canary request. While
the endpoint keeps answering non-JSON it logs one INFO line and returns
[]. If real JSON ever returns, the canary result is parsed and the
remaining profile-driven queries resume without further changes.

Rate limit (historical, if revived): 150 requests per 5 minutes.
"""
import asyncio
import json
import logging
from datetime import datetime, timezone

import aiohttp

from src.core.settings import REQUEST_TIMEOUT
from src.models import Job
from src.sources.base import BaseJobSource, _is_uk_or_remote

logger = logging.getLogger("job360.sources.gov_apprenticeships")


class GovApprenticeshipsSource(BaseJobSource):
    name = "gov_apprenticeships"
    category = "rss"  # 15-min tier
    # Apprenticeships span every domain (trades, healthcare, tech, finance)
    # so keep in "general" alongside the education tag.
    DOMAINS = {"education", "general"}

    API_URL = "https://findapprenticeship.service.gov.uk/api/v1/vacancies"

    async def _probe_canary(self, params: dict) -> dict | None:
        """Single un-retried request against the retired endpoint.

        Returns the parsed JSON dict if the API ever comes back, None
        while it keeps serving the HTML not-found page. Bypasses the base
        retry helpers on purpose: the HTML response is deterministic, so
        retries only burn time and log warnings.
        """
        await self._rate_limiter.acquire()
        try:
            async with self._session.get(
                self.API_URL,
                params=params,
                headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json(content_type=None)
                return data if isinstance(data, dict) else None
        except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError):
            return None
        finally:
            self._rate_limiter.release()

    async def fetch_jobs(self) -> list[Job]:
        # The real API supported `q=` keyword filters; we fan out over the
        # user's top queries when profile-driven, else do an unfiltered
        # pull. Kept bounded to avoid eating the 150/5min budget.
        queries = self.search_queries[:5] or [""]
        all_results: list[Job] = []
        seen_urls: set[str] = set()

        # Canary: probe the first query once, without retries. The endpoint
        # was retired upstream (HTML 200 not-found page) — see module docs.
        canary_params: dict[str, str] = {"q": queries[0]} if queries[0] else {}
        canary_data = await self._probe_canary(canary_params)
        if canary_data is None:
            logger.info(
                "GovApprenticeships: v1 API still retired upstream "
                "(non-JSON response) — quarantined, 0 jobs",
            )
            return []

        responses: list[dict] = [canary_data]
        for q in queries[1:]:
            params: dict[str, str] = {}
            if q:
                params["q"] = q
            data = await self._get_json(self.API_URL, params=params)
            if not data or not isinstance(data, dict):
                continue
            responses.append(data)

        for data in responses:
            raw_vacancies = data.get("vacancies") or data.get("results") or []
            if not isinstance(raw_vacancies, list):
                continue

            now_iso = datetime.now(timezone.utc).isoformat()
            for item in raw_vacancies:
                apply_url = item.get("vacancyUrl") or item.get("url") or ""
                if apply_url in seen_urls:
                    continue
                seen_urls.add(apply_url)

                location = (
                    item.get("location")
                    or item.get("town")
                    or item.get("address", {}).get("town")
                    if isinstance(item.get("address"), dict)
                    else item.get("location") or "UK"
                )
                if not _is_uk_or_remote(str(location)):
                    continue

                raw_posted = (
                    item.get("postedDate")
                    or item.get("publishedDate")
                    or item.get("posted_date")
                )
                posted_at = raw_posted if raw_posted else None
                confidence = "high" if raw_posted else "low"

                all_results.append(Job(
                    title=(item.get("title") or item.get("jobTitle") or "Apprenticeship"),
                    company=(item.get("employerName") or item.get("employer") or "UK Apprenticeship"),
                    location=str(location),
                    description=(item.get("description") or "")[:5000],
                    apply_url=apply_url,
                    source=self.name,
                    date_found=now_iso,
                    posted_at=posted_at,
                    date_confidence=confidence,
                    date_posted_raw=raw_posted,
                ))

        logger.info("GovApprenticeships: found %s relevant jobs", len(all_results))
        return all_results
