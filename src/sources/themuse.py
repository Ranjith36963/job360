import logging
import re
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource, _is_uk_or_remote

logger = logging.getLogger("job360.sources.themuse")

_HTML_TAG_RE = re.compile(r"<[^>]+>")

CATEGORIES = ["Data Science", "Engineering"]
MAX_PAGES = 5


class TheMuseSource(BaseJobSource):
    name = "themuse"

    async def fetch_jobs(self) -> list[Job]:
        jobs = []
        seen_keys = set()

        for category in CATEGORIES:
            for page in range(MAX_PAGES):
                params = {
                    "page": page,
                    "category": category,
                    "location": "United Kingdom",
                }
                data = await self._get_json(
                    "https://www.themuse.com/api/public/jobs",
                    params=params,
                )
                if not data or "results" not in data:
                    break

                results = data["results"]
                if not results:
                    break

                for item in results:
                    title = item.get("name", "")
                    company_obj = item.get("company", {})
                    company = company_obj.get("name", "") if isinstance(company_obj, dict) else ""
                    contents = item.get("contents", "")
                    description = _HTML_TAG_RE.sub("", contents)[:5000]
                    text = f"{title} {description}".lower()

                    if not any(kw in text for kw in self.relevance_keywords):
                        continue

                    locations = item.get("locations", [])
                    location = ", ".join(
                        loc.get("name", "") for loc in locations if isinstance(loc, dict)
                    ) if locations else ""

                    if not _is_uk_or_remote(location):
                        continue

                    refs = item.get("refs", {})
                    apply_url = refs.get("landing_page", "") if isinstance(refs, dict) else ""
                    date_found = item.get("publication_date") or datetime.now(timezone.utc).isoformat()

                    # Experience level from levels array
                    levels = item.get("levels", [])
                    experience_level = levels[0].get("name", "") if levels and isinstance(levels[0], dict) else ""

                    dedup_key = (company.lower(), title.lower())
                    if dedup_key in seen_keys:
                        continue
                    seen_keys.add(dedup_key)

                    jobs.append(Job(
                        title=title,
                        company=company,
                        location=location,
                        description=description,
                        apply_url=apply_url,
                        source=self.name,
                        date_found=date_found,
                        experience_level=experience_level,
                    ))

        logger.info(f"TheMuse: found {len(jobs)} relevant jobs")
        return jobs
