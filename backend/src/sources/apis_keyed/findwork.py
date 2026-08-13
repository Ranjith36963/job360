import logging
from datetime import datetime, timezone
from typing import Any, Optional, cast

import aiohttp

from src.models import Job
from src.services.profile.models import SearchConfig
from src.sources.base import BaseJobSource, _is_uk_or_remote
from src.utils.dates import normalize_posted_at

logger = logging.getLogger("job360.sources.findwork")

class FindworkSource(BaseJobSource):
    name = "findwork"
    category = "keyed_api"

    def __init__(self, session: aiohttp.ClientSession, api_key: str = "", search_config: Optional[SearchConfig] = None):
        super().__init__(session, search_config=search_config)
        self._api_key = api_key

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    async def fetch_jobs(self) -> list[Job]:
        if not self.is_configured:
            logger.warning("Findwork: no FINDWORK_API_KEY, skipping")
            return []

        jobs = []
        headers = {"Authorization": f"Token {self._api_key}"}
        # `search_titles`, not `search_queries`: this source loops its own
        # ("uk", "london") location terms below, so the " UK" the query strings
        # carry would be the word "uk" twice in one request.
        search_term = (
            self.search_titles[0] if self.search_titles
            else "software engineer"
        )
        # Findwork's location filter does NOT understand "united kingdom" —
        # measured live 2026-08-11 with a valid token: "united kingdom" -> 0
        # results, "uk" -> 10, "london" -> 12, no location -> 1,207. The source
        # therefore returned nothing for months while the API, the token and
        # the parser were all perfectly healthy.
        # Query the terms it DOES recognise and dedupe; `_is_uk_or_remote`
        # below still gates each result, so a loose term cannot leak non-UK
        # jobs into the catalog.
        results: list[dict[str, Any]] = []
        seen_ids: set[Any] = set()
        for loc in ("uk", "london"):
            data = await self._get_json(
                "https://findwork.dev/api/jobs/",
                params={"search": search_term, "location": loc},
                headers=headers,
            )
            if not data or "results" not in data:
                continue
            for item in cast(dict[str, Any], data)["results"]:
                key = item.get("id") or item.get("url")
                if key in seen_ids:
                    continue
                seen_ids.add(key)
                results.append(item)

        if not results:
            return []

        for item in results:
            # `or ""` on EVERY string field, never a .get() default: Findwork
            # sends explicit nulls ("role": null, "location": null, "text":
            # null) and dict.get's default only applies when the KEY IS ABSENT,
            # not when its value is None. Those Nones reached Job() and raised
            # TypeError inside html.unescape(self.title). All latent until the
            # location-filter fix above started returning these rows at all.
            title = item.get("role") or ""
            description = item.get("text") or ""
            location = item.get("location") or ""

            # A posting with no title is unusable downstream (it also becomes
            # part of the dedup key), so drop it rather than store a blank.
            if not title:
                continue

            if not _is_uk_or_remote(location):
                continue

            now_iso = datetime.now(timezone.utc).isoformat()
            raw_posted = item.get("date_posted")
            posted_at, confidence = normalize_posted_at(raw_posted)

            apply_url = item.get("url") or ""

            jobs.append(Job(
                title=title,
                company=item.get("company_name") or "",
                location=location,
                description=description[:5000],
                apply_url=apply_url,
                source=self.name,
                date_found=now_iso,
                posted_at=posted_at,
                date_confidence=confidence,
                date_posted_raw=raw_posted,
            ))

        logger.info("Findwork: found %s relevant jobs", len(jobs))
        return jobs
