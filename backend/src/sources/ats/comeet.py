"""Comeet ATS public postings — QUARANTINED (upstream token gate).

Endpoint pattern: https://www.comeet.co/careers-api/2.0/company/{slug}/positions

NOTE: As of 2026-06 the careers API rejects every request that lacks a
per-company ``token`` query parameter (HTTP 400 ``{"message": "Token is
missing"}``). The token is issued per company to embed in that company's
own careers site and is not publicly discoverable — Comeet's hosted
careers pages (``comeet.com/jobs/{name}/{uid}``) were also retired, so
there is nothing to scrape it from. All five original slugs were probed
on 2026-06-11: three are gone outright (HTTP 404 HTML shell) and the
remaining two answer only the token error. Riskified and Lightricks have
moved to Greenhouse, with zero UK locations on their boards.

``fetch_jobs`` therefore makes a single un-retried canary request. While
the gate is up it logs one INFO line and returns []. If the gate is ever
lifted (canary returns HTTP 200 with a JSON array) the original per-slug
fetch resumes without further changes. Candidate for full removal in the
next source-rotation batch (see M6 in docs/maintenance/MISSIONS.md).
"""
import asyncio
import json
import logging
from datetime import datetime, timezone

import aiohttp

from src.core.companies import COMEET_COMPANIES, COMPANY_NAME_OVERRIDES
from src.core.settings import REQUEST_TIMEOUT
from src.models import Job
from src.sources.base import BaseJobSource, _is_uk_or_remote

logger = logging.getLogger("job360.sources.comeet")


class ComeetSource(BaseJobSource):
    name = "comeet"
    category = "ats"

    def __init__(
        self,
        session: aiohttp.ClientSession,
        companies: list[str] | None = None,
        search_config=None,
    ):
        super().__init__(session, search_config=search_config)
        self._companies = companies if companies is not None else COMEET_COMPANIES

    async def _probe_canary(self, slug: str) -> list | None:
        """Single un-retried request to one slug.

        Returns the parsed JSON array when the token gate is lifted,
        None while the API keeps rejecting un-tokened requests. Bypasses
        the base retry helpers on purpose: the 400 is deterministic, so
        retries only burn time and log warnings.
        """
        url = f"https://www.comeet.co/careers-api/2.0/company/{slug}/positions"
        await self._rate_limiter.acquire()
        try:
            async with self._session.get(
                url,
                params={"ui_aware": "true"},
                headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json(content_type=None)
                return data if isinstance(data, list) else None
        except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError):
            return None
        finally:
            self._rate_limiter.release()

    async def fetch_jobs(self) -> list[Job]:
        if not self._companies:
            return []

        canary_slug = self._companies[0]
        canary_positions = await self._probe_canary(canary_slug)
        if canary_positions is None:
            logger.info(
                "Comeet: careers API still token-gated upstream — "
                "quarantined, 0 jobs (canary slug %r)",
                canary_slug,
            )
            return []

        # Token gate lifted — resume the original per-slug fetch. The canary
        # already holds the first slug's positions; the rest go through the
        # normal retrying helper.
        results: list[Job] = []
        slug_positions: list[tuple[str, list]] = [(canary_slug, canary_positions)]
        for slug in self._companies[1:]:
            url = f"https://www.comeet.co/careers-api/2.0/company/{slug}/positions"
            data = await self._get_json(url, params={"ui_aware": "true"})
            if not data:
                continue
            # Comeet returns a flat array; some wrappers nest it under "positions"
            positions = data if isinstance(data, list) else data.get("positions", [])
            if not isinstance(positions, list):
                continue
            slug_positions.append((slug, positions))

        for slug, positions in slug_positions:
            company_name = COMPANY_NAME_OVERRIDES.get(
                slug, slug.replace("-", " ").title()
            )
            for item in positions:
                title = item.get("name") or item.get("position_name") or ""
                location = (
                    item.get("location", {}).get("name")
                    if isinstance(item.get("location"), dict)
                    else (item.get("location") or "")
                )
                if not _is_uk_or_remote(location or ""):
                    continue

                raw_updated = item.get("time_updated") or item.get("timeUpdated")
                raw_posted = item.get("time_created") or raw_updated
                posted_at = raw_posted if raw_posted else None
                confidence = "high" if raw_posted else "low"

                now_iso = datetime.now(timezone.utc).isoformat()
                results.append(Job(
                    title=title,
                    company=company_name,
                    location=location or "Remote",
                    description=(item.get("description") or "")[:5000],
                    apply_url=item.get("url_comeet")
                              or item.get("url")
                              or item.get("apply_url")
                              or "",
                    source=self.name,
                    date_found=now_iso,
                    posted_at=posted_at,
                    date_confidence=confidence,
                    date_posted_raw=raw_posted,
                ))

        logger.info(
            "Comeet: found %s relevant jobs across %s companies",
            len(results),
            len(self._companies),
        )
        return results
