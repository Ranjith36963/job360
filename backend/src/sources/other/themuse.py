import logging
import re
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource, _is_uk_or_remote

logger = logging.getLogger("job360.sources.themuse")

_HTML_TAG_RE = re.compile(r"<[^>]+>")

MAX_PAGES = 5


class TheMuseSource(BaseJobSource):
    name = "themuse"
    category = "other"

    async def fetch_jobs(self) -> list[Job]:
        jobs = []
        seen_keys = set()

        for page in range(MAX_PAGES):
            params = {
                "page": page,
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

                locations = item.get("locations", [])
                location = ", ".join(
                    loc.get("name", "") for loc in locations if isinstance(loc, dict)
                ) if locations else ""

                if not _is_uk_or_remote(location):
                    continue

                refs = item.get("refs", {})
                apply_url = refs.get("landing_page", "") if isinstance(refs, dict) else ""
                now_iso = datetime.now(timezone.utc).isoformat()
                raw_pub = item.get("publication_date")
                posted_at = raw_pub if raw_pub else None
                confidence = "high" if raw_pub else "low"

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
                    date_found=now_iso,
                    posted_at=posted_at,
                    date_confidence=confidence,
                    date_posted_raw=raw_pub,
                    experience_level=experience_level,
                ))

        logger.info("TheMuse: found %s relevant jobs", len(jobs))
        return jobs
