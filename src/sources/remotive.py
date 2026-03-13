"""Remotive — remote job board with free API.

No API key needed. JSON API.
Covers: global remote jobs across software, design, marketing, etc.
URL: https://remotive.com/api/remote-jobs
Note: Max ~4 requests/day recommended. Jobs delayed 24h.
"""

import logging
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource
from src.filters.skill_matcher import get_relevance_keywords, get_search_queries

logger = logging.getLogger("job360.sources.remotive")


class RemotiveSource(BaseJobSource):
    name = "remotive"

    async def fetch_jobs(self) -> list[Job]:
        jobs = []
        keywords = get_relevance_keywords()
        queries = get_search_queries(limit=2)

        for query in queries:
            data = await self._get_json(
                "https://remotive.com/api/remote-jobs",
                params={"search": query, "limit": 50},
            )
            if not data or "jobs" not in data:
                continue
            for item in data["jobs"]:
                title = item.get("title", "")
                company = item.get("company_name", "")
                desc = item.get("description", "")
                url = item.get("url", "")
                if not url:
                    continue

                text = f"{title} {desc} {company}".lower()
                if not any(kw in text for kw in keywords):
                    continue

                # Strip HTML
                import re
                clean_desc = re.sub(r"<[^>]+>", " ", desc)[:500]
                location = item.get("candidate_required_location", "Remote")
                sal = item.get("salary", "")
                date_found = item.get("publication_date", "") or datetime.now(timezone.utc).isoformat()

                jobs.append(Job(
                    title=title,
                    company=company,
                    location=location or "Remote",
                    description=clean_desc,
                    apply_url=url,
                    source=self.name,
                    date_found=date_found,
                ))

        logger.info(f"Remotive: found {len(jobs)} relevant jobs")
        return jobs
