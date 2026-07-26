import html as html_lib
import logging
import re
from datetime import datetime, timezone

from src.models import Job
from src.sources.base import BaseJobSource, _is_uk_or_remote

logger = logging.getLogger("job360.sources.aijobs_ai")

# Match the job-card ANCHOR and capture its whole inner HTML.
#
# The previous pattern ended in `>\s*([^<]+?)\s*</a>`, i.e. it required the
# anchor to contain plain text and NOTHING else. In 2026-07 aijobs.ai moved to
# card components — the <a> now wraps nested <div>s — so that pattern matched
# zero anchors and the source silently returned no jobs (Sentry
# PYTHON-FASTAPI-7). Capturing inner HTML and pulling the text out afterwards
# survives markup churn: only the URL scheme (/job/<slug>) has to stay stable.
#
# Anchoring the group right after `href="` keeps third-party ad links out —
# e.g. https://www.telusinternational.ai/.../jobs/available/123 contains
# "/jobs/" but is not an aijobs.ai listing.
_JOB_ANCHOR_RE = re.compile(
    r'<a\s[^>]*href="((?:https?://(?:www\.)?aijobs\.ai)?/jobs?/[^"#?]+)"[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)

# Company sits in a `*card-title*` span inside the card. Class names are the
# first thing a redesign changes, so treat this as a HINT — `_parse_html`
# falls back to the surrounding block, then to "Unknown".
_CARD_TITLE_RE = re.compile(
    r'class="[^"]*card-title[^"]*"[^>]*>\s*([^<]+?)\s*<', re.IGNORECASE
)

_SCRIPT_RE = re.compile(r"<(script|style)\b.*?</\1>", re.IGNORECASE | re.DOTALL)
# Comments MUST be stripped before tags: `<[^>]+>` stops at the first `>`, so a
# comment containing one (very common in build output) leaves a stray `-->`
# behind that then reads as visible text. Two live cards reported their company
# as "-->" until this was added.
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_NAV_TITLES = {"view all", "see more", "load more", "apply now", "view job"}

# S4: structural health check. If a non-trivially large page comes back but
# the anchor regex above matches ZERO raw job-link candidates, the site's
# markup changed — not a real zero-listing day — and the parser is silently
# blind.
_MIN_STRUCTURAL_HTML_LEN = 2000


class AIJobsAISource(BaseJobSource):
    """aijobs.ai — dedicated AI job board with server-rendered listings."""
    name = "aijobs_ai"
    category = "scrapers"
    DOMAINS = {"tech"}

    async def fetch_jobs(self) -> list[Job]:
        jobs = []
        seen_urls = set()

        # Try multiple pages/categories
        urls = [
            "https://aijobs.ai/",
            "https://aijobs.ai/remote/",
        ]

        for page_url in urls:
            html = await self._get_text(page_url)
            if not html:
                continue
            for job in self._parse_html(html):
                if job.apply_url not in seen_urls:
                    seen_urls.add(job.apply_url)
                    jobs.append(job)

        logger.info("AI Jobs AI: found %s relevant jobs", len(jobs))
        return jobs

    @staticmethod
    def _card_texts(inner_html: str) -> list[str]:
        """Visible text runs inside a card anchor, in document order.

        For the card layout this yields roughly [title, age, job type,
        company]; for the older plain-text anchor it yields just [title].
        """
        cleaned = _COMMENT_RE.sub(" ", _SCRIPT_RE.sub(" ", inner_html))
        parts = (re.sub(r"\s+", " ", p).strip() for p in _TAG_RE.split(cleaned))
        return [html_lib.unescape(p) for p in parts if p]

    def _parse_html(self, html: str) -> list[Job]:
        try:
            jobs = []
            now = datetime.now(timezone.utc).isoformat()
            raw_matches = 0

            for match in _JOB_ANCHOR_RE.finditer(html):
                raw_matches += 1
                path, inner = match.group(1), match.group(2)

                texts = self._card_texts(inner)
                if not texts:
                    continue
                title = texts[0]

                # Skip navigation/non-job links
                if len(title) < 5 or title.lower() in _NAV_TITLES:
                    continue

                pos = match.start()
                block = html[max(0, pos - 500):pos + 1000]

                # Company: the card-title span, else the surrounding block
                # (how the older markup exposed it). Deliberately NO
                # "last text run" fallback — sponsored cards omit the
                # card-title span and their last run is the LOCATION, which
                # produced company="United States" on two live listings. A
                # wrong company is worse than a missing one: `company` feeds
                # `normalized_key()` dedup (hard rule #1), so a bogus value
                # silently splits or merges the wrong postings.
                card_title = _CARD_TITLE_RE.search(inner)
                if card_title:
                    company = html_lib.unescape(card_title.group(1).strip())
                else:
                    company = self._extract_nearby(
                        block, r'(?:company|employer|org)[^"]*"[^>]*>\s*([^<]+)'
                    )

                # Cards carry NO location (verified against the live page), so
                # this stays empty for them — and `_is_uk_or_remote("")` is
                # True ("unknown, don't filter", base.py), which is what we
                # want: never drop a job for a field the site stopped showing.
                location = self._extract_nearby(
                    block, r'(?:location|city)[^"]*"[^>]*>\s*([^<]+)'
                )

                if not _is_uk_or_remote(location):
                    continue

                apply_url = path if path.startswith("http") else f"https://aijobs.ai{path}"

                jobs.append(Job(
                    title=title,
                    company=company or "Unknown",
                    location=location or "",
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
                    "[aijobs_ai] STRUCTURE CHANGED: expected job-link anchor pattern "
                    "not found in a %d-byte response — parser may be broken",
                    len(html),
                )

            return jobs
        except Exception as e:
            logger.warning("AI Jobs AI: HTML parsing failed: %s", e)
            return []

    @staticmethod
    def _extract_nearby(block: str, pattern: str) -> str:
        m = re.search(pattern, block, re.IGNORECASE)
        return m.group(1).strip() if m else ""
