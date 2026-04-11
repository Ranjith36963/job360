import logging
import re
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource, _is_uk_or_remote

logger = logging.getLogger("job360.sources.aijobs_ai")

# Broader patterns to match aijobs.ai HTML structure
_JOB_LINK_RE = re.compile(
    r'<a[^>]+href="((?:https://aijobs\.ai)?/job[s]?/[^"]+)"[^>]*>\s*([^<]+?)\s*</a>',
    re.IGNORECASE,
)


class AIJobsAISource(BaseJobSource):
    """aijobs.ai — dedicated AI job board with server-rendered listings."""
    name = "aijobs_ai"
    category = "scraper"

    async def fetch_jobs(self) -> list[Job]:
        jobs = []
        seen_urls = set()

        # Try multiple pages/categories
        urls = [
            "https://aijobs.ai/",
            "https://aijobs.ai/remote/",
        ]

        for page_url in urls:
            html = await self._get_text(page_url)
            if not html:
                continue
            for job in self._parse_html(html):
                if job.apply_url not in seen_urls:
                    seen_urls.add(job.apply_url)
                    jobs.append(job)

        logger.info("AI Jobs AI: found %s relevant jobs", len(jobs))
        return jobs

    def _parse_html(self, html: str) -> list[Job]:
        try:
            jobs = []
            now = datetime.now(timezone.utc).isoformat()

            for match in _JOB_LINK_RE.finditer(html):
                path, title = match.group(1), match.group(2).strip()

                # Skip navigation/non-job links
                if len(title) < 5 or title.lower() in ("view all", "see more", "load more"):
                    continue

                # Try to extract company/location from nearby HTML
                pos = match.start()
                block = html[max(0, pos - 500):pos + 1000]

                company = self._extract_nearby(block, r'(?:company|employer|org)[^"]*"[^>]*>\s*([^<]+)')
                location = self._extract_nearby(block, r'(?:location|city)[^"]*"[^>]*>\s*([^<]+)')

                if not _is_uk_or_remote(location):
                    continue

                apply_url = path if path.startswith("http") else f"https://aijobs.ai{path}"

                jobs.append(Job(
                    title=title,
                    company=company or "Unknown",
                    location=location or "",
                    description=title,
                    apply_url=apply_url,
                    source=self.name,
                    date_found=now,
                ))

            return jobs
        except Exception as e:
            logger.warning("AI Jobs AI: HTML parsing failed: %s", e)
            return []

    @staticmethod
    def _extract_nearby(block: str, pattern: str) -> str:
        m = re.search(pattern, block, re.IGNORECASE)
        return m.group(1).strip() if m else ""
