import logging
import re
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource

logger = logging.getLogger("job360.sources.devitjobs")

_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    """Remove HTML tags and collapse whitespace."""
    clean = _HTML_TAG_RE.sub(" ", text)
    return re.sub(r"\s+", " ", clean).strip()


_DESC_CAP = 30  # Max jobs to fetch descriptions for (must fit within 60s timeout)


class DevITJobsSource(BaseJobSource):
    name = "devitjobs"

    async def _fetch_description(self, slug: str) -> str:
        """Fetch job detail page and extract description text."""
        if not slug:
            return ""
        url = f"https://devitjobs.uk/jobs/{slug}"
        html = await self._get_text(url)
        if not html:
            return ""
        # Extract from JSON-LD or meta description or main content
        # DevITJobs embeds job data in a <script type="application/ld+json"> block
        import json as _json
        ld_match = re.search(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL)
        if ld_match:
            try:
                ld = _json.loads(ld_match.group(1))
                raw = ld.get("description", "")
                if raw:
                    return _strip_html(raw)[:5000]
            except (ValueError, TypeError):
                pass
        # Fallback: extract from meta og:description
        meta_match = re.search(r'<meta[^>]*property="og:description"[^>]*content="([^"]*)"', html)
        if meta_match:
            return _strip_html(meta_match.group(1))[:5000]
        return ""

    async def fetch_jobs(self) -> list[Job]:
        # Light listing for fast relevance filtering
        data = await self._get_json("https://devitjobs.uk/api/jobsLight")
        if not data or not isinstance(data, list):
            return []

        jobs = []
        slugs = []
        for item in data:
            title = item.get("name", "")
            if not self._relevance_match(title.lower()):
                continue

            company = item.get("company", "")
            location = item.get("actualCity", "")
            raw_url = item.get("jobUrl", "")
            slug = raw_url if not raw_url.startswith("http") else ""
            apply_url = raw_url if raw_url.startswith("http") else f"https://devitjobs.uk/jobs/{raw_url}" if raw_url else ""
            date_found = item.get("activeFrom") or item.get("publishedAt") or datetime.now(timezone.utc).isoformat()

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

            job = Job(
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
            )
            jobs.append(job)
            slugs.append(slug)

        # Batch-fetch descriptions for top jobs (capped to avoid rate-limit abuse)
        if jobs and slugs:
            to_fetch = min(len(jobs), _DESC_CAP)
            coros = [self._fetch_description(slugs[i]) for i in range(to_fetch)]
            descs = await self._gather_queries(coros, batch_size=5)
            for i, desc in enumerate(descs):
                if desc:
                    jobs[i].description = desc
            fetched = sum(1 for d in descs if d)
            if fetched:
                logger.info(f"DevITjobs: fetched {fetched}/{to_fetch} descriptions")

        logger.info(f"DevITjobs: found {len(jobs)} relevant jobs")
        return jobs
