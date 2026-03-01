import logging
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource
from src.config.keywords import RELEVANCE_KEYWORDS

logger = logging.getLogger("job360.sources.remotive")


class RemotiveSource(BaseJobSource):
    name = "remotive"

    async def fetch_jobs(self) -> list[Job]:
        jobs = []
        data = await self._get_json(
            "https://remotive.com/api/remote-jobs",
            params={"category": "software-dev", "limit": "100"},
        )
        if not data or "jobs" not in data:
            return []
        for item in data["jobs"]:
            title = item.get("title", "")
            desc = item.get("description", "")
            tags = " ".join(item.get("tags", []))
            text = f"{title} {desc} {tags}".lower()
            if not any(kw in text for kw in RELEVANCE_KEYWORDS):
                continue
            date_found = item.get("publication_date") or datetime.now(timezone.utc).isoformat()
            salary = item.get("salary", "")
            salary_min = None
            salary_max = None
            if salary and "-" in str(salary):
                parts = str(salary).replace(",", "").replace("$", "").replace("£", "").split("-")
                try:
                    salary_min = float(parts[0].strip())
                    salary_max = float(parts[1].strip())
                except (ValueError, IndexError):
                    pass
            jobs.append(Job(
                title=title,
                company=item.get("company_name", ""),
                location=item.get("candidate_required_location", ""),
                description=desc[:5000],
                apply_url=item.get("url", ""),
                source=self.name,
                date_found=date_found,
                salary_min=salary_min,
                salary_max=salary_max,
            ))
        logger.info(f"Remotive: found {len(jobs)} relevant jobs")
        return jobs
