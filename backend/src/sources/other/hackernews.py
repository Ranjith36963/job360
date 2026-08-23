import logging
import re
from datetime import datetime, timezone
from typing import Any, cast

from src.models import Job
from src.sources.base import BaseJobSource, _is_uk_or_remote
from src.utils.dates import normalize_posted_at

logger = logging.getLogger("job360.sources.hackernews")

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_URL_RE = re.compile(r"https?://[^\s<>\"]+")

def _parse_hn_comment(text: str) -> dict[str, Any] | None:
    """Parse a HN 'Who is Hiring' comment into job fields.

    First line typically follows: Company | Location | Remote | URL
    """
    if not text:
        return None

    # Strip HTML tags
    clean = _HTML_TAG_RE.sub(" ", text)
    lines = [ln.strip() for ln in clean.split("\n") if ln.strip()]
    if not lines:
        return None

    first_line = lines[0]
    parts = [p.strip() for p in first_line.split("|")]

    company = parts[0] if parts else ""

    # A HN "Who's hiring" post is EXPECTED to start "Company | Location | Role".
    # When a comment is plain prose instead, there is no "|", so parts[0] is the
    # ENTIRE paragraph — and it became both the company and the job title.
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
    location = parts[1] if len(parts) > 1 else ""

    # Extract URL from anywhere in the text
    url_match = _URL_RE.search(clean)
    apply_url = url_match.group(0) if url_match else ""

    # Use full text as description
    description = clean[:5000]

    # Use company name as the title (scorer will evaluate actual relevance)
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
        # Step 1: Find latest "Who is Hiring" thread
        # `/search` ranks by RELEVANCE, not date, so this query returned the
        # same COVID-era thread forever: "Ask HN: Who is hiring right now?"
        # from 2020-03-23. Measured 2026-08-08 — all 118 stored HackerNews
        # jobs carried posted_at=2020-03-23 (and we stamped them
        # date_confidence='high'), i.e. the catalog held six-year-old dead
        # postings presented as current. Found by scripts/distribution_sanity
        # on its first run: one distinct posted_at across 118 rows.
        #
        # `/search_by_date` returns newest-first. Ask for a few hits, not one,
        # because the same author posts a sibling "Who wants to be hired?"
        # thread on the same day — that one is job SEEKERS advertising
        # themselves, the exact inverse of what this source is for.
        params = {
            "tags": "story,author_whoishiring",
            "hitsPerPage": "5",
        }
        data = await self._get_json(
            "https://hn.algolia.com/api/v1/search_by_date",
            params=params,
        )
        if not data or not cast(dict[str, Any], data).get("hits"):
            logger.info("HackerNews: no 'Who is Hiring' thread found")
            return []

        # Newest-first, so the first title that is a HIRING thread wins.
        story_id = None
        for hit in cast(dict[str, Any], data)["hits"]:
            title = str(hit.get("title") or "").lower()
            if "who is hiring" in title and "wants to be hired" not in title:
                story_id = hit.get("objectID")
                logger.info("HackerNews: using thread %r", hit.get("title"))
                break
        if not story_id:
            logger.info("HackerNews: no current 'Who is hiring' thread in the "
                        "newest %s stories", params["hitsPerPage"])
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
