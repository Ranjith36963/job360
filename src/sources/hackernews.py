import logging
import re
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource, _is_uk_or_remote

logger = logging.getLogger("job360.sources.hackernews")

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_URL_RE = re.compile(r"https?://[^\s<>\"]+")


def _parse_hn_comment(text: str, relevance_keywords: list[str]) -> dict | None:
    """Parse a HN 'Who is Hiring' comment into job fields.

    First line typically follows: Company | Location | Remote | URL
    """
    if not text:
        return None

    # Strip HTML tags
    clean = _HTML_TAG_RE.sub(" ", text)
    lines = [l.strip() for l in clean.split("\n") if l.strip()]
    if not lines:
        return None

    first_line = lines[0]
    parts = [p.strip() for p in first_line.split("|")]

    company = parts[0] if parts else ""
    location = parts[1] if len(parts) > 1 else ""

    # Extract URL from anywhere in the text
    url_match = _URL_RE.search(clean)
    apply_url = url_match.group(0) if url_match else ""

    # Use full text as description
    description = clean[:5000]

    # Infer title from relevance keywords in the text (word-boundary match)
    title = ""
    text_lower = clean.lower()
    for kw in relevance_keywords:
        kw_lower = kw.lower()
        if len(kw_lower) <= 2:
            if re.search(r'\b' + re.escape(kw_lower) + r'\b', text_lower):
                title = f"{company} - Hiring"
                break
        else:
            if kw_lower in text_lower:
                title = f"{company} - Hiring"
                break

    if not title:
        return None

    return {
        "company": company,
        "location": location,
        "apply_url": apply_url,
        "description": description,
        "title": title,
    }


class HackerNewsSource(BaseJobSource):
    name = "hackernews"

    async def fetch_jobs(self) -> list[Job]:
        # Step 1: Find latest "Who is Hiring" thread
        params = {
            "query": "Ask HN: Who is hiring?",
            "tags": "story,author_whoishiring",
            "hitsPerPage": "1",
        }
        data = await self._get_json(
            "https://hn.algolia.com/api/v1/search",
            params=params,
        )
        if not data or not data.get("hits"):
            logger.info("HackerNews: no 'Who is Hiring' thread found")
            return []

        story_id = data["hits"][0].get("objectID")
        if not story_id:
            return []

        # Step 2: Fetch comments (each comment = one job posting)
        comments_data = await self._get_json(
            f"https://hn.algolia.com/api/v1/items/{story_id}",
        )
        if not comments_data or "children" not in comments_data:
            return []

        jobs = []
        children = comments_data.get("children", [])

        for child in children[:200]:  # Cap at 200 comments
            comment_text = child.get("text", "")
            if not comment_text:
                continue

            text_lower = comment_text.lower()
            if not self._relevance_match(text_lower):
                continue

            parsed = _parse_hn_comment(comment_text, self.relevance_keywords)
            if not parsed:
                continue

            location = parsed["location"]
            if not _is_uk_or_remote(location):
                continue

            created_at = child.get("created_at") or datetime.now(timezone.utc).isoformat()

            jobs.append(Job(
                title=parsed["title"],
                company=parsed["company"],
                location=location,
                description=parsed["description"],
                apply_url=parsed["apply_url"],
                source=self.name,
                date_found=created_at,
            ))

        logger.info(f"HackerNews: found {len(jobs)} relevant jobs")
        return jobs
