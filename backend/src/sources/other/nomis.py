import logging
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource

logger = logging.getLogger("job360.sources.nomis")


class NomisSource(BaseJobSource):
    """Nomis — UK Government vacancy statistics (market intelligence).

    This source provides regional vacancy trend data rather than individual
    job listings. It creates summary "jobs" that link to the Nomis portal
    for regional vacancy analysis.
    """
    name = "nomis"
    category = "other"

    async def fetch_jobs(self) -> list[Job]:
        data = await self._get_json(
            "https://www.nomisweb.co.uk/api/v01/dataset/NM_1_1.data.json",
            params={
                "geography": "2092957697",  # UK
                "variable": "18",  # Vacancies
                "measures": "20100",
                "select": "date_name,obs_value",
                "time": "latest",
            },
        )
        if not data:
            return []

        jobs = []
        now = datetime.now(timezone.utc).isoformat()

        obs = data.get("obs", [])
        if not obs:
            return []

        for record in obs[:5]:
            period = record.get("date", {}).get("value", "")
            value = record.get("obs_value", {}).get("value", "")
            date_label = record.get("date", {}).get("label", period)

            if not value:
                continue

            title = f"UK Vacancy Trends: {date_label} ({value}k vacancies)"
            jobs.append(Job(
                title=title,
                company="ONS / Nomis",
                location="UK",
                description=f"UK labour market vacancy statistics for {date_label}. Total vacancies: {value}k.",
                apply_url="https://www.nomisweb.co.uk/reports/lmp/gor/contents.aspx",
                source=self.name,
                date_found=now,
                posted_at=None,
                date_confidence="low",
                date_posted_raw=None,
            ))

        logger.info("Nomis: found %s vacancy records", len(jobs))
        return jobs
