import html
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

            # `jobIndustry` (100% fill live 2026-08-17, e.g. "Software
            # Engineering", "Data Science &amp; Analytics", "Sales") is
            # Jobicy's OWN closed industry list -- the same concept as the
            # JobCategory shelf, unread until now. Two transport details, not
            # normalisation: it arrives as a list of one (same shape as
            # `jobType` above), and the feed HTML-escapes the ampersand, so
            # the raw value has to be unescaped before it is a real string.
            industry_list = item.get("jobIndustry")
            industry = (
                industry_list[0]
                if isinstance(industry_list, list) and industry_list
                else industry_list
            )
            category = html.unescape(str(industry)) if industry else None

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
                # `jobLevel` (100% fill live, e.g. "Senior") feeds BOTH the
                # legacy free-text `experience_level` (unchanged) AND the
                # closed-enum `seniority` shelf (new) -- same raw string, two
                # different Job fields, dumb pass-through either way; the
                # gate's closed-set matcher decides whether the token lands.
                experience_level=item.get("jobLevel", ""),
                seniority=item.get("jobLevel"),
                employment_type=employment_type,
                category=category,
            ))
        jobs = [j for j in jobs if _is_uk_or_remote(j.location)]
        logger.info("Jobicy: found %s relevant jobs", len(jobs))
        return jobs
