import asyncio
import html as _html
import json
import logging
import re
from datetime import datetime, timezone

from src.models import Job
from src.sources.base import BaseJobSource, _is_uk_or_remote

logger = logging.getLogger("job360.sources.linkedin")

_BASE_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"

# Regex patterns for LinkedIn guest HTML fragments
_JOB_CARD_RE = re.compile(
    r'<li[^>]*>.*?</li>',
    re.DOTALL,
)
_TITLE_RE = re.compile(r'<h3[^>]*class="[^"]*base-search-card__title[^"]*"[^>]*>\s*([^<]+)', re.IGNORECASE)
_COMPANY_RE = re.compile(r'<h4[^>]*class="[^"]*base-search-card__subtitle[^"]*"[^>]*>\s*([^<]+)', re.IGNORECASE)
_LOCATION_RE = re.compile(r'<span[^>]*class="[^"]*job-search-card__location[^"]*"[^>]*>\s*([^<]+)', re.IGNORECASE)
_LINK_RE = re.compile(r'href="(https://[^"]*linkedin\.com/jobs/view/[^"]*)"', re.IGNORECASE)

# S4: structural health check. `base-search-card__title` is the class the
# title regex keys on — it MUST appear on any non-trivial LinkedIn guest
# search response (results or not: LinkedIn's "no results" shell is tiny).
# If the response is big but this marker is gone, the guest HTML layout
# changed and the regexes above are silently matching nothing.
_STRUCTURE_ANCHOR = "base-search-card__title"
_MIN_STRUCTURAL_HTML_LEN = 500

# Job-understanding fix (2026-08-06): the job-view pages serve the FULL
# description to guests via JSON-LD SEO markup (verified live: 8,930 chars on
# a real posting with a plain browser UA — the assumed auth-wall does not
# apply to the SEO payload). One extra fetch per kept job, capped.
_LDJSON_RE = re.compile(
    r'<script type="application/ld\+json">(.*?)</script>', re.DOTALL
)
_TAG_RE = re.compile(r"<[^>]+>")
_MAX_DETAIL_FETCHES = 30


class LinkedInSource(BaseJobSource):
    name = "linkedin"
    category = "scrapers"

    async def fetch_jobs(self) -> list[Job]:
        jobs = []
        seen_urls = set()
        queries = self.search_queries[:5]
        if not queries:
            logger.info("LinkedIn: no search queries in profile, skipping")
            return []
        for query in queries:
            params = {
                "keywords": query,
                "location": "United Kingdom",
                "f_TPR": "r604800",
                "start": "0",
            }
            html = await self._get_text(_BASE_URL, params=params)
            if not html:
                await asyncio.sleep(3)
                continue
            if len(html) > _MIN_STRUCTURAL_HTML_LEN and _STRUCTURE_ANCHOR not in html:
                logger.error(
                    "[linkedin] STRUCTURE CHANGED: expected '%s' not found in a "
                    "%d-byte response — parser may be broken",
                    _STRUCTURE_ANCHOR, len(html),
                )
            try:
                titles = _TITLE_RE.findall(html)
                companies = _COMPANY_RE.findall(html)
                locations = _LOCATION_RE.findall(html)
                links = _LINK_RE.findall(html)
                count = min(len(titles), len(links))
                for i in range(count):
                    url = links[i].split("?")[0]
                    if url in seen_urls:
                        continue
                    seen_urls.add(url)
                    title = titles[i].strip()
                    company = companies[i].strip() if i < len(companies) else ""
                    location = locations[i].strip() if i < len(locations) else "UK"
                    jobs.append(Job(
                        title=title,
                        company=company,
                        location=location,
                        description="",
                        apply_url=url,
                        source=self.name,
                        date_found=datetime.now(timezone.utc).isoformat(),
                        posted_at=None,
                        date_confidence="fabricated",
                        date_posted_raw=None,
                    ))
                    if len(jobs) >= 50:
                        break
            except Exception as e:
                logger.warning("LinkedIn: HTML parsing failed for query '%s': %s", query, e)
            await asyncio.sleep(3)
            if len(jobs) >= 50:
                break
        jobs = [j for j in jobs if _is_uk_or_remote(j.location)]
        # Detail pass — only for jobs we KEEP, capped, graceful per-job degrade.
        for job in jobs[:_MAX_DETAIL_FETCHES]:
            try:
                job.description = await self._fetch_description(job.apply_url)
            except Exception as e:  # noqa: BLE001 — a detail miss never drops the job
                logger.debug("LinkedIn: detail fetch failed for %s: %s", job.apply_url, e)
            await asyncio.sleep(1)
        logger.info("LinkedIn: found %s relevant jobs", len(jobs))
        return jobs

    async def _fetch_description(self, view_url: str) -> str:
        """Pull the posting's full description from the job-view page's JSON-LD.

        The SEO markup is guest-accessible (verified live 2026-08-06). The
        `description` field arrives as entity-escaped HTML — unescape, strip
        tags, cap 5,000 chars. Returns "" on any miss: absence of text is a
        data gap the scorer treats neutrally, never an error.
        """
        page = await self._get_text(view_url)
        if not page:
            return ""
        m = _LDJSON_RE.search(page)
        if not m:
            return ""
        try:
            data = json.loads(m.group(1))
        except (ValueError, TypeError):
            return ""
        desc = data.get("description") or ""
        if not isinstance(desc, str) or not desc:
            return ""
        return _TAG_RE.sub(" ", _html.unescape(desc))[:5000].strip()
