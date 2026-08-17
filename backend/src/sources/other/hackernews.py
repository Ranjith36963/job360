import html
import logging
import re
from datetime import datetime, timezone
from typing import Any, cast

from src.models import Job
from src.sources.base import BaseJobSource, _is_uk_or_remote
from src.utils.dates import normalize_posted_at

logger = logging.getLogger("job360.sources.hackernews")

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_HREF_RE = re.compile(r'href="([^"]+)"')
_URL_RE = re.compile(r"https?://[^\s<>\"]+")

def _parse_hn_comment(text: str) -> dict[str, Any] | None:
    """Parse a HN 'Who is Hiring' comment into job fields.

    First line typically follows: Company | Role(s) | Location | Type
    (order varies across posters -- this is a community convention, not a
    schema -- so `location`/`title` below are best-effort, not guaranteed).
    """
    if not text:
        return None

    # HN comment `text` is HTML with entity-escaped hrefs, e.g.
    # <a href="https:&#x2F;&#x2F;example.com&#x2F;">https:&#x2F;&#x2F;example.com&#x2F;</a>.
    # Pull the URL out of the href BEFORE stripping tags -- measured live
    # 2026-08-16: every apply_url came back empty because the tag-strip threw
    # the href away, and the un-unescaped &#x2F; in the visible link text
    # never matched _URL_RE either. Unescape first so both paths can match.
    unescaped = html.unescape(text)
    href_match = _HREF_RE.search(unescaped)
    apply_url = href_match.group(1) if href_match else ""

    # Strip HTML tags
    clean = _HTML_TAG_RE.sub(" ", unescaped)
    if not apply_url:
        url_match = _URL_RE.search(clean)
        apply_url = url_match.group(0) if url_match else ""

    lines = [ln.strip() for ln in clean.split("\n") if ln.strip()]
    if not lines:
        return None

    first_line = lines[0]
    # The company's own URL usually sits right after its name with no pipe
    # separating it (the anchor text mirrors the href) -- drop it from the
    # company segment so it does not pollute the company field.
    first_line_no_url = _URL_RE.sub(" ", first_line).strip()
    parts = [p.strip() for p in first_line_no_url.split("|")]

    company = parts[0] if parts else ""

    # A HN "Who's hiring" post is EXPECTED to start "Company | Roles | ...".
    # When a comment is plain prose instead, there is no "|", so parts[0] is the
    # ENTIRE paragraph -- and it became both the company and the job title.
    #
    # Measured 2026-08-03: 14 such titles over 300 chars, longest 1,551. They
    # render as unreadable cards, produce meaningless dedup keys, and one of them
    # blew Postgres's 2,704-byte index limit and aborted a real user's search
    # twice, freezing his feed for seven days.
    #
    # No company name is 120 characters. If we cannot find one, this comment is
    # not a parseable job posting and we say so, rather than inventing a job out
    # of a paragraph. Dropping a non-posting costs nothing; storing it cost a
    # week of one user's feed.
    if len(company) > 120:
        return None
    location = parts[2] if len(parts) > 2 else (parts[1] if len(parts) > 1 else "")

    # Use full text as description
    description = clean[:5000]

    # The role(s) sit in the second pipe-delimited field on most posts
    # (Company | Role(s) | Location | Type) -- measured live 2026-08-16. This
    # is the RAW field, not a parsed role: some posters put location or a
    # comp range there instead, since "who's hiring" is a convention, not a
    # schema. Still strictly better than the old fabricated
    # "{company} - Hiring" title, which discarded the real text entirely.
    # Same 120-char / must-exist guard as company, for the same reason.
    role_field = parts[1] if len(parts) > 1 else ""
    if role_field and len(role_field) <= 120:
        title = role_field
    else:
        title = f"{company} - Hiring" if company else "Unknown - Hiring"

    return {
        "company": company,
        "location": location,
        "apply_url": apply_url,
        "description": description,
        "title": title,
    }

class HackerNewsSource(BaseJobSource):
    name = "hackernews"
    category = "other"
    DOMAINS = {"tech"}

    async def fetch_jobs(self) -> list[Job]:
        # Step 1: Find latest "Who is Hiring" thread.
        #
        # `/api/v1/search` ranks by ALGOLIA RELEVANCE, not recency -- measured
        # live 2026-08-16: it returned story 22665398, "Ask HN: Who is hiring
        # right now?" from 2020-03-23, as the #1 hit for this exact query,
        # ahead of the real August-2026 thread. Every job ingested from that
        # story was ~6 years old and stamped date_confidence="high" (the
        # comment's own created_at parses fine -- it is just old). Every
        # `/search` result was a REAL comment with a REAL date; the staleness
        # was invisible downstream because nothing was malformed, just six
        # years late. `/api/v1/search_by_date` with the same query+tags
        # sorts by recency and returns the current month's thread first
        # (verified live: "Ask HN: Who is hiring? (August 2026)").
        params = {
            "query": "Ask HN: Who is hiring?",
            "tags": "story,author_whoishiring",
            "hitsPerPage": "1",
        }
        data = await self._get_json(
            "https://hn.algolia.com/api/v1/search_by_date",
            params=params,
        )
        if not data or not cast(dict[str, Any], data).get("hits"):
            logger.info("HackerNews: no 'Who is Hiring' thread found")
            return []

        story_id = cast(dict[str, Any], data)["hits"][0].get("objectID")
        if not story_id:
            return []

        # Step 2: Fetch comments (each comment = one job posting)
        comments_data = await self._get_json(
            f"https://hn.algolia.com/api/v1/items/{story_id}",
        )
        if not comments_data or "children" not in comments_data:
            return []

        jobs = []
        children = cast(dict[str, Any], comments_data).get("children", [])

        for child in children[:200]:  # Cap at 200 comments
            comment_text = child.get("text", "")
            if not comment_text:
                continue

            parsed = _parse_hn_comment(comment_text)
            if not parsed:
                continue

            location = parsed["location"]
            if not _is_uk_or_remote(location):
                continue

            now_iso = datetime.now(timezone.utc).isoformat()
            raw_created = child.get("created_at")
            posted_at, confidence = normalize_posted_at(raw_created)

            jobs.append(Job(
                title=parsed["title"],
                company=parsed["company"],
                location=location,
                description=parsed["description"],
                apply_url=parsed["apply_url"],
                source=self.name,
                date_found=now_iso,
                posted_at=posted_at,
                date_confidence=confidence,
                date_posted_raw=raw_created,
            ))

        logger.info("HackerNews: found %s relevant jobs", len(jobs))
        return jobs
