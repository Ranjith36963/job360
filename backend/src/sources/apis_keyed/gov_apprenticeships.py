"""DfE "Find an apprenticeship" Display Advert API v2 — keyed source.

The old keyless v1 (``findapprenticeship.service.gov.uk/api/v1/vacancies``)
was retired in 2026. Its replacement is the Azure-APIM-gated Display Advert
API v2, which needs a free subscription key (register at
https://developer.apprenticeships.education.gov.uk). Without the key this
source skips gracefully and returns [].

The v2 API has NO free-text keyword parameter — you filter by location,
route, LARS code or recency and page through. We pull the most recently
posted vacancies (``Sort=AgeDesc``) bounded by ``PostedInLastNumberOfDays``
plus a page cap, so we stay inside the 150-requests / 5-min budget, then
keep them all (every DfE apprenticeship is UK, so no location filtering).

Endpoint + contract verified against the live OpenAPI v2 spec at
developer.apprenticeships.education.gov.uk (2026-06).

Rate limit: 150 requests per 5-minute rolling window.
"""
import logging
from datetime import datetime, timezone
from typing import Any, Optional

import aiohttp

from src.models import Job
from src.services.profile.models import SearchConfig
from src.sources.base import BaseJobSource

logger = logging.getLogger("job360.sources.gov_apprenticeships")

API_URL = "https://api.apprenticeships.education.gov.uk/vacancies/vacancy"


class GovApprenticeshipsSource(BaseJobSource):
    name = "gov_apprenticeships"
    category = "keyed_api"  # subscription key required → 5-min tier
    # Apprenticeships span every domain (trades, healthcare, tech, finance),
    # so keep them in "general" alongside the education tag.
    DOMAINS = {"education", "general"}

    # Bounded paging keeps us well inside the 150-req / 5-min budget.
    PAGE_SIZE = 50
    MAX_PAGES = 5
    POSTED_IN_LAST_DAYS = 30

    def __init__(
        self,
        session: aiohttp.ClientSession,
        api_key: str = "",
        search_config: Optional[SearchConfig] = None,
    ):
        super().__init__(session, search_config=search_config)
        self._api_key = api_key

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    async def fetch_jobs(self) -> list[Job]:
        if not self.is_configured:
            logger.info("GovApprenticeships: no API key, skipping")
            return []

        headers = {
            "Ocp-Apim-Subscription-Key": self._api_key,
            "X-Version": "2",
        }
        jobs: list[Job] = []
        seen: set[str] = set()

        for page in range(1, self.MAX_PAGES + 1):
            params = {
                "PageNumber": page,
                "PageSize": self.PAGE_SIZE,
                "Sort": "AgeDesc",
                "PostedInLastNumberOfDays": self.POSTED_IN_LAST_DAYS,
            }
            data = await self._get_json(API_URL, params=params, headers=headers)
            if not data or not isinstance(data, dict):
                break
            vacancies = data.get("vacancies") or []
            if not isinstance(vacancies, list) or not vacancies:
                break
            for item in vacancies:
                job = self._parse_vacancy(item, seen)
                if job is not None:
                    jobs.append(job)
            total_pages = data.get("totalPages")
            if isinstance(total_pages, int) and page >= total_pages:
                break

        logger.info("GovApprenticeships: found %s jobs", len(jobs))
        return jobs

    def _parse_vacancy(self, item: dict[str, Any], seen: set[str]) -> Optional[Job]:
        ref = item.get("vacancyReference") or ""
        # Prefer the employer's external apply link; fall back to the gov.uk page.
        apply_url = item.get("applicationUrl") or item.get("vacancyUrl") or ""
        dedup_key = ref or apply_url
        if not dedup_key or dedup_key in seen:
            return None
        seen.add(dedup_key)

        location = "UK"
        addresses = item.get("addresses")
        if isinstance(addresses, list) and addresses:
            first = addresses[0] or {}
            location = first.get("addressLine1") or first.get("postcode") or "UK"

        # Only treat an annual wage as salary — apprentice pay is often hourly,
        # and Job.__post_init__ would null small values anyway.
        salary: Optional[float] = None
        wage = item.get("wage")
        if isinstance(wage, dict) and str(wage.get("wageUnit", "")).lower() == "annually":
            amount = wage.get("wageAmount")
            if isinstance(amount, (int, float)) and amount > 0:
                salary = float(amount)

        raw_posted = item.get("postedDate")
        now_iso = datetime.now(timezone.utc).isoformat()

        return Job(
            title=item.get("title", ""),
            company=item.get("employerName", "") or "Unknown",
            location=str(location),
            salary_min=salary,
            salary_max=salary,
            description=item.get("description", "") or "",
            apply_url=apply_url,
            source=self.name,
            date_found=now_iso,
            posted_at=raw_posted if raw_posted else None,
            date_confidence="high" if raw_posted else "low",
            date_posted_raw=raw_posted,
        )
