"""UK Department for Education — Teaching Vacancies API (schema.org JobPosting).

Docs: https://teaching-vacancies.service.gov.uk/pages/api_specification
Licence: Open Government Licence v3.0. No auth required.
No documented rate limit — be polite (polled on the 15-min RSS tier).
"""
import logging
from datetime import datetime, timezone

from src.models import Job
from src.sources.base import BaseJobSource, _is_uk_or_remote

logger = logging.getLogger("job360.sources.teaching_vacancies")


class TeachingVacanciesSource(BaseJobSource):
    name = "teaching_vacancies"
    category = "rss"  # 15-min tier — frequent enough for school-day postings

    API_URL = "https://teaching-vacancies.service.gov.uk/api/v1/jobs.json"

    async def fetch_jobs(self) -> list[Job]:
        data = await self._get_json(self.API_URL)
        if not data or not isinstance(data, dict):
            return []

        raw_jobs = data.get("jobs") or data.get("data") or []
        if not isinstance(raw_jobs, list):
            return []

        results: list[Job] = []
        now_iso = datetime.now(timezone.utc).isoformat()

        for item in raw_jobs:
            # schema.org JobPosting structure
            title = (item.get("title") or item.get("jobTitle") or "").strip()
            org = item.get("hiringOrganization") or {}
            company = (org.get("name") if isinstance(org, dict) else str(org)) or "UK School"

            loc_obj = item.get("jobLocation") or {}
            if isinstance(loc_obj, dict):
                addr = loc_obj.get("address") or {}
                if isinstance(addr, dict):
                    location = (
                        addr.get("addressLocality")
                        or addr.get("addressRegion")
                        or addr.get("postalCode")
                        or "UK"
                    )
                else:
                    location = str(addr) or "UK"
            else:
                location = str(loc_obj) or "UK"

            if not _is_uk_or_remote(location):
                continue

            raw_posted = item.get("datePosted") or item.get("date_posted")
            posted_at = raw_posted if raw_posted else None
            confidence = "high" if raw_posted else "low"

            apply_url = item.get("url") or item.get("applyUrl") or ""
            description = (item.get("description") or "")[:5000]

            results.append(Job(
                title=title or "Teaching Vacancy",
                company=company,
                location=location,
                description=description,
                apply_url=apply_url,
                source=self.name,
                date_found=now_iso,
                posted_at=posted_at,
                date_confidence=confidence,
                date_posted_raw=raw_posted,
            ))

        logger.info("TeachingVacancies: found %s relevant jobs", len(results))
        return results
