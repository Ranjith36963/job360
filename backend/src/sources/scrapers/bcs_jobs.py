import logging
import re
from datetime import datetime, timezone

from src.models import Job
from src.sources.base import BaseJobSource, _is_uk_or_remote

logger = logging.getLogger("job360.sources.bcs_jobs")

# Broader link patterns for BCS job board
_JOB_LINK_RE = re.compile(
    r'<a[^>]+href="([^"]*(?:job|vacanc|career|position|opportunity)[^"]*)"[^>]*>\s*([^<]{5,}?)\s*</a>',
    re.IGNORECASE,
)

# S4: structural health check. A real BCS jobs-board page is a substantial
# listing document; if it comes back non-trivially large but the anchor
# regex above matches ZERO raw `<a href="...job...">` candidates (before any
# nav-link filtering), the page markup changed and the parser is silently
# blind rather than looking at a genuine zero-results day.
_MIN_STRUCTURAL_HTML_LEN = 2000


class BCSJobsSource(BaseJobSource):
    """BCS (Chartered Institute for IT) Job Board — UK IT professional jobs."""
    name = "bcs_jobs"
    category = "scrapers"
    DOMAINS = {"tech"}

    async def fetch_jobs(self) -> list[Job]:
        # Try multiple URL patterns
        urls = [
            "https://www.bcs.org/jobs-board",
            "https://www.bcs.org/jobs",
        ]

        html = None
        for url in urls:
            html = await self._get_text(url)
            if html:
                break

        if not html:
            return []

        jobs = self._parse_html(html)
        jobs = [j for j in jobs if _is_uk_or_remote(j.location)]
        logger.info("BCS Jobs: found %s relevant jobs", len(jobs))
        return jobs

    def _parse_html(self, html: str) -> list[Job]:
        try:
            jobs = []
            now = datetime.now(timezone.utc).isoformat()
            seen_urls = set()
            raw_matches = 0

            for match in _JOB_LINK_RE.finditer(html):
                raw_matches += 1
                path, title = match.group(1), match.group(2).strip()

                # Skip navigation links
                if title.lower() in ("jobs", "careers", "job board", "search", "view all"):
                    continue

                if path.startswith("http"):
                    apply_url = path
                elif path.startswith("/"):
                    apply_url = f"https://www.bcs.org{path}"
                else:
                    apply_url = f"https://www.bcs.org/{path}"

                if apply_url in seen_urls:
                    continue
                seen_urls.add(apply_url)

                jobs.append(Job(
                    title=title,
                    company="Unknown",
                    location="UK",
                    description=title,
                    apply_url=apply_url,
                    source=self.name,
                    date_found=now,
                    posted_at=None,
                    date_confidence="low",
                    date_posted_raw=None,
                ))

            if raw_matches == 0 and len(html) > _MIN_STRUCTURAL_HTML_LEN:
                logger.error(
                    "[bcs_jobs] STRUCTURE CHANGED: expected job-link anchor pattern "
                    "not found in a %d-byte response — parser may be broken",
                    len(html),
                )

            return jobs
        except Exception as e:
            logger.warning("BCS Jobs: HTML parsing failed: %s", e)
            return []
