import logging
from datetime import datetime, timezone
from typing import Any, Optional, cast

import aiohttp

from src.models import Job
from src.services.profile.models import SearchConfig
from src.sources.base import BaseJobSource, _is_uk_or_remote
from src.utils.dates import normalize_posted_at

logger = logging.getLogger("job360.sources.adzuna")

class AdzunaSource(BaseJobSource):
    name = "adzuna"
    category = "keyed_api"

    def __init__(self, session: aiohttp.ClientSession, app_id: str = "", app_key: str = "", search_config: Optional[SearchConfig] = None):
        super().__init__(session, search_config=search_config)
        self._app_id = app_id
        self._app_key = app_key

    @property
    def is_configured(self) -> bool:
        return bool(self._app_id and self._app_key)

    async def fetch_jobs(self) -> list[Job]:
        if not self.is_configured:
            logger.warning("Adzuna: no API keys, skipping")
            return []
        jobs = []
        queries = self.search_titles[:8]
        for query in queries:
            params = {
                "app_id": self._app_id,
                "app_key": self._app_key,
                "what": query,
                "results_per_page": 50,
                "max_days_old": 14,
                "content-type": "application/json",
            }
            data = await self._get_json(
                "https://api.adzuna.com/v1/api/jobs/gb/search/1",
                params=params,
            )
            if not data or "results" not in data:
                continue
            for item in cast(dict[str, Any], data)["results"]:
                company = item.get("company", {})
                location = item.get("location", {})
                now_iso = datetime.now(timezone.utc).isoformat()
                raw_created = item.get("created")
                posted_at, confidence = normalize_posted_at(raw_created)

                # `salary_is_predicted` ("0"/"1", both seen live 2026-08-16)
                # is Adzuna's own ML-guessed-vs-advertised flag — until now it
                # was read and thrown away, so a guess was stored identically
                # to a real advertised figure. Absent entirely -> unknown
                # (None), never a guessed False (rule #29).
                raw_predicted = item.get("salary_is_predicted")
                salary_is_estimated = None if raw_predicted is None else str(raw_predicted) == "1"

                # `contract_time` (full_time/part_time) and `contract_type`
                # (permanent/contract) are two different axes of the SAME
                # employment_type shelf, and Job has one field to put a raw
                # value on. contract_type is the more specific of the two
                # when both are present (permanent/contract beats
                # full_time/part_time for a "hard constraint" filter), so it
                # wins; contract_time is the fallback when Adzuna only sends
                # that axis. Confirmed live 2026-08-16: both values seen,
                # never both null on the same job in the sample.
                contract_time = item.get("contract_time")
                contract_type = item.get("contract_type")
                employment_type = contract_type or contract_time

                category = item.get("category")
                category_label = category.get("label") if isinstance(category, dict) else None

                jobs.append(Job(
                    title=item.get("title", ""),
                    company=company.get("display_name", "") if isinstance(company, dict) else str(company),
                    location=location.get("display_name", "") if isinstance(location, dict) else str(location),
                    salary_min=item.get("salary_min"),
                    salary_max=item.get("salary_max"),
                    salary_is_estimated=salary_is_estimated,
                    description=item.get("description", ""),
                    apply_url=item.get("redirect_url", ""),
                    source=self.name,
                    date_found=now_iso,
                    posted_at=posted_at,
                    date_confidence=confidence,
                    date_posted_raw=raw_created,
                    employment_type=employment_type,
                    category=category_label,
                ))
        jobs = [j for j in jobs if _is_uk_or_remote(j.location)]
        logger.info("Adzuna: found %s jobs", len(jobs))
        return jobs
