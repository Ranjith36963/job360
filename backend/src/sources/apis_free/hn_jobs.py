import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, cast

from src.models import Job
from src.sources.base import BaseJobSource, _is_uk_or_remote

logger = logging.getLogger("job360.sources.hn_jobs")


class HNJobsSource(BaseJobSource):
    """YC Startup Jobs via Firebase HN API (job stories, NOT 'Who is Hiring')."""
    name = "hn_jobs"
    category = "free_json"
    DOMAINS = {"tech"}

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
                job = self._parse_item(cast(dict[str, Any], item))
                if job:
                    jobs.append(job)

        logger.info("HN Jobs: found %s relevant jobs", len(jobs))
        return jobs

    def _parse_item(self, item: dict[str, Any]) -> Job | None:
        title = item.get("title", "")
        url = item.get("url", "")
        text = item.get("text", "")

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

        now_iso = datetime.now(timezone.utc).isoformat()
        ts = item.get("time", 0)
        posted_at = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else None
        # Confidence follows the RESULT, not the input. A present-but-
        # unparseable value used to be stamped "high" purely because the
        # field existed — certifying as trustworthy a date we could not read.
        confidence = "high" if posted_at else "low"
        return Job(
            title=title,
            company=company,
            location="",
            description=text[:5000] if text else title,
            apply_url=url or f"https://news.ycombinator.com/item?id={item.get('id', '')}",
            source=self.name,
            date_found=now_iso,
            posted_at=posted_at,
            date_confidence=confidence,
            date_posted_raw=str(ts) if ts else None,
        )
