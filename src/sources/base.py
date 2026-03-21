import asyncio
import json
import logging
import re
from abc import ABC, abstractmethod

import aiohttp

from src.models import Job
from src.config.settings import MAX_RETRIES, RETRY_BACKOFF, REQUEST_TIMEOUT, USER_AGENT, RATE_LIMITS
from src.filters.skill_matcher import is_foreign_only
from src.utils.rate_limiter import RateLimiter

logger = logging.getLogger("job360.sources")

# HTTP status codes that indicate a bad request format — retrying won't help
_NO_RETRY_STATUSES = (401, 403, 404, 422)

# Circuit breaker: track consecutive failures per source
_circuit_breaker: dict[str, int] = {}
_CIRCUIT_BREAKER_THRESHOLD = 3


def _sanitize_xml(text: str) -> str:
    """Fix common XML issues: unescaped &, invalid chars."""
    # Replace bare & with &amp; (but not already-escaped entities)
    text = re.sub(r'&(?!amp;|lt;|gt;|quot;|apos;|#\d+;|#x[0-9a-fA-F]+;)', '&amp;', text)
    # Remove invalid XML characters
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', text)
    return text


def _is_uk_or_remote(location: str) -> bool:
    """Return True if a job location is likely UK, remote, or unknown."""
    return not is_foreign_only(location)


class BaseJobSource(ABC):
    name: str = "base"

    def __init__(self, session: aiohttp.ClientSession, search_config=None):
        self._session = session
        self._search_config = search_config
        cfg = RATE_LIMITS.get(self.name, {"concurrent": 2, "delay": 1.0})
        self._rate_limiter = RateLimiter(concurrent=cfg["concurrent"], delay=cfg["delay"])

    @property
    def relevance_keywords(self) -> list[str]:
        if self._search_config is not None:
            return self._search_config.relevance_keywords
        return []

    @property
    def job_titles(self) -> list[str]:
        if self._search_config is not None:
            return self._search_config.job_titles
        return []

    @property
    def search_queries(self) -> list[str]:
        if self._search_config is not None and self._search_config.search_queries:
            return self._search_config.search_queries
        return []

    def _relevance_match(self, text: str) -> bool:
        """Check if any relevance keyword appears as a whole word in text.

        Uses word-boundary matching for short keywords to avoid false positives
        (e.g. keyword 'R' matching 'Recruitment', 'Go' matching 'Good').
        """
        if not self.relevance_keywords:
            return True  # No keywords configured — don't filter
        text_lower = text.lower()
        for kw in self.relevance_keywords:
            kw_lower = kw.lower()
            if len(kw_lower) <= 2:
                if re.search(r'\b' + re.escape(kw_lower) + r'\b', text_lower):
                    return True
            else:
                if kw_lower in text_lower:
                    return True
        return False

    def _headers(self, extra: dict | None = None) -> dict:
        """Build request headers with User-Agent default."""
        h = {"User-Agent": USER_AGENT}
        if extra:
            h.update(extra)
        return h

    @abstractmethod
    async def fetch_jobs(self) -> list[Job]:
        ...

    async def safe_fetch(self) -> list[Job]:
        """Fetch with circuit breaker — skip source after consecutive failures."""
        failures = _circuit_breaker.get(self.name, 0)
        if failures >= _CIRCUIT_BREAKER_THRESHOLD:
            logger.warning(
                f"[{self.name}] Circuit breaker OPEN — skipping after "
                f"{failures} consecutive failures"
            )
            return []
        try:
            jobs = await self.fetch_jobs()
            _circuit_breaker[self.name] = 0  # Reset on success
            return jobs
        except Exception as e:
            _circuit_breaker[self.name] = failures + 1
            logger.error(
                f"[{self.name}] Fetch failed ({failures + 1}/"
                f"{_CIRCUIT_BREAKER_THRESHOLD}): {e}"
            )
            return []

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
                        logger.debug(f"[{self.name}] HTTP {resp.status} from {url}")
                        return None
                    if resp.status == 429:
                        retry_after = resp.headers.get("Retry-After")
                        if retry_after and retry_after.isdigit():
                            wait = min(int(retry_after), 60)
                        else:
                            wait = RETRY_BACKOFF[attempt] * 3
                        logger.warning(
                            f"[{self.name}] Rate limited (429), waiting {wait}s "
                            f"(attempt {attempt + 1}/{MAX_RETRIES})"
                        )
                        if attempt < MAX_RETRIES - 1:
                            await asyncio.sleep(wait)
                            continue
                        return None
                    if resp.status >= 400:
                        logger.warning(f"[{self.name}] HTTP {resp.status} from {url}")
                        if attempt < MAX_RETRIES - 1:
                            await asyncio.sleep(RETRY_BACKOFF[attempt])
                            continue
                        return None
                    if as_text:
                        return await resp.text()
                    return await resp.json(content_type=None)
            except exceptions as e:
                logger.warning(f"[{self.name}] Request error: {e}")
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
