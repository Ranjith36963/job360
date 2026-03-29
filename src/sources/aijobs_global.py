import logging
import re
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource, _is_uk_or_remote

logger = logging.getLogger("job360.sources.aijobs_global")

class AIJobsGlobalSource(BaseJobSource):
    """AI Jobs Worldwide — ai-jobs.global (WordPress + WP Job Manager)."""
    name = "aijobs_global"

    async def fetch_jobs(self) -> list[Job]:
        if not self.search_queries:
            logger.info("AI Jobs Global: no search queries configured, skipping")
            return []

        # Run queries concurrently in batches (not sequentially)
        queries = self.search_queries[:5]  # WordPress site doesn't need 15 queries
        coros = [self._fetch_single_query(q) for q in queries]
        batch_results = await self._gather_queries(coros, batch_size=3)

        # Merge and deduplicate
        jobs = []
        seen_urls: set[str] = set()
        for query_jobs in batch_results:
            for job in query_jobs:
                if job.apply_url not in seen_urls:
                    seen_urls.add(job.apply_url)
                    jobs.append(job)

        logger.info(f"AI Jobs Global: found {len(jobs)} relevant jobs")
        return jobs

    async def _fetch_single_query(self, query: str) -> list[Job]:
        """Fetch jobs for a single query via HTML search (AJAX endpoint unreliable)."""
        html = await self._get_text(
            "https://ai-jobs.global/",
            params={"s": query, "post_type": "job_listing"},
        )
        if html:
            return self._parse_html(html)
        return []

    def _parse_ajax_item(self, item: dict) -> Job | None:
        now = datetime.now(timezone.utc).isoformat()

        title = item.get("label", "") or item.get("value", "") or item.get("title", "")
        if not title:
            return None

        text = title.lower()
        if not self._relevance_match(text):
            return None

        location = item.get("location", "") or ""
        if not _is_uk_or_remote(location):
            return None

        apply_url = item.get("url", "") or item.get("link", "") or ""
        company = item.get("company", "") or "Unknown"

        return Job(
            title=title,
            company=company,
            location=location or "",
            description=title,
            apply_url=apply_url,
            source=self.name,
            date_found=now,
        )

    def _parse_html(self, html: str) -> list[Job]:
        """Fallback HTML parsing for WP Job Manager listings."""
        jobs = []
        now = datetime.now(timezone.utc).isoformat()

        # WP Job Manager uses .job_listing class
        link_pattern = re.compile(
            r'<a[^>]+href="(https://ai-jobs\.global/job[s]?/[^"]+)"[^>]*>\s*([^<]+?)\s*</a>',
            re.IGNORECASE,
        )

        for match in link_pattern.finditer(html):
            url, title = match.group(1), match.group(2).strip()

            if len(title) < 5:
                continue

            text = title.lower()
            if not self._relevance_match(text):
                continue

            jobs.append(Job(
                title=title,
                company="Unknown",
                location="",
                description=title,
                apply_url=url,
                source=self.name,
                date_found=now,
            ))

        return jobs
