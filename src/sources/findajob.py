import re
import logging
from datetime import datetime, timezone

from src.models import Job
from src.sources.base import BaseJobSource

logger = logging.getLogger("job360.sources.findajob")

FINDAJOB_QUERIES = [
    "AI engineer",
    "machine learning engineer",
    "data scientist",
    "NLP engineer",
    "deep learning",
    "MLOps engineer",
    "computer vision engineer",
    "LLM engineer",
]

# Updated regex patterns for current FindAJob HTML structure
# Job links appear as <a href="/details/12345">Job Title</a> inside <h3> tags
_JOB_LINK_RE = re.compile(
    r'<a[^>]+href="(/details/\d+)"[^>]*>\s*([^<]+?)\s*</a>',
    re.IGNORECASE,
)
# Company names appear in <strong> tags near job cards
_COMPANY_RE = re.compile(
    r'<strong>\s*([^<]+?)\s*</strong>',
    re.IGNORECASE,
)
# Salary patterns
_SALARY_RE = re.compile(
    r'£([\d,]+)\s+to\s+£([\d,]+)\s+per\s+year',
    re.IGNORECASE,
)


class FindAJobSource(BaseJobSource):
    name = "findajob"

    async def fetch_jobs(self) -> list[Job]:
        jobs = []
        seen_urls = set()
        queries = self.search_queries if self.search_queries else FINDAJOB_QUERIES
        for query in queries:
            params = {
                "q": query,
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

                # Try to extract salary from nearby HTML
                salary_min = None
                salary_max = None
                # Find salary near this job link
                idx = html.find(path)
                if idx >= 0:
                    block = html[idx:idx + 1000]
                    sal_match = _SALARY_RE.search(block)
                    if sal_match:
                        try:
                            salary_min = float(sal_match.group(1).replace(",", ""))
                            salary_max = float(sal_match.group(2).replace(",", ""))
                        except ValueError:
                            pass

                jobs.append(Job(
                    title=title.strip(),
                    company="",
                    location="UK",
                    description="",
                    apply_url=url,
                    source=self.name,
                    date_found=datetime.now(timezone.utc).isoformat(),
                    salary_min=salary_min,
                    salary_max=salary_max,
                ))
        logger.info(f"FindAJob: found {len(jobs)} jobs")
        return jobs
