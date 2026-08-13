import logging
from datetime import datetime, timezone
from typing import Any, Optional, cast

import aiohttp

from src.models import Job
from src.services.profile.models import SearchConfig
from src.sources.base import BaseJobSource, _is_uk_or_remote
from src.utils.dates import normalize_posted_at

logger = logging.getLogger("job360.sources.careerjet")

class CareerjetSource(BaseJobSource):
    name = "careerjet"
    category = "keyed_api"

    def __init__(self, session: aiohttp.ClientSession, affid: str = "", search_config: Optional[SearchConfig] = None):
        super().__init__(session, search_config=search_config)
        self._affid = affid

    @property
    def is_configured(self) -> bool:
        return bool(self._affid)

    async def fetch_jobs(self) -> list[Job]:
        if not self.is_configured:
            logger.warning("Careerjet: no CAREERJET_AFFID, skipping")
            return []

        jobs = []
        seen_urls = set()

        for query in self.search_titles[:6]:
            params = {
                "keywords": query,
                "location": "United Kingdom",
                "affid": self._affid,
                # The affiliate endpoint identifies the CALLER, not the end
                # user: omitting these makes it treat the request as
                # unattributed traffic. Static values are correct here — this
                # is a server-side crawl, not a browser session.
                "user_ip": "1.2.3.4",
                "user_agent": "Job360Bot/1.0",
                "locale_code": "en_GB",
                "pagesize": "50",
                "page": "1",
                "sort": "date",
            }
            # Careerjet REQUIRES a Referer (and identifies callers by user_ip /
            # user_agent). Without them it answers HTTP 403 with
            # {"type": "ERROR", "error": "Undeclared referrer. Please add a
            # Referer header so we know who is calling this API and from which
            # page."} — verified live 2026-08-11. The affiliate ID alone is not
            # enough, so this looked exactly like a bad key for as long as the
            # header was missing.
            # Careerjet has TWO APIs and they take different credentials
            # (all verified live 2026-08-11 with a real affiliate ID):
            #
            #   search.api.careerjet.net/v4/query — wants an API KEY via HTTP
            #     Basic Auth AND an allow-listed source IP. With an affiliate
            #     ID it answers 401 "You did not provide an API key", and with
            #     Basic Auth it answers 403 "Unauthorized access from IP".
            #     This is what the source used to call, so it returned nothing
            #     no matter how valid the affiliate ID was.
            #
            #   public.api.careerjet.net/search — the affiliate endpoint. Takes
            #     `affid` as a query param and REQUIRES a Referer header
            #     (without it: 403 "Undeclared referrer"). Returns 8,600 hits
            #     for "data engineer" in the UK.
            #
            # Must be http:// — the public host does not listen on 443
            # (https attempt = ClientConnectorError, port refused).
            data = await self._get_json(
                "http://public.api.careerjet.net/search",
                params=params,
                headers={
                    "Referer": "https://job360.uk/",
                    "User-Agent": "Job360Bot/1.0 (+https://job360.uk)",
                },
            )
            if not data or "jobs" not in data:
                continue

            for item in cast(dict[str, Any], data)["jobs"]:
                title = item.get("title", "")
                description = item.get("description", "")

                apply_url = item.get("url", "")
                if apply_url in seen_urls:
                    continue
                seen_urls.add(apply_url)

                now_iso = datetime.now(timezone.utc).isoformat()
                raw_date = item.get("date")
                posted_at, confidence = normalize_posted_at(raw_date)

                # Parse salary if available
                salary_min = item.get("salary_min")
                salary_max = item.get("salary_max")
                if salary_min is not None:
                    try:
                        salary_min = float(salary_min)
                    except (ValueError, TypeError):
                        salary_min = None
                if salary_max is not None:
                    try:
                        salary_max = float(salary_max)
                    except (ValueError, TypeError):
                        salary_max = None

                jobs.append(Job(
                    title=title,
                    company=item.get("company", ""),
                    location=item.get("locations", ""),
                    description=description[:5000],
                    apply_url=apply_url,
                    source=self.name,
                    date_found=now_iso,
                    posted_at=posted_at,
                    date_confidence=confidence,
                    date_posted_raw=raw_date,
                    salary_min=salary_min,
                    salary_max=salary_max,
                ))

        jobs = [j for j in jobs if _is_uk_or_remote(j.location)]
        logger.info("Careerjet: found %s relevant jobs", len(jobs))
        return jobs
