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
    category = "scraper"

    async def fetch_jobs(self) -> list[Job]:
        jobs = []
        seen_urls = set()

        queries = self.search_queries[:6]  # Bounded to prevent source timeout
        if not queries:
            logger.info("AI Jobs Global: no search queries in profile, skipping")
            return []

        for query in queries:
            # Try WP Job Manager AJAX endpoint
            params = {
                "action": "workscout_incremental_jobs_suggest",
                "term": query,
            }
            data = await self._get_json(
                "https://ai-jobs.global/wp-admin/admin-ajax.php",
                params=params,
            )

            if data and isinstance(data, list):
                for item in data:
                    job = self._parse_ajax_item(item)
                    if job and job.apply_url not in seen_urls:
                        seen_urls.add(job.apply_url)
                        jobs.append(job)
                continue

            # Fallback: try HTML with search param
            html = await self._get_text(
                "https://ai-jobs.global/",
                params={"s": query, "post_type": "job_listing"},
            )
            if html:
                for job in self._parse_html(html):
                    if job.apply_url not in seen_urls:
                        seen_urls.add(job.apply_url)
                        jobs.append(job)

        logger.info("AI Jobs Global: found %s relevant jobs", len(jobs))
        return jobs

    def _parse_ajax_item(self, item: dict) -> Job | None:
        now = datetime.now(timezone.utc).isoformat()

        title = item.get("label", "") or item.get("value", "") or item.get("title", "")
        if not title:
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
        try:
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
        except Exception as e:
            logger.warning("AI Jobs Global: HTML parsing failed: %s", e)
            return []
