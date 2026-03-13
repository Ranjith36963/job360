import re
import logging
from datetime import datetime, timezone

from src.models import Job
from src.sources.base import BaseJobSource
from src.filters.skill_matcher import get_search_queries

logger = logging.getLogger("job360.sources.findajob")

# Regex to extract job cards from the Find a Job search results HTML
_JOB_LINK_RE = re.compile(
    r'<a[^>]+href="(/job/[^"]+)"[^>]*>\s*([^<]+)</a>',
    re.IGNORECASE,
)
_COMPANY_RE = re.compile(
    r'<li[^>]*class="[^"]*company[^"]*"[^>]*>\s*([^<]+)',
    re.IGNORECASE,
)


class FindAJobSource(BaseJobSource):
    name = "findajob"

    async def fetch_jobs(self) -> list[Job]:
        jobs = []
        seen_urls = set()
        queries = get_search_queries(limit=5)
        for query in queries:
            params = {
                "q": query,
                "w": "united kingdom",
                "d": "20",
            }
            html = await self._get_text(
                "https://findajob.dwp.gov.uk/search",
                params=params,
            )
            if not html:
                continue
            matches = _JOB_LINK_RE.findall(html)
            if not matches:
                logger.debug(f"FindAJob: no job links found for query '{query}'")
                continue
            for path, title in matches:
                url = f"https://findajob.dwp.gov.uk{path}"
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                jobs.append(Job(
                    title=title.strip(),
                    company="",
                    location="UK",
                    description="",
                    apply_url=url,
                    source=self.name,
                    date_found=datetime.now(timezone.utc).isoformat(),
                ))
        logger.info(f"FindAJob: found {len(jobs)} jobs")
        return jobs
