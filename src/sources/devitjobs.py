import logging
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource
from src.config.keywords import RELEVANCE_KEYWORDS

logger = logging.getLogger("job360.sources.devitjobs")


class DevITJobsSource(BaseJobSource):
    name = "devitjobs"

    async def fetch_jobs(self) -> list[Job]:
        data = await self._get_json("https://devitjobs.uk/api/jobsLight")
        if not data or not isinstance(data, list):
            return []

        jobs = []
        for item in data:
            title = item.get("name", "")
            text = title.lower()
            if not any(kw in text for kw in RELEVANCE_KEYWORDS):
                continue

            company = item.get("company", "")
            location = item.get("actualCity", "")
            apply_url = item.get("jobUrl", "")
            date_found = item.get("publishedAt") or datetime.now(timezone.utc).isoformat()

            salary_min = item.get("annualSalaryFrom")
            salary_max = item.get("annualSalaryTo")
            if salary_min is not None:
                try:
                    salary_min = float(salary_min)
                except (ValueError, TypeError):
                    salary_min = None
            if salary_max is not None:
                try:
                    salary_max = float(salary_max)
                except (ValueError, TypeError):
                    salary_max = None

            visa_flag = bool(item.get("hasVisaSponsorship", False))
            exp_level = item.get("expLevel", "")

            jobs.append(Job(
                title=title,
                company=company,
                location=location,
                apply_url=apply_url,
                source=self.name,
                date_found=date_found,
                salary_min=salary_min,
                salary_max=salary_max,
                visa_flag=visa_flag,
                experience_level=exp_level,
            ))

        logger.info(f"DevITjobs: found {len(jobs)} relevant jobs")
        return jobs
