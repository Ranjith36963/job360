import json
import logging
import re
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource

logger = logging.getLogger("job360.sources.jobtensor")

# Extract the context JSON from the page to get AJAX search params
_CONTEXT_RE = re.compile(r'var\s+context\s*=\s*(\{.*?\});\s*</script>', re.DOTALL)


class JobTensorSource(BaseJobSource):
    """JobTensor — UK AI/Science/Tech jobs via AJAX API."""
    name = "jobtensor"

    async def fetch_jobs(self) -> list[Job]:
        if not self.relevance_keywords:
            logger.info("JobTensor: no keywords configured, skipping")
            return []

        # HTML search (AJAX API returns 400 — unreliable)
        query = self.search_queries[0] if self.search_queries else self.relevance_keywords[0]
        html = await self._get_text(
            "https://jobtensor.com/search",
            params={"q": query, "country": "uk"},
        )
        if not html:
            return []

        jobs = self._parse_html(html)
        logger.info(f"JobTensor: found {len(jobs)} relevant jobs")
        return jobs

    def _parse_api_results(self, hits: list) -> list[Job]:
        jobs = []
        now = datetime.now(timezone.utc).isoformat()

        for item in hits:
            title = item.get("title", "") or item.get("name", "")
            company = item.get("company", "") or item.get("company_name", "") or "Unknown"
            location = item.get("location", "") or item.get("city", "") or "UK"

            text = f"{title} {company}".lower()
            if not self._relevance_match(text):
                continue

            slug = item.get("slug", "") or item.get("url", "")
            if slug.startswith("http"):
                apply_url = slug
            elif slug:
                apply_url = f"https://jobtensor.com/uk/{slug}"
            else:
                apply_url = ""

            jobs.append(Job(
                title=title,
                company=company,
                location=location,
                description=title,
                apply_url=apply_url,
                source=self.name,
                date_found=now,
            ))

        return jobs

    def _parse_html(self, html: str) -> list[Job]:
        """Fallback HTML parsing."""
        jobs = []
        now = datetime.now(timezone.utc).isoformat()

        # Try to extract embedded JSON context
        ctx_match = _CONTEXT_RE.search(html)
        if ctx_match:
            try:
                ctx = json.loads(ctx_match.group(1))
                hits = ctx.get("results", {}).get("hits", [])
                if hits:
                    return self._parse_api_results(hits)
            except (json.JSONDecodeError, AttributeError):
                pass

        # Last resort: regex on any visible job links
        link_pattern = re.compile(
            r'<a[^>]+href="(/uk/[^"]*?)"[^>]*>([^<]+)</a>',
            re.IGNORECASE
        )

        for match in link_pattern.finditer(html):
            path, title = match.group(1), match.group(2).strip()
            text = title.lower()
            if not self._relevance_match(text):
                continue

            apply_url = f"https://jobtensor.com{path}"
            jobs.append(Job(
                title=title,
                company="Unknown",
                location="UK",
                description=title,
                apply_url=apply_url,
                source=self.name,
                date_found=now,
            ))

        return jobs
