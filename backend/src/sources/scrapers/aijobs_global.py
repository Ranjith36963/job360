"""AI Jobs Worldwide — ai-jobs.global — QUARANTINED (board abandoned).

NOTE: Probed 2026-06-11. The WordPress site is HTTP-alive but content-dead:
every ``job_listing`` post on the search page carries ``status-expired``
and the newest item in the WP Job Manager RSS feed (``/jobs/feed/``) is
from October 2023. The WP suggest endpoint answers ``([])`` — a
paren-wrapped JSONP empty array — for every term, which made
``json.loads`` raise "Expecting value: line 1 column 1" and burn three
retries per query at WARNING level, followed by up to six HTML fallback
fetches.

``fetch_jobs`` therefore makes a single un-retried canary request to the
suggest endpoint with the user's top query. While the board stays empty
it logs one INFO line and returns []. If the canary ever yields a
non-empty array (parens stripped), the items are parsed and the
remaining queries resume, HTML fallback included. Candidate for full
removal in the next source-rotation batch.
"""
import asyncio
import json
import logging
import re
from datetime import datetime, timezone

import aiohttp

from src.core.settings import REQUEST_TIMEOUT
from src.models import Job
from src.sources.base import BaseJobSource, _is_uk_or_remote

logger = logging.getLogger("job360.sources.aijobs_global")

_AJAX_URL = "https://ai-jobs.global/wp-admin/admin-ajax.php"


def _parse_jsonp_array(text: str) -> list | None:
    """Parse the suggest endpoint's paren-wrapped JSON array.

    Returns the list (possibly empty) or None when the body is not a
    JSONP/JSON array at all.
    """
    stripped = text.strip()
    if stripped.startswith("(") and stripped.endswith(")"):
        stripped = stripped[1:-1]
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, list) else None


class AIJobsGlobalSource(BaseJobSource):
    """AI Jobs Worldwide — ai-jobs.global (WordPress + WP Job Manager)."""
    name = "aijobs_global"
    category = "scraper"
    DOMAINS = {"tech"}

    async def _probe_canary(self, term: str) -> list | None:
        """Single un-retried request to the suggest endpoint.

        Returns a non-empty item list only if the board has revived;
        None for empty/garbage/error responses. Bypasses the base retry
        helpers on purpose: the empty JSONP answer is deterministic, so
        retries only burn time and log warnings.
        """
        await self._rate_limiter.acquire()
        try:
            async with self._session.get(
                _AJAX_URL,
                params={"action": "workscout_incremental_jobs_suggest", "term": term},
                headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            ) as resp:
                if resp.status != 200:
                    return None
                items = _parse_jsonp_array(await resp.text())
                return items if items else None
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return None
        finally:
            self._rate_limiter.release()

    async def fetch_jobs(self) -> list[Job]:
        jobs: list[Job] = []
        seen_urls: set[str] = set()

        queries = self.search_queries[:6]  # Bounded to prevent source timeout
        if not queries:
            logger.info("AI Jobs Global: no search queries in profile, skipping")
            return []

        # Canary: probe the top query once, without retries. The board is
        # abandoned upstream (all listings expired, suggest endpoint empty
        # for every term) — see module docs.
        canary_items = await self._probe_canary(queries[0])
        if canary_items is None:
            logger.info(
                "AI Jobs Global: board still abandoned upstream (suggest "
                "empty/non-JSON) — quarantined, 0 jobs",
            )
            return []

        for item in canary_items:
            job = self._parse_ajax_item(item)
            if job and job.apply_url not in seen_urls:
                seen_urls.add(job.apply_url)
                jobs.append(job)

        for query in queries[1:]:
            # WP Job Manager AJAX endpoint (JSONP-wrapped array)
            text = await self._get_text(
                _AJAX_URL,
                params={
                    "action": "workscout_incremental_jobs_suggest",
                    "term": query,
                },
            )
            data = _parse_jsonp_array(text) if text else None

            if data:
                for item in data:
                    job = self._parse_ajax_item(item)
                    if job and job.apply_url not in seen_urls:
                        seen_urls.add(job.apply_url)
                        jobs.append(job)
                continue

            # Fallback: try HTML with search param
            html = await self._get_text(
                "https://ai-jobs.global/",
                params={"s": query, "post_type": "job_listing"},
            )
            if html:
                for job in self._parse_html(html):
                    if job.apply_url not in seen_urls:
                        seen_urls.add(job.apply_url)
                        jobs.append(job)

        logger.info("AI Jobs Global: found %s relevant jobs", len(jobs))
        return jobs

    def _parse_ajax_item(self, item: dict) -> Job | None:
        now = datetime.now(timezone.utc).isoformat()

        title = item.get("label", "") or item.get("value", "") or item.get("title", "")
        if not title:
            return None

        location = item.get("location", "") or ""
        if not _is_uk_or_remote(location):
            return None

        apply_url = item.get("url", "") or item.get("link", "") or ""
        company = item.get("company", "") or "Unknown"

        return Job(
            title=title,
            company=company,
            location=location or "",
            description=title,
            apply_url=apply_url,
            source=self.name,
            date_found=now,
            posted_at=None,
            date_confidence="low",
            date_posted_raw=None,
        )

    def _parse_html(self, html: str) -> list[Job]:
        """Fallback HTML parsing for WP Job Manager listings."""
        try:
            jobs = []
            now = datetime.now(timezone.utc).isoformat()

            # WP Job Manager uses .job_listing class
            link_pattern = re.compile(
                r'<a[^>]+href="(https://ai-jobs\.global/job[s]?/[^"]+)"[^>]*>\s*([^<]+?)\s*</a>',
                re.IGNORECASE,
            )

            for match in link_pattern.finditer(html):
                url, title = match.group(1), match.group(2).strip()

                if len(title) < 5:
                    continue

                jobs.append(Job(
                    title=title,
                    company="Unknown",
                    location="",
                    description=title,
                    apply_url=url,
                    source=self.name,
                    date_found=now,
                    posted_at=None,
                    date_confidence="low",
                    date_posted_raw=None,
                ))

            return jobs
        except Exception as e:
            logger.warning("AI Jobs Global: HTML parsing failed: %s", e)
            return []
