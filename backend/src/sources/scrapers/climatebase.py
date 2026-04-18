import json
import logging
import re
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource, _is_uk_or_remote

logger = logging.getLogger("job360.sources.climatebase")

# Regex to extract Next.js embedded JSON data
_NEXT_DATA_RE = re.compile(r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL)


class ClimatebaseSource(BaseJobSource):
    """Climatebase — climate tech jobs. Extracts from Next.js embedded JSON."""
    name = "climatebase"
    category = "scraper"

    async def fetch_jobs(self) -> list[Job]:
        jobs = []
        seen_ids = set()
        queries = ["data scientist", "machine learning", "AI", "data engineer"]

        for query in queries:
            html = await self._get_text(
                "https://climatebase.org/jobs",
                params={"l": "United Kingdom", "q": query},
            )
            if not html:
                continue

            parsed = self._extract_jobs_from_next_data(html)
            for job in parsed:
                job_id = job.apply_url
                if job_id not in seen_ids:
                    seen_ids.add(job_id)
                    jobs.append(job)

        jobs = [j for j in jobs if _is_uk_or_remote(j.location)]
        logger.info("Climatebase: found %s relevant jobs", len(jobs))
        return jobs

    def _extract_jobs_from_next_data(self, html: str) -> list[Job]:
        """Extract jobs from Next.js __NEXT_DATA__ script tag."""
        try:
            jobs = []
            now = datetime.now(timezone.utc).isoformat()

            match = _NEXT_DATA_RE.search(html)
            if not match:
                # Fallback to HTML scraping if __NEXT_DATA__ not found
                return self._parse_html_fallback(html)

            try:
                data = json.loads(match.group(1))
            except json.JSONDecodeError:
                return self._parse_html_fallback(html)

            # Navigate to jobs in the Next.js props structure
            page_props = data.get("props", {}).get("pageProps", {})
            job_list = page_props.get("jobs", [])

            if not isinstance(job_list, list):
                return self._parse_html_fallback(html)

            for item in job_list:
                title = item.get("title", "")
                company = item.get("name_of_employer", "") or item.get("company", "") or "Unknown"

                locations = item.get("locations", [])
                if isinstance(locations, list):
                    location = ", ".join(str(l) for l in locations) if locations else "United Kingdom"
                else:
                    location = str(locations) if locations else "United Kingdom"

                job_id = item.get("id", "")
                apply_url = f"https://climatebase.org/jobs/{job_id}" if job_id else ""

                salary_min = item.get("salary_from")
                salary_max = item.get("salary_to")

                jobs.append(Job(
                    title=title,
                    company=company,
                    location=location,
                    description=title,
                    apply_url=apply_url,
                    source=self.name,
                    date_found=now,
                    posted_at=None,
                    date_confidence="low",
                    date_posted_raw=None,
                    salary_min=salary_min,
                    salary_max=salary_max,
                ))

            return jobs
        except Exception as e:
            logger.warning("Climatebase: HTML/JSON parsing failed: %s", e)
            return []

    def _parse_html_fallback(self, html: str) -> list[Job]:
        """Fallback HTML parsing if __NEXT_DATA__ extraction fails."""
        try:
            jobs = []
            now = datetime.now(timezone.utc).isoformat()

            link_pattern = re.compile(
                r'<a[^>]+href="(/jobs/[^"]+)"[^>]*>([^<]+)</a>',
                re.IGNORECASE
            )

            for match in link_pattern.finditer(html):
                path, title = match.group(1), match.group(2).strip()
                apply_url = f"https://climatebase.org{path}"
                jobs.append(Job(
                    title=title,
                    company="Unknown",
                    location="United Kingdom",
                    description=title,
                    apply_url=apply_url,
                    source=self.name,
                    date_found=now,
                    posted_at=None,
                    date_confidence="low",
                    date_posted_raw=None,
                ))

            return jobs
        except Exception as e:
            logger.warning("Climatebase: HTML fallback parsing failed: %s", e)
            return []
