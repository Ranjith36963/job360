import asyncio
import logging
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource, _is_uk_or_remote

logger = logging.getLogger("job360.sources.jooble")


class JoobleSource(BaseJobSource):
    name = "jooble"
    category = "keyed_api"

    def __init__(self, session: aiohttp.ClientSession, api_key: str = "", search_config=None):
        super().__init__(session, search_config=search_config)
        self._api_key = api_key

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    async def fetch_jobs(self) -> list[Job]:
        if not self.is_configured:
            logger.warning("Jooble: no API key, skipping")
            return []
        jobs = []
        seen_ids = set()
        queries = self.job_titles[:8]
        for query in queries:
            body = {
                "keywords": query,
                "location": "United Kingdom",
                "page": "1",
            }
            data = await self._post_json(
                f"https://jooble.org/api/{self._api_key}",
                body=body,
            )
            if not data or "jobs" not in data:
                continue
            for item in data["jobs"]:
                job_id = item.get("id", "")
                if job_id in seen_ids:
                    continue
                seen_ids.add(job_id)
                # Jooble's "updated" is a mutation date, not a posting date —
                # using it as posted_at would fabricate freshness. Keep it in
                # date_posted_raw for audit; posted_at=None + confidence=low.
                now_iso = datetime.now(timezone.utc).isoformat()
                raw_updated = item.get("updated")
                salary_text = item.get("salary", "")
                salary_min = None
                salary_max = None
                if salary_text and "-" in str(salary_text):
                    parts = str(salary_text).replace(",", "").replace("£", "").replace("$", "").split("-")
                    try:
                        salary_min = float("".join(c for c in parts[0] if c.isdigit() or c == "."))
                        salary_max = float("".join(c for c in parts[1] if c.isdigit() or c == "."))
                    except (ValueError, IndexError):
                        pass
                jobs.append(Job(
                    title=item.get("title", ""),
                    company=item.get("company", ""),
                    location=item.get("location", ""),
                    description=item.get("snippet", ""),
                    apply_url=item.get("link", ""),
                    source=self.name,
                    date_found=now_iso,
                    posted_at=None,
                    date_confidence="low",
                    date_posted_raw=raw_updated,
                    salary_min=salary_min,
                    salary_max=salary_max,
                ))
            await asyncio.sleep(1)
        jobs = [j for j in jobs if _is_uk_or_remote(j.location)]
        logger.info("Jooble: found %s jobs", len(jobs))
        return jobs
