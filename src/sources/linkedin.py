import re
import asyncio
import logging
from datetime import datetime, timezone

from src.models import Job
from src.sources.base import BaseJobSource
from src.config.keywords import RELEVANCE_KEYWORDS

logger = logging.getLogger("job360.sources.linkedin")

_LINKEDIN_QUERIES = [
    "AI engineer",
    "machine learning engineer",
    "data scientist AI",
    "NLP engineer",
    "MLOps engineer",
]

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

    async def fetch_jobs(self) -> list[Job]:
        jobs = []
        seen_urls = set()
        for query in _LINKEDIN_QUERIES:
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
                text = title.lower()
                if not any(kw in text for kw in RELEVANCE_KEYWORDS):
                    continue
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
            await asyncio.sleep(3)
            if len(jobs) >= 50:
                break
        logger.info(f"LinkedIn: found {len(jobs)} relevant jobs")
        return jobs
