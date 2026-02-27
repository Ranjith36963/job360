import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource
from src.config.keywords import JOB_TITLES

logger = logging.getLogger("job360.sources.findajob")

# Use RSS search with key queries
FINDAJOB_QUERIES = [
    "AI engineer",
    "machine learning",
    "data scientist",
    "NLP engineer",
    "deep learning",
]


class FindAJobSource(BaseJobSource):
    name = "findajob"

    async def fetch_jobs(self) -> list[Job]:
        jobs = []
        for query in FINDAJOB_QUERIES:
            params = {
                "q": query,
                "w": "united kingdom",
                "d": "20",
            }
            text = await self._get_text(
                "https://findajob.dwp.gov.uk/search.rss",
                params=params,
            )
            if not text:
                continue
            try:
                root = ET.fromstring(text)
                channel = root.find("channel")
                if channel is None:
                    continue
                for item in channel.findall("item"):
                    title = item.findtext("title", "")
                    link = item.findtext("link", "")
                    desc = item.findtext("description", "")
                    jobs.append(Job(
                        title=title,
                        company="",
                        location="UK",
                        description=desc,
                        apply_url=link,
                        source=self.name,
                        date_found=datetime.now(timezone.utc).isoformat(),
                    ))
            except ET.ParseError:
                logger.warning(f"FindAJob: failed to parse RSS for query '{query}'")
                continue
        logger.info(f"FindAJob: found {len(jobs)} jobs")
        return jobs
