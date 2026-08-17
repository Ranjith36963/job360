import logging
from datetime import datetime, timezone
from typing import Any, cast

from src.models import Job
from src.sources.base import BaseJobSource, _is_uk_or_remote
from src.utils.dates import normalize_posted_at

logger = logging.getLogger("job360.sources.jobicy")

class JobicySource(BaseJobSource):
    name = "jobicy"
    category = "free_json"

    async def fetch_jobs(self) -> list[Job]:
        jobs = []
        # No "tag" param: Jobicy 400s on values shorter than 3 chars (e.g. "ai").
        # The industry filter is enough; relevance is filtered by the scorer downstream.
        params = {
            "count": "50",
            "geo": "uk",
            "industry": "data-science",
        }
        data = await self._get_json(
            "https://jobicy.com/api/v2/remote-jobs", params=params
        )
        if not data or "jobs" not in data:
            return []
        for item in cast(dict[str, Any], data)["jobs"]:
            title = item.get("jobTitle", "")
            # `jobDescription` (100% fill) is the full prose posting;
            # `jobExcerpt` is just a short teaser. Prefer the full text and
            # fall back to the excerpt only when the description is missing.
            description = item.get("jobDescription") or item.get("jobExcerpt", "")
            now_iso = datetime.now(timezone.utc).isoformat()
            raw_pub = item.get("pubDate")
            posted_at, confidence = normalize_posted_at(raw_pub)

            # `jobType` (100% fill live, e.g. ["Full-Time"]) arrives as a
            # list of one. `salaryCurrency`/`salaryPeriod` sit on the same
            # item as salaryMin/Max (measured live: filled together, ~20% of
            # rows, same rows that carry a salary at all) and were unread —
            # raw values only, the gate converts units.
            job_type_list = item.get("jobType")
            employment_type = job_type_list[0] if isinstance(job_type_list, list) and job_type_list else job_type_list

            jobs.append(Job(
                title=title,
                company=item.get("companyName", ""),
                location=item.get("jobGeo", ""),
                # `annualSalaryMin`/`annualSalaryMax` never appear on the live
                # API — verified against a real response. The real keys are
                # `salaryMin`/`salaryMax` (23% fill).
                salary_min=item.get("salaryMin"),
                salary_max=item.get("salaryMax"),
                salary_currency=item.get("salaryCurrency"),
                salary_period=item.get("salaryPeriod"),
                description=description,
                apply_url=item.get("url", ""),
                source=self.name,
                date_found=now_iso,
                posted_at=posted_at,
                date_confidence=confidence,
                date_posted_raw=raw_pub,
                # `jobLevel` (100% fill, e.g. "Senior").
                experience_level=item.get("jobLevel", ""),
                employment_type=employment_type,
            ))
        jobs = [j for j in jobs if _is_uk_or_remote(j.location)]
        logger.info("Jobicy: found %s relevant jobs", len(jobs))
        return jobs
