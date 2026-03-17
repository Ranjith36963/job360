import logging
import re
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource

logger = logging.getLogger("job360.sources.bcs_jobs")

# Broader link patterns for BCS job board
_JOB_LINK_RE = re.compile(
    r'<a[^>]+href="([^"]*(?:job|vacanc|career|position|opportunity)[^"]*)"[^>]*>\s*([^<]{5,}?)\s*</a>',
    re.IGNORECASE,
)


class BCSJobsSource(BaseJobSource):
    """BCS (Chartered Institute for IT) Job Board — UK IT professional jobs."""
    name = "bcs_jobs"

    async def fetch_jobs(self) -> list[Job]:
        # Try multiple URL patterns
        urls = [
            "https://www.bcs.org/jobs-board",
            "https://www.bcs.org/jobs",
        ]

        html = None
        for url in urls:
            html = await self._get_text(url)
            if html:
                break

        if not html:
            return []

        jobs = self._parse_html(html)
        logger.info(f"BCS Jobs: found {len(jobs)} relevant jobs")
        return jobs

    def _parse_html(self, html: str) -> list[Job]:
        jobs = []
        now = datetime.now(timezone.utc).isoformat()
        seen_urls = set()

        for match in _JOB_LINK_RE.finditer(html):
            path, title = match.group(1), match.group(2).strip()

            # Skip navigation links
            if title.lower() in ("jobs", "careers", "job board", "search", "view all"):
                continue

            text = title.lower()
            if not any(kw in text for kw in self.relevance_keywords):
                continue

            if path.startswith("http"):
                apply_url = path
            elif path.startswith("/"):
                apply_url = f"https://www.bcs.org{path}"
            else:
                apply_url = f"https://www.bcs.org/{path}"

            if apply_url in seen_urls:
                continue
            seen_urls.add(apply_url)

            jobs.append(Job(
                title=title,
                company="Unknown",
                location="UK",
                description=title,
                apply_url=apply_url,
                source=self.name,
                date_found=now,
            ))

        return jobs
