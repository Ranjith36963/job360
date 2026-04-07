import asyncio
import json
import logging
import re
from abc import ABC, abstractmethod

import aiohttp

from src.models import Job
from src.config.settings import MAX_RETRIES, RETRY_BACKOFF, REQUEST_TIMEOUT, USER_AGENT, RATE_LIMITS
from src.filters.skill_matcher import UK_TERMS, REMOTE_TERMS, FOREIGN_INDICATORS
from src.utils.rate_limiter import RateLimiter
from src.config.keywords import RELEVANCE_KEYWORDS as _DEFAULT_RELEVANCE_KEYWORDS
from src.config.keywords import JOB_TITLES as _DEFAULT_JOB_TITLES

logger = logging.getLogger("job360.sources")

# HTTP status codes that indicate a bad request format — retrying won't help
_NO_RETRY_STATUSES = (401, 403, 404, 422)


def _sanitize_xml(text: str) -> str:
    """Fix common XML issues: unescaped &, invalid chars."""
    # Replace bare & with &amp; (but not already-escaped entities)
    text = re.sub(r'&(?!amp;|lt;|gt;|quot;|apos;|#\d+;|#x[0-9a-fA-F]+;)', '&amp;', text)
    # Remove invalid XML characters
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', text)
    return text


def _is_uk_or_remote(location: str) -> bool:
    """Return True if a job location is likely UK, remote, or unknown."""
    if not location:
        return True  # Unknown — might be UK, don't filter
    loc_lower = location.lower()
    for term in UK_TERMS:
        if term in loc_lower:
            return True
    for term in REMOTE_TERMS:
        if term in loc_lower:
            return True
    for indicator in FOREIGN_INDICATORS:
        if indicator in loc_lower:
            return False
    return True  # Unknown location, don't filter out


class BaseJobSource(ABC):
    name: str = "base"
    category: str = "unknown"  # keyed_api, free_json, ats, rss, scraper, other

    def __init__(self, session: aiohttp.ClientSession, search_config=None):
        self._session = session
        self._search_config = search_config
        cfg = RATE_LIMITS.get(self.name, {"concurrent": 2, "delay": 1.0})
        self._rate_limiter = RateLimiter(concurrent=cfg["concurrent"], delay=cfg["delay"])

    @property
    def relevance_keywords(self) -> list[str]:
        if self._search_config is not None:
            return self._search_config.relevance_keywords
        return _DEFAULT_RELEVANCE_KEYWORDS

    @property
    def job_titles(self) -> list[str]:
        if self._search_config is not None:
            return self._search_config.job_titles
        return _DEFAULT_JOB_TITLES

    @property
    def search_queries(self) -> list[str]:
        if self._search_config is not None and self._search_config.search_queries:
            return self._search_config.search_queries
        return []

    def _headers(self, extra: dict | None = None) -> dict:
        """Build request headers with User-Agent default."""
        h = {"User-Agent": USER_AGENT}
        if extra:
            h.update(extra)
        return h

    @abstractmethod
    async def fetch_jobs(self) -> list[Job]:
        ...

    async def _request(self, method: str, url: str, *,
                       params: dict | None = None,
                       body: dict | None = None,
                       headers: dict | None = None,
                       as_text: bool = False):
        """Shared retry/rate-limit logic for all HTTP methods."""
        exceptions = (aiohttp.ClientError, asyncio.TimeoutError)
        if not as_text:
            exceptions = (*exceptions, json.JSONDecodeError)

        for attempt in range(MAX_RETRIES):
            await self._rate_limiter.acquire()
            try:
                kwargs = {
                    "headers": self._headers(headers),
                    "timeout": aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
                }
                if method == "GET":
                    kwargs["params"] = params
                else:
                    kwargs["json"] = body or {}

                async with self._session.request(method, url, **kwargs) as resp:
                    if resp.status in _NO_RETRY_STATUSES:
                        logger.debug("[%s] HTTP %s from %s", self.name, resp.status, url)
                        return None
                    if resp.status == 429:
                        retry_after = resp.headers.get("Retry-After")
                        if retry_after and retry_after.isdigit():
                            wait = min(int(retry_after), 60)
                        else:
                            wait = RETRY_BACKOFF[attempt] * 3
                        logger.warning(
                            "[%s] Rate limited (429), waiting %ss (attempt %s/%s)",
                            self.name, wait, attempt + 1, MAX_RETRIES
                        )
                        if attempt < MAX_RETRIES - 1:
                            await asyncio.sleep(wait)
                            continue
                        return None
                    if resp.status >= 400:
                        logger.warning("[%s] HTTP %s from %s", self.name, resp.status, url)
                        if attempt < MAX_RETRIES - 1:
                            await asyncio.sleep(RETRY_BACKOFF[attempt])
                            continue
                        return None
                    if as_text:
                        return await resp.text()
                    return await resp.json(content_type=None)
            except exceptions as e:
                logger.warning("[%s] Request error: %s", self.name, e)
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(RETRY_BACKOFF[attempt])
            finally:
                self._rate_limiter.release()
        return None

    async def _get_json(self, url: str, params: dict | None = None,
                        headers: dict | None = None) -> dict | list | None:
        return await self._request("GET", url, params=params, headers=headers)

    async def _post_json(self, url: str, body: dict | None = None,
                         headers: dict | None = None) -> dict | list | None:
        return await self._request("POST", url, body=body, headers=headers)

    async def _get_text(self, url: str, params: dict | None = None,
                        headers: dict | None = None) -> str | None:
        return await self._request("GET", url, params=params, headers=headers, as_text=True)
