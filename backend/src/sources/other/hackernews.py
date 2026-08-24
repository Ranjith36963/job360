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
# Where a HN comment's header line ends and its prose begins.
_PARA_BREAK_RE = re.compile(r"<p>|<br\s*/?>", re.IGNORECASE)

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

    # The 4th pipe-delimited field (Company | Role(s) | Location | Type) is
    # a poster's own short descriptor -- measured live 2026-08-16: 195/242
    # comments (81%) carry one, e.g. "Full-time", "ONSITE", "HYBRID",
    # "Remote". It was parsed into location/title/company and then dropped.
    # It conflates employment-type words ("Full-time") and workplace-mode
    # words ("ONSITE"/"HYBRID"/"REMOTE") the same way arbeitnow's job_types
    # does -- handed to BOTH job.employment_type and job.workplace_mode
    # below; each field's own closed-enum matcher only accepts its own
    # vocabulary, so a word that means the other concept just lands as an
    # honest "not_mapped", never a wrong classification. A short-length
    # guard (60 chars) keeps out the small share of posts where the 4th
    # field is actually a prose fragment ("Full Time Join us at Snout...")
    # rather than a short descriptor.
    #
    # It must be read off the HEADER LINE ONLY. A HN comment is
    # "Company | Role | Location | Type<p>then the prose", and the tag-strip
    # above turns that <p> into a SPACE, so `parts[3]` is the type word plus
    # the whole first sentence of the ad ("Remote We are looking for a
    # machine learning engineer..."). That silently broke the field two ways:
    # long posts blew the 60-char guard and lost a value they really had, and
    # short ones ("Full-time Join our team.") kept a value the gate can never
    # match. Cut at the paragraph break first, then split on pipes.
    header = _PARA_BREAK_RE.split(unescaped, maxsplit=1)[0]
    header_clean = _URL_RE.sub(" ", _HTML_TAG_RE.sub(" ", header))
    header_parts = [p.strip() for p in header_clean.split("|")]
    type_field = header_parts[3].strip() if len(header_parts) > 3 else ""
    type_field = type_field if type_field and len(type_field) <= 60 else None

    return {
        "company": company,
        "location": location,
        "apply_url": apply_url,
        "description": description,
        "title": title,
        "type_field": type_field,
    }

class HackerNewsSource(BaseJobSource):
    name = "hackernews"
    category = "other"
    DOMAINS = {"tech"}

    async def fetch_jobs(self) -> list[Job]:
        # Step 1: Find latest "Who is Hiring" thread.
        #
        # `/search` ranks by ALGOLIA RELEVANCE, not date, so this query returned
        # the same COVID-era thread forever: story 22665398, "Ask HN: Who is
        # hiring right now?" from 2020-03-23, came back as the #1 hit ahead of
        # the real current thread (re-measured live 2026-08-16).
        #
        # Measured 2026-08-08 — all 118 stored HackerNews jobs carried
        # posted_at=2020-03-23 and we stamped them date_confidence='high', i.e.
        # the catalog held six-year-old dead postings presented as current.
        # Found by scripts/distribution_sanity on its first run: ONE distinct
        # posted_at across 118 rows. Nothing was malformed — every result was a
        # real comment with a real date, just six years late — which is exactly
        # why nothing downstream noticed.
        #
        # `/search_by_date` returns newest-first (verified live: "Ask HN: Who is
        # hiring? (August 2026)"). Ask for a few hits, not one, because the same
        # author posts a sibling "Who wants to be hired?" thread on the same day
        # — that one is job SEEKERS advertising themselves, the exact inverse of
        # what this source is for.
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
                employment_type=parsed["type_field"],
                workplace_mode=parsed["type_field"],
            ))

        logger.info("HackerNews: found %s relevant jobs", len(jobs))
        return jobs
