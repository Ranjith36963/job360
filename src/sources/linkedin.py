import re
import asyncio
import logging
from datetime import datetime, timezone

from src.models import Job
from src.sources.base import BaseJobSource

logger = logging.getLogger("job360.sources.linkedin")

_BASE_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"

# Regex patterns for LinkedIn guest HTML fragments
_JOB_CARD_RE = re.compile(
    r'<li[^>]*>.*?</li>',
    re.DOTALL,
)
_TITLE_RE = re.compile(r'<h3[^>]*class="[^"]*base-search-card__title[^"]*"[^>]*>\s*([^<]+)', re.IGNORECASE)
_COMPANY_RE = re.compile(r'<h4[^>]*class="[^"]*base-search-card__subtitle[^"]*"[^>]*>\s*([^<]+)', re.IGNORECASE)
_LOCATION_RE = re.compile(r'<span[^>]*class="[^"]*job-search-card__location[^"]*"[^>]*>\s*([^<]+)', re.IGNORECASE)
_LINK_RE = re.compile(r'href="(https://[^"]*linkedin\.com/jobs/view/[^"]*)"', re.IGNORECASE)
_TIME_RE = re.compile(r'<time[^>]*datetime="([^"]+)"', re.IGNORECASE)


class LinkedInSource(BaseJobSource):
    name = "linkedin"

    async def fetch_jobs(self) -> list[Job]:
        jobs = []
        seen_urls = set()
        if not self.search_queries:
            logger.info("LinkedIn: no search queries configured, skipping")
            return []
        queries = self.search_queries[:5]
        for query in queries:
            params = {
                "keywords": query,
                "location": "United Kingdom",
                "f_TPR": "r604800",
                "start": "0",
            }
            html = await self._get_text(_BASE_URL, params=params)
            if not html:
                await asyncio.sleep(1.5)
                continue
            titles = _TITLE_RE.findall(html)
            companies = _COMPANY_RE.findall(html)
            locations = _LOCATION_RE.findall(html)
            links = _LINK_RE.findall(html)
            datetimes = _TIME_RE.findall(html)
            count = min(len(titles), len(links))
            now = datetime.now(timezone.utc).isoformat()
            for i in range(count):
                url = links[i].split("?")[0]
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                title = titles[i].strip()
                text = title.lower()
                if not self._relevance_match(text):
                    continue
                company = companies[i].strip() if i < len(companies) else ""
                location = locations[i].strip() if i < len(locations) else "UK"
                date_found = datetimes[i] if i < len(datetimes) else now
                jobs.append(Job(
                    title=title,
                    company=company,
                    location=location,
                    description="",
                    apply_url=url,
                    source=self.name,
                    date_found=date_found,
                ))
                if len(jobs) >= 50:
                    break
            await asyncio.sleep(1.5)
            if len(jobs) >= 50:
                break
        logger.info(f"LinkedIn: found {len(jobs)} relevant jobs")
        return jobs
