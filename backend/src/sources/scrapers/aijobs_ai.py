import html as html_lib
import json
import logging
import re
from datetime import datetime, timezone

from src.models import Job
from src.sources.base import BaseJobSource, _is_uk_or_remote
from src.utils.dates import normalize_posted_at

logger = logging.getLogger("job360.sources.aijobs_ai")

# Per-job detail page carries the FULL description plus datePosted and
# validThrough in a schema.org JobPosting ld+json block — the list page only
# ever gives us the title (description=title, confirmed live 2026-08-16).
_LDJSON_RE = re.compile(
    r'<script type="application/ld\+json">(.*?)</script>', re.DOTALL
)
# Rate-limited (2s/request, RATE_LIMITS["aijobs_ai"]) and serial: 15 detail
# fetches costs ~30s on top of the 2 list-page fetches, well inside the 60s
# SOURCE_FETCH_TIMEOUT ceiling (see linkedin.py for what happens when a
# source does NOT budget this).
_MAX_DETAIL_FETCHES = 15

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

        detailed = 0
        for job in jobs[:_MAX_DETAIL_FETCHES]:
            try:
                if await self._fill_detail(job):
                    detailed += 1
            except Exception as e:  # noqa: BLE001 -- a detail miss never drops the job
                logger.debug("AI Jobs AI: detail fetch failed for %s: %s", job.apply_url, e)

        logger.info(
            "AI Jobs AI: found %s relevant jobs (%s with detail)", len(jobs), detailed
        )
        return jobs

    async def _fill_detail(self, job: Job) -> bool:
        """Pull description/datePosted/validThrough/employmentType/
        jobLocationType from the job's own schema.org JobPosting ld+json.
        The last two (confirmed live 2026-08-17: "FULL_TIME" /
        "TELECOMMUTE") were sitting in the SAME object this source already
        parses for description/dates and were simply never read. Returns
        True iff at least one field was recovered; a total miss never drops
        the job (title-only description is the safe fallback already on it).
        """
        page = await self._get_text(job.apply_url)
        if not page:
            return False
        m = _LDJSON_RE.search(page)
        if not m:
            return False
        fields = self._extract_ldjson_fields(m.group(1))
        if not fields:
            return False
        if fields.get("description"):
            cleaned = _TAG_RE.sub(" ", html_lib.unescape(fields["description"]))
            cleaned = cleaned.replace("\n", " ").strip()
            if cleaned:
                job.description = cleaned[:5000]
        if fields.get("datePosted"):
            posted_at, confidence = normalize_posted_at(fields["datePosted"])
            if posted_at:
                job.posted_at = posted_at
                job.date_confidence = confidence
                job.date_posted_raw = fields["datePosted"]
        if fields.get("validThrough"):
            deadline_iso, deadline_confidence = normalize_posted_at(fields["validThrough"])
            if deadline_confidence == "high" and deadline_iso:
                job.deadline = deadline_iso[:10]
                job.deadline_source = "listing"
        if fields.get("employmentType"):
            job.employment_type = fields["employmentType"]
        if fields.get("jobLocationType"):
            job.workplace_mode = fields["jobLocationType"]
        return True

    @staticmethod
    def _extract_ldjson_fields(raw: str) -> dict:
        """Tolerant JobPosting field extractor.

        aijobs.ai's per-job ld+json is frequently NOT valid JSON: description
        HTML embeds literal unescaped quotes (e.g. `href="..."`) and some
        jobs are missing a trailing comma between fields (confirmed live
        2026-08-16, one real posting reproduced the exact break). Try strict
        json.loads first (works whenever the description has no embedded
        markup with quotes); fall back to independent per-field regexes that
        do not require the whole document to be well-formed. A field this
        cannot find stays absent — never guessed (rule #29).
        """
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                out = {}
                for key in ("description", "datePosted", "validThrough", "employmentType", "jobLocationType"):
                    val = data.get(key)
                    if isinstance(val, str) and val:
                        out[key] = val
                return out
        except (ValueError, TypeError):
            pass

        fields: dict = {}
        dp = re.search(r'"datePosted"\s*:\s*"([^"]*)"', raw)
        if dp and dp.group(1):
            fields["datePosted"] = dp.group(1)
        vt = re.search(r'"validThrough"\s*:\s*"([^"]*)"', raw)
        if vt and vt.group(1):
            fields["validThrough"] = vt.group(1)
        et = re.search(r'"employmentType"\s*:\s*"([^"]*)"', raw)
        if et and et.group(1):
            fields["employmentType"] = et.group(1)
        jlt = re.search(r'"jobLocationType"\s*:\s*"([^"]*)"', raw)
        if jlt and jlt.group(1):
            fields["jobLocationType"] = jlt.group(1)
        # description: bounded by the next top-level JobPosting key this site
        # emits after it — tolerant of the embedded unescaped quotes above,
        # which strict JSON parsing cannot survive.
        desc = re.search(
            r'"description"\s*:\s*"(.*)",\s*'
            r'"(?:identifier|datePosted|validThrough|jobLocationType|'
            r'employmentType|hiringOrganization|jobLocation|baseSalary)"',
            raw, re.DOTALL,
        )
        if desc and desc.group(1):
            fields["description"] = desc.group(1)
        return fields

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
                # "last text run" fallback for the NO-span case — sponsored
                # cards omit the card-title span and (see below) their last
                # text run is the LOCATION, not a company.
                card_title = _CARD_TITLE_RE.search(inner)
                if card_title:
                    company = html_lib.unescape(card_title.group(1).strip())
                else:
                    company = self._extract_nearby(
                        block, r'(?:company|employer|org)[^"]*"[^>]*>\s*([^<]+)'
                    )

                # Location: no <location>/<city> HTML attribute exists on
                # this markup — the site puts it as a plain text run inside
                # the card instead. Confirmed live 2026-08-16 across both
                # card layouts:
                #   - "featured" cards (no card-title span): exactly 3 texts
                #     [title, employment_type, location] — texts[2] IS the
                #     location ('India', 'United States' seen live).
                #   - "grid" cards (card-title span present): a location,
                #     when the card states one, is the LAST text run and it
                #     sits strictly after the company text, e.g.
                #     [..., 'Forma.ai', 'Canada']; cards with no stated
                #     location simply end at the company, e.g.
                #     [..., 'Pinterest'].
                # This used to be thrown away entirely (comment above used
                # to claim "cards carry NO location, verified against the
                # live page" — true only for the layout that existed then).
                # Discarding it meant `_is_uk_or_remote("")` always reads
                # "unknown, don't filter" and a non-UK card sails through —
                # confirmed live: a 'United States' card slipped this filter
                # (harvest 2026-08-16, UNIVERSAL_SHELF.md §1 row 3).
                if card_title:
                    location = texts[-1] if texts and texts[-1] != company else ""
                else:
                    location = texts[2] if len(texts) >= 3 else ""

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
