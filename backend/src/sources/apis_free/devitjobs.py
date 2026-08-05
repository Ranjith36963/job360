import logging
from datetime import datetime, timezone

from src.models import Job
from src.sources.base import BaseJobSource, _is_uk_or_remote
from src.utils.dates import normalize_posted_at

logger = logging.getLogger("job360.sources.devitjobs")

class DevITJobsSource(BaseJobSource):
    name = "devitjobs"
    category = "free_json"
    DOMAINS = {"tech"}

    async def fetch_jobs(self) -> list[Job]:
        data = await self._get_json("https://devitjobs.uk/api/jobsLight")
        if not data or not isinstance(data, list):
            return []

        jobs = []
        for item in data:
            title = item.get("name", "")
            company = item.get("company", "")
            location = item.get("actualCity", "")
            # The API's `jobUrl` is a SLUG, not a URL — "FBI-TMT-Metadata-Lead".
            # Stored raw, it produced an Apply button that goes nowhere, on 2,805
            # jobs: 43% of the entire catalog, since devitjobs is our largest
            # source. Nothing detected it for months, because a bad link only
            # fails when a user clicks it and we never see that click.
            # Found 2026-08-03 by the data-invariants detector on its first run.
            apply_url = (item.get("jobUrl") or "").strip()
            if apply_url and not apply_url.startswith(("http://", "https://")):
                # VERIFIED against the live site, not guessed: /jobs/<slug>
                # returns the real page (9.6 KB, company name present) while
                # /job/<slug> and /<slug> both return the 4.7 KB empty SPA shell.
                apply_url = f"https://devitjobs.uk/jobs/{apply_url.lstrip('/')}"
            now_iso = datetime.now(timezone.utc).isoformat()
            raw_published = item.get("publishedAt")
            posted_at, confidence = normalize_posted_at(raw_published)

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
                date_found=now_iso,
                posted_at=posted_at,
                date_confidence=confidence,
                date_posted_raw=raw_published,
                salary_min=salary_min,
                salary_max=salary_max,
                visa_flag=visa_flag,
                experience_level=exp_level,
            ))

        jobs = [j for j in jobs if _is_uk_or_remote(j.location)]
        logger.info("DevITjobs: found %s relevant jobs", len(jobs))
        return jobs
