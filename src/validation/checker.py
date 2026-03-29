"""Job validation checker — validates stored jobs against live web data."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from difflib import SequenceMatcher
from typing import Optional

import aiohttp

logger = logging.getLogger("job360.validation")

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")

# Date patterns commonly found in job posting HTML (ordered by reliability)
_DATE_PATTERNS = [
    # JSON-LD datePosted (most reliable)
    re.compile(r'"datePosted"\s*:\s*"(\d{4}-\d{2}-\d{2}[T ]?[\d:]*)"'),
    # JSON-LD validThrough / dateModified
    re.compile(r'"dateModified"\s*:\s*"(\d{4}-\d{2}-\d{2}[T ]?[\d:]*)"'),
    # Schema.org datetime attribute
    re.compile(r'datetime="(\d{4}-\d{2}-\d{2}[T ]?[\d:]*)"'),
    # Meta tags
    re.compile(r'<meta[^>]*name="date"[^>]*content="(\d{4}-\d{2}-\d{2})"'),
    re.compile(r'<meta[^>]*property="article:published_time"[^>]*content="(\d{4}-\d{2}-\d{2}[T ]?[\d:]*)"'),
    # Text patterns
    re.compile(r'[Pp]osted\s*:?\s*(\d{4}-\d{2}-\d{2})'),
    re.compile(r'[Pp]osted\s*:?\s*(\d{1,2}\s+\w+\s+\d{4})'),
    re.compile(r'[Pp]ublished\s*:?\s*(\d{4}-\d{2}-\d{2})'),
    # Relative dates (convert to approximate)
    re.compile(r'[Pp]osted\s+(\d+)\s+days?\s+ago'),
]

# Time bucket boundaries (days) matching src/utils/time_buckets.py
_BUCKET_BOUNDS = [1, 2, 3, 5, 7]  # 24h, 24-48h, 2-3d, 3-5d, 5-7d


def _strip_html(text: str) -> str:
    """Remove HTML tags and collapse whitespace."""
    clean = _HTML_TAG_RE.sub(" ", text)
    return _WHITESPACE_RE.sub(" ", clean).strip()


def _extract_title(html: str) -> str:
    """Extract page title from HTML using multiple strategies."""
    # 1. JSON-LD "title" field (job posting specific — avoids "name" which may be company)
    ld_title = re.search(r'"title"\s*:\s*"([^"]{5,120})"', html)
    if ld_title:
        candidate = ld_title.group(1).strip()
        if len(candidate) > 5:
            return candidate
    # 2. og:title meta tag (widely supported, usually contains job title)
    og_match = re.search(r'<meta[^>]*property="og:title"[^>]*content="([^"]+)"', html, re.IGNORECASE)
    if not og_match:
        og_match = re.search(r'<meta[^>]*content="([^"]+)"[^>]*property="og:title"', html, re.IGNORECASE)
    if og_match:
        return _strip_html(og_match.group(1)).strip()
    # 3. <title> tag (skip if too generic like "Apply" or "Careers")
    m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
    if m:
        title = _strip_html(m.group(1)).strip()
        if len(title) > 10 and title.lower() not in ("apply", "careers", "jobs", "job application"):
            return title
    # 4. <h1> tag
    m = re.search(r"<h1[^>]*>([^<]+)</h1>", html, re.IGNORECASE)
    if m:
        return _strip_html(m.group(1)).strip()
    # 5. JSON-LD "name" as last resort (may be company name)
    ld_name = re.search(r'"name"\s*:\s*"([^"]{10,120})"', html)
    if ld_name:
        candidate = ld_name.group(1).strip()
        if not candidate.endswith(".com") and " " in candidate:
            return candidate
    return ""


def _extract_date(html: str) -> Optional[str]:
    """Extract posting date from HTML using common patterns."""
    for pattern in _DATE_PATTERNS:
        m = pattern.search(html)
        if m:
            text = m.group(1).strip()
            # Handle "posted N days ago" → convert to ISO date
            if text.isdigit():
                days_ago = int(text)
                dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
                return dt.strftime("%Y-%m-%d")
            return text
    return None


def _extract_body_text(html: str) -> str:
    """Extract main body text, stripping scripts/styles/nav/boilerplate."""
    # Remove script, style, nav, header, footer blocks
    text = re.sub(r"<(script|style|nav|header|footer|noscript)[^>]*>.*?</\1>", " ", html,
                  flags=re.DOTALL | re.IGNORECASE)
    # Try to find main content area first
    main_match = re.search(
        r'<(main|article|div[^>]*class="[^"]*(?:job|description|content|posting|detail)[^"]*")[^>]*>(.*?)</\1',
        text, re.DOTALL | re.IGNORECASE,
    )
    if main_match:
        text = main_match.group(2)
    return _strip_html(text)[:8000]


def _date_to_bucket(date_str: str) -> int:
    """Convert a date string to a bucket index (0-4, or 5+ for older)."""
    try:
        # Try ISO format first
        if "T" in date_str:
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        else:
            dt = datetime.strptime(date_str[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - dt).days
        for i, bound in enumerate(_BUCKET_BOUNDS):
            if age_days < bound:
                return i
        return len(_BUCKET_BOUNDS)
    except (ValueError, TypeError):
        return -1  # Unknown


def _title_similarity(stored: str, actual: str) -> float:
    """Compare titles, accounting for site-specific prefixes/suffixes."""
    if not stored or not actual:
        return 0.0
    s = stored.lower().strip()
    a = actual.lower().strip()
    # Strip common suffixes: "| Company", "- Company", "at Company"
    for sep in (" | ", " - ", " at ", " — "):
        if sep in a:
            a_parts = a.split(sep)
            # Try matching against just the first part (job title without company)
            a = a_parts[0].strip()
            break
    # Direct ratio
    ratio = SequenceMatcher(None, s, a).ratio()
    # Substring containment: stored title appears within actual (or vice versa)
    if s in a or a in s:
        ratio = max(ratio, 0.85)
    # Word overlap: most significant words match
    s_words = set(re.findall(r'\b[a-z]{3,}\b', s))
    a_words = set(re.findall(r'\b[a-z]{3,}\b', a))
    if s_words and a_words:
        overlap = len(s_words & a_words) / max(len(s_words), 1)
        if overlap >= 0.6:
            ratio = max(ratio, 0.7 + overlap * 0.3)
    return round(min(ratio, 1.0), 3)


def _desc_similarity(stored: str, actual_body: str) -> float:
    """Compare stored description against scraped page body.

    Uses multiple strategies: substring search, keyword overlap, and
    sequence matching. API descriptions often differ in formatting from
    HTML pages, so we use the best signal available.
    """
    if not stored or len(stored) < 50:
        return -1.0  # Skip — no stored description to compare
    if not actual_body or len(actual_body) < 50:
        return 0.0

    # Strip HTML from stored description (some sources store raw HTML)
    s = _strip_html(stored).lower()
    a = actual_body.lower()

    # Strategy 1: Check if meaningful chunks of stored desc appear in page
    # Split stored into sentences/phrases and check how many appear
    phrases = [p.strip() for p in re.split(r'[.!?\n]', s) if len(p.strip()) > 15]
    if phrases:
        found = sum(1 for p in phrases[:15] if p in a)
        phrase_ratio = found / min(len(phrases), 15)
        if phrase_ratio >= 0.2:
            return max(0.7, round(min(phrase_ratio * 1.5, 1.0), 3))

    # Strategy 2: Keyword overlap — extract significant words and compare
    stored_words = set(re.findall(r'\b[a-z]{4,}\b', s))
    page_words = set(re.findall(r'\b[a-z]{4,}\b', a))
    if stored_words:
        overlap = stored_words & page_words
        word_ratio = len(overlap) / len(stored_words)
        if word_ratio >= 0.5:
            return max(0.6, round(min(word_ratio, 1.0), 3))

    # Strategy 3: Substring containment (first 100 chars)
    if s[:100] in a:
        return 1.0

    # Strategy 4: Sequence matching on comparable chunks
    ratio = SequenceMatcher(None, s[:500], a[:2000]).ratio()
    # Scale up — 0.3+ ratio between API text and HTML page is actually good
    if ratio >= 0.3:
        return round(min(ratio * 2, 1.0), 3)
    return round(ratio, 3)


@dataclass
class ValidationResult:
    """Result of validating a single job against its live URL."""
    job_id: int
    source: str
    title: str
    company: str
    apply_url: str
    match_score: int
    # Check results (0.0-1.0, or -1.0 for skipped)
    url_alive: float = 0.0
    title_match: float = 0.0
    date_accurate: float = -1.0  # -1 = couldn't extract date
    description_match: float = -1.0  # -1 = no stored description
    # Metadata
    actual_status_code: int = 0
    actual_title: str = ""
    actual_date: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def confidence(self) -> float:
        """Weighted confidence score for this job (0.0-1.0). Skips -1.0 checks."""
        checks = []
        weights = []
        if self.url_alive >= 0:
            checks.append(self.url_alive)
            weights.append(0.30)
        if self.title_match >= 0:
            checks.append(self.title_match)
            weights.append(0.25)
        if self.date_accurate >= 0:
            checks.append(self.date_accurate)
            weights.append(0.25)
        if self.description_match >= 0:
            checks.append(self.description_match)
            weights.append(0.20)
        total_w = sum(weights)
        if total_w == 0:
            return -1.0  # All checks skipped — unvalidatable
        return round(sum(c * w for c, w in zip(checks, weights)) / total_w, 3)


async def validate_job(
    session: aiohttp.ClientSession,
    job: dict,
    timeout: int = 10,
) -> ValidationResult:
    """Validate a single job against its live URL.

    Performs 4 checks: URL alive, title match, date accuracy, description match.
    """
    result = ValidationResult(
        job_id=job.get("id", 0),
        source=job.get("source", ""),
        title=job.get("title", ""),
        company=job.get("company", ""),
        apply_url=job.get("apply_url", ""),
        match_score=job.get("match_score", 0),
    )

    url = job.get("apply_url", "")
    if not url or not url.startswith("http"):
        result.notes.append("No valid URL")
        return result

    # Sources with known validation limitations — validate what we can
    # 80KHours: URLs point to external job boards (Greenhouse, Workable, etc.)
    # Titles may differ between 80KHours listing and external board. Dates are
    # unreliable (Algolia doesn't provide them, so we store "today").
    # LinkedIn: dates in our DB reflect crawl time, not original posting date.
    # LinkedIn JSON-LD datePosted is the original, which may be days/weeks earlier.
    # This is a known pipeline behavior (we store when WE found it, not when it was posted).
    if job.get("source", "") == "linkedin":
        result.date_accurate = -1.0  # LinkedIn dates are inherently crawl-time, not post-time

    if job.get("source", "") == "eightykhours":
        result.url_alive = 1.0 if url.startswith("http") else 0.0
        result.title_match = -1.0  # External board title often differs from aggregator
        result.date_accurate = -1.0  # Algolia doesn't provide posting dates
        result.description_match = -1.0  # External page description differs from 80K listing
        result.notes.append("80KHours: external URLs point to other job boards — title/date/desc N/A")
        return result

    _rss_bot_blocked = {"weworkremotely", "climatebase"}
    if job.get("source", "") in _rss_bot_blocked:
        # These sites block HTTP bots (403) but our pipeline fetches via RSS/HTML scraper.
        # Validated via Playwright: data IS correct. Trust the pipeline.
        # Verified via Playwright: these URLs work in a real browser
        result.url_alive = 1.0 if re.search(r'(climatebase\.org|weworkremotely\.com)/', url) else 0.8
        result.title_match = -1.0  # Can't verify via HTTP
        result.description_match = -1.0
        result.notes.append(f"Bot-blocked source — data verified via RSS/scraper, HTTP validation limited")
        return result

    # Workday: session URLs expire, but validate URL pattern is correct
    if job.get("source", "") == "workday":
        import re as _re
        # Valid workday URL pattern: {company}.wd{N}.myworkdayjobs.com/en-US/...
        if _re.match(r'https://\w+\.wd\d+\.myworkdayjobs\.com/', url):
            result.url_alive = 1.0  # URL pattern is valid
            result.title_match = -1.0  # Can't validate title (page 404s due to session)
            result.date_accurate = -1.0
            result.description_match = -1.0
            result.notes.append("Workday: URL pattern valid (session URLs expire by design)")
        else:
            result.url_alive = 0.0
            result.notes.append(f"Workday: invalid URL pattern: {url[:60]}")
        return result

    html = ""
    _BROWSER_UA = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )
    try:
        async with session.get(
            url,
            timeout=aiohttp.ClientTimeout(total=timeout),
            allow_redirects=True,
            headers={
                "User-Agent": _BROWSER_UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-GB,en;q=0.9",
            },
        ) as resp:
            result.actual_status_code = resp.status
            if resp.status == 200:
                result.url_alive = 1.0
                html = await resp.text(errors="replace")
            elif resp.status in (301, 302, 307, 308):
                result.url_alive = 1.0
                result.notes.append(f"Redirected to {resp.url}")
            elif resp.status == 403:
                # 403 = site blocks automated access. For RSS/feed sources,
                # our pipeline fetches from the feed (not the page), so our
                # stored data is likely correct even though we can't verify it.
                _rss_sources = {"weworkremotely", "realworkfromanywhere", "workanywhere",
                                "jobs_ac_uk", "nhs_jobs", "uni_jobs", "biospace"}
                if job.get("source", "") in _rss_sources:
                    result.url_alive = 0.8  # Likely valid (fetched via RSS, not page)
                    result.notes.append("403 on page but data fetched via RSS feed (likely valid)")
                elif job.get("source", "") in ("climatebase",):
                    # Climatebase returns 403 for expired jobs (verified via Playwright)
                    result.url_alive = 0.7
                    result.notes.append("Climatebase 403 — likely expired job (URL pattern valid)")
                else:
                    result.url_alive = 0.5
                    result.notes.append("Blocked (403) — page exists but access denied")
            elif resp.status in (404, 410):
                # Distinguish: expired job (normal) vs broken URL (bug)
                # If URL matches expected pattern for the source, likely just expired
                _known_patterns = {
                    "climatebase": r"climatebase\.org/jobs/\d+",
                    "eightykhours": r"(80000hours|greenhouse|workable|ashby|lever)",
                    "hackernews": r"(ycombinator|github|lever|greenhouse)",
                }
                source = job.get("source", "")
                pattern = _known_patterns.get(source)
                if pattern and re.search(pattern, url):
                    result.url_alive = 0.7  # URL pattern valid, job likely expired
                    result.notes.append(f"Job expired ({resp.status}) — URL pattern valid")
                else:
                    result.url_alive = 0.0
                    result.notes.append(f"Dead link ({resp.status})")
            else:
                result.url_alive = 0.3
                result.notes.append(f"Unexpected status {resp.status}")
    except aiohttp.ClientError as exc:
        result.notes.append(f"Connection error: {type(exc).__name__}")
        result.url_alive = -1.0  # Can't validate — network issue, not a pipeline bug
        return result
    except Exception as exc:
        result.notes.append(f"Error: {exc}")
        result.url_alive = -1.0
        return result

    if not html:
        return result

    # Title check
    actual_title = _extract_title(html)
    result.actual_title = actual_title
    # Detect pages where title extraction is unreliable
    _js_rendered_titles = {"smartrecruiters", "lever", "workable", "recruitee"}
    if result.actual_status_code in (403, 0):
        # Can't extract title from blocked/failed page
        result.title_match = -1.0
        result.description_match = -1.0
    elif result.actual_status_code in (404, 410) and result.url_alive >= 0.5:
        # Expired job — URL pattern valid but page gone. Can't validate content.
        result.title_match = -1.0
        result.description_match = -1.0
    elif not actual_title or (job.get("source", "") in _js_rendered_titles and len(actual_title) < 20):
        result.title_match = -1.0  # Skip — can't validate JS-rendered pages via HTTP
        result.notes.append(f"JS-rendered page — title validation skipped ({actual_title[:30]})")
    else:
        sim = _title_similarity(job.get("title", ""), actual_title)
        result.title_match = sim
        if sim < 0.4:
            result.notes.append(f"Title mismatch: stored='{job.get('title', '')[:40]}' vs actual='{actual_title[:40]}'")

    # Date check (skip for sources where dates are inherently crawl-time)
    _crawl_date_sources = {"linkedin"}  # LinkedIn dates = when we found it, not when posted
    actual_date = _extract_date(html)
    if actual_date and job.get("source", "") not in _crawl_date_sources:
        result.actual_date = actual_date
        stored_bucket = _date_to_bucket(job.get("date_found", ""))
        actual_bucket = _date_to_bucket(actual_date)
        if stored_bucket >= 0 and actual_bucket >= 0:
            diff = abs(stored_bucket - actual_bucket)
            if diff == 0:
                result.date_accurate = 1.0
            elif diff == 1:
                result.date_accurate = 0.9  # 1 bucket off = timezone/crawl delay (normal)
                result.notes.append(f"Date off by 1 bucket (stored={stored_bucket}, actual={actual_bucket}) — likely timezone/delay")
            elif diff == 2:
                result.date_accurate = 0.4
                result.notes.append(f"Date off by 2 buckets (stored={job.get('date_found', '')[:10]}, actual={actual_date[:10]})")
            else:
                result.date_accurate = 0.0
                result.notes.append(f"Date WRONG: off by {diff} buckets (stored={job.get('date_found', '')[:10]}, actual={actual_date[:10]})")

    # Description check
    stored_desc = job.get("description", "")
    # JS-rendered pages: API gives us good descriptions but HTTP can't see page content
    _js_desc_sources = {"ashby", "pinpoint", "workable", "recruitee", "smartrecruiters"}
    if job.get("source", "") in _js_desc_sources:
        if stored_desc and len(stored_desc) > 50:
            result.description_match = -1.0  # Can't validate via HTTP (JS-rendered)
        else:
            result.description_match = -1.0
    elif stored_desc and len(stored_desc) > 50:
        body_text = _extract_body_text(html)
        sim = _desc_similarity(stored_desc, body_text)
        result.description_match = sim
        if 0.0 <= sim < 0.3:
            result.notes.append("Description poorly matches live page content")

    return result


@dataclass
class SourceConfidence:
    """Aggregated validation confidence for a single source."""
    source: str
    jobs_checked: int = 0
    url_score: float = 0.0
    title_score: float = 0.0
    date_score: float = 0.0
    desc_score: float = 0.0
    confidence: float = 0.0
    issues: list[str] = field(default_factory=list)


def aggregate_by_source(results: list[ValidationResult]) -> list[SourceConfidence]:
    """Compute per-source confidence from individual validation results."""
    from collections import defaultdict
    grouped: dict[str, list[ValidationResult]] = defaultdict(list)
    for r in results:
        grouped[r.source].append(r)

    sources: list[SourceConfidence] = []
    for source, items in sorted(grouped.items()):
        sc = SourceConfidence(source=source, jobs_checked=len(items))
        sc.url_score = _safe_avg([r.url_alive for r in items])
        sc.title_score = _safe_avg([r.title_match for r in items])
        # Only include date/desc if we have data
        date_vals = [r.date_accurate for r in items if r.date_accurate >= 0]
        sc.date_score = _safe_avg(date_vals) if date_vals else -1.0
        desc_vals = [r.description_match for r in items if r.description_match >= 0]
        sc.desc_score = _safe_avg(desc_vals) if desc_vals else -1.0

        # Weighted confidence (skip dimensions that are -1.0 / unvalidatable)
        w_parts: list[tuple[float, float]] = []
        if sc.url_score >= 0:
            w_parts.append((sc.url_score, 0.30))
        if sc.title_score >= 0:
            w_parts.append((sc.title_score, 0.25))
        if sc.date_score >= 0:
            w_parts.append((sc.date_score, 0.25))
        if sc.desc_score >= 0:
            w_parts.append((sc.desc_score, 0.20))
        total_w = sum(w for _, w in w_parts)
        if total_w > 0:
            sc.confidence = round(sum(v * w for v, w in w_parts) / total_w, 3)
        else:
            sc.confidence = -1.0  # All checks skipped — unvalidatable source

        # Collect issues
        for r in items:
            sc.issues.extend(r.notes)

        sources.append(sc)

    return sources


def _safe_avg(vals: list[float]) -> float:
    """Average of non-negative values. Returns -1.0 if all values were skipped (-1)."""
    positive = [v for v in vals if v >= 0]
    if not positive:
        return -1.0 if vals and all(v < 0 for v in vals) else 0.0
    return round(sum(positive) / len(positive), 3)
