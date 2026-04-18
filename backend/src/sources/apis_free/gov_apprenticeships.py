"""GOV.UK Find an Apprenticeship API.

Docs: https://findapprenticeship.service.gov.uk/pages/api
Rate limit: 150 requests per 5 minutes (published — cite this in tests).
We poll every 15 min so a single scrape uses ~1 request — well under budget.
"""
import logging
from datetime import datetime, timezone

from src.models import Job
from src.sources.base import BaseJobSource, _is_uk_or_remote

logger = logging.getLogger("job360.sources.gov_apprenticeships")


class GovApprenticeshipsSource(BaseJobSource):
    name = "gov_apprenticeships"
    category = "rss"  # 15-min tier

    API_URL = "https://findapprenticeship.service.gov.uk/api/v1/vacancies"

    async def fetch_jobs(self) -> list[Job]:
        # The real API supports `q=` keyword filters; we fan out over the
        # user's top queries when profile-driven, else do an unfiltered
        # pull. Kept bounded to avoid eating the 150/5min budget.
        queries = self.search_queries[:5] or [""]
        all_results: list[Job] = []
        seen_urls: set[str] = set()

        for q in queries:
            params: dict[str, str] = {}
            if q:
                params["q"] = q
            data = await self._get_json(self.API_URL, params=params)
            if not data or not isinstance(data, dict):
                continue
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
