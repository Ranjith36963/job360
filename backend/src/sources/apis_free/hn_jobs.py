import asyncio
import logging
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource, _is_uk_or_remote

logger = logging.getLogger("job360.sources.hn_jobs")


class HNJobsSource(BaseJobSource):
    """YC Startup Jobs via Firebase HN API (job stories, NOT 'Who is Hiring')."""
    name = "hn_jobs"
    category = "free_json"

    async def fetch_jobs(self) -> list[Job]:
        # Get list of job story IDs
        ids = await self._get_json(
            "https://hacker-news.firebaseio.com/v0/jobstories.json"
        )
        if not ids or not isinstance(ids, list):
            return []

        # Fetch items concurrently in batches of 20
        jobs = []
        for i in range(0, min(len(ids), 200), 20):
            batch = ids[i:i + 20]
            tasks = [
                self._get_json(f"https://hacker-news.firebaseio.com/v0/item/{item_id}.json")
                for item_id in batch
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for item in results:
                if isinstance(item, Exception) or not item:
                    continue
                job = self._parse_item(item)
                if job:
                    jobs.append(job)

        logger.info("HN Jobs: found %s relevant jobs", len(jobs))
        return jobs

    def _parse_item(self, item: dict) -> Job | None:
        title = item.get("title", "")
        url = item.get("url", "")
        text = item.get("text", "")
        check_text = f"{title} {text}".lower()

        # Extract company from title (format: "Company is hiring ..." or "Company (YC ...)")
        company = "Unknown"
        for sep in [" is hiring", " (YC", " Is Hiring", " - "]:
            if sep in title:
                company = title.split(sep)[0].strip()
                break

        # Check UK/remote
        location_text = f"{title} {url} {text}"
        if not _is_uk_or_remote(location_text):
            return None

        ts = item.get("time", 0)
        date_found = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else datetime.now(timezone.utc).isoformat()

        return Job(
            title=title,
            company=company,
            location="",
            description=text[:5000] if text else title,
            apply_url=url or f"https://news.ycombinator.com/item?id={item.get('id', '')}",
            source=self.name,
            date_found=date_found,
        )
