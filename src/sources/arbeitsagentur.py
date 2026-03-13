"""Arbeitsagentur — German Federal Employment Agency job board.

Public API with hardcoded key. JSON API.
Covers: all jobs in Germany via the Bundesagentur fur Arbeit.
URL: https://github.com/bundesAPI/jobsuche-api
"""

import logging
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource
from src.filters.skill_matcher import get_search_queries, get_search_locations

logger = logging.getLogger("job360.sources.arbeitsagentur")


class ArbeitsagenturSource(BaseJobSource):
    name = "arbeitsagentur"

    async def fetch_jobs(self) -> list[Job]:
        # Only fetch if user profile mentions Germany
        locations = get_search_locations()
        de_relevant = any(
            loc.lower() in ("germany", "berlin", "munich", "hamburg", "frankfurt",
                            "cologne", "stuttgart", "dusseldorf", "de", "deutschland")
            for loc in locations
        )
        if not de_relevant:
            logger.debug("Arbeitsagentur: no DE locations in profile, skipping")
            return []

        jobs = []
        queries = get_search_queries(limit=3)
        headers = {
            "X-API-Key": "jobboerse-jobsuche",
        }

        for query in queries:
            params = {
                "was": query,
                "size": 50,
                "page": 0,
            }
            data = await self._get_json(
                "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v4/jobs",
                params=params,
                headers=headers,
            )
            if not data:
                continue
            stellenangebote = data.get("stellenangebote", [])
            if not stellenangebote:
                continue
            for item in stellenangebote:
                title = item.get("titel", "")
                company = item.get("arbeitgeber", "")
                location = item.get("arbeitsort", {})
                if isinstance(location, dict):
                    location = location.get("ort", "") or location.get("plz", "Germany")
                ref = item.get("refnr", "")
                url = f"https://www.arbeitsagentur.de/jobsuche/jobdetail/{ref}" if ref else ""
                if not url:
                    continue
                date_found = item.get("eintrittsdatum", "") or item.get("modifikationsTimestamp", "") or datetime.now(timezone.utc).isoformat()
                jobs.append(Job(
                    title=title,
                    company=company if isinstance(company, str) else "",
                    location=location if isinstance(location, str) else "Germany",
                    description="",
                    apply_url=url,
                    source=self.name,
                    date_found=date_found,
                ))
        logger.info(f"Arbeitsagentur: found {len(jobs)} jobs")
        return jobs
