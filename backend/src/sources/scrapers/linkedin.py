import re
import asyncio
import logging
from datetime import datetime, timezone

from src.models import Job
from src.sources.base import BaseJobSource, _is_uk_or_remote

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


class LinkedInSource(BaseJobSource):
    name = "linkedin"
    category = "scraper"

    async def fetch_jobs(self) -> list[Job]:
        jobs = []
        seen_urls = set()
        queries = self.search_queries[:5]
        if not queries:
            logger.info("LinkedIn: no search queries in profile, skipping")
            return []
        for query in queries:
            params = {
                "keywords": query,
                "location": "United Kingdom",
                "f_TPR": "r604800",
                "start": "0",
            }
            html = await self._get_text(_BASE_URL, params=params)
            if not html:
                await asyncio.sleep(3)
                continue
            try:
                titles = _TITLE_RE.findall(html)
                companies = _COMPANY_RE.findall(html)
                locations = _LOCATION_RE.findall(html)
                links = _LINK_RE.findall(html)
                count = min(len(titles), len(links))
                for i in range(count):
                    url = links[i].split("?")[0]
                    if url in seen_urls:
                        continue
                    seen_urls.add(url)
                    title = titles[i].strip()
                    company = companies[i].strip() if i < len(companies) else ""
                    location = locations[i].strip() if i < len(locations) else "UK"
                    jobs.append(Job(
                        title=title,
                        company=company,
                        location=location,
                        description="",
                        apply_url=url,
                        source=self.name,
                        date_found=datetime.now(timezone.utc).isoformat(),
                    ))
                    if len(jobs) >= 50:
                        break
            except Exception as e:
                logger.warning("LinkedIn: HTML parsing failed for query '%s': %s", query, e)
            await asyncio.sleep(3)
            if len(jobs) >= 50:
                break
        jobs = [j for j in jobs if _is_uk_or_remote(j.location)]
        logger.info("LinkedIn: found %s relevant jobs", len(jobs))
        return jobs
