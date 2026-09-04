import asyncio
import json
import logging
import re
from abc import ABC, abstractmethod
from typing import Any, Optional, Union, cast

import aiohttp

from src.core.keywords import JOB_TITLES as _DEFAULT_JOB_TITLES
from src.core.keywords import RELEVANCE_KEYWORDS as _DEFAULT_RELEVANCE_KEYWORDS
from src.core.settings import MAX_RETRIES, RATE_LIMITS, REQUEST_TIMEOUT, RETRY_BACKOFF, USER_AGENT
from src.models import Job
from src.services.conditional_cache import CachedEntry, ConditionalCache
from src.services.profile.models import SearchConfig
from src.services.uk_gate import names_foreign_place
from src.utils.rate_limiter import RateLimiter

logger = logging.getLogger("job360.sources")

# HTTP status codes that indicate a bad request format — retrying won't help
_NO_RETRY_STATUSES = (401, 403, 404, 422)

# Auth-failure statuses within _NO_RETRY_STATUSES — an expired/bad API key is an
# operational problem (S9), not a routine "not found"/"unprocessable" response,
# so these get logged louder than 404/422.
_AUTH_FAIL_STATUSES = (401, 403)


def _sanitize_xml(text: str) -> str:
    """Fix common XML issues: unescaped &, invalid chars."""
    # Replace bare & with &amp; (but not already-escaped entities)
    text = re.sub(r'&(?!amp;|lt;|gt;|quot;|apos;|#\d+;|#x[0-9a-fA-F]+;)', '&amp;', text)
    # Remove invalid XML characters
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', text)
    return text


def _is_uk_or_remote(location: str) -> bool:
    """Fetch-time skip for jobs whose LOCATION FIELD names a place outside the UK.

    This is NOT the door. One chokepoint decides what enters the catalog —
    `services/uk_gate.check_uk`, called in `main.py` before storage with the
    source name and the ad body in hand. This only avoids carrying obviously
    foreign rows through scoring and the O(n^2) dedup (see
    docs/product/plans/2026-07-26-uk-first-location-eligibility.md, adversary catch #2).

    WHY IT ONLY EVER SEES A LOCATION, AND WHY THAT IS LOAD-BEARING
    -------------------------------------------------------------
    It used to answer from `FOREIGN_INDICATORS`, a hand-typed set of foreign
    cities and US state codes. That set is unbounded by nature, so it rotted
    ("seoul" and "ottawa" were never in it) and it mis-fired ("Belfast, Northern
    Ireland" matched its "ireland" entry, docking a genuinely UK job). Rule #30
    bans exactly that. It now asks the gate's `names_foreign_place`, which reads
    a COMPLETE, data-built set of countries and first-level admin divisions.

    But a complete set of ISO codes contains `LI`, `BR`, `TD`, `TR`, `TH`, `HR` —
    Liechtenstein, Brazil, Chad, Turkey, Thailand, Croatia. Four callers pass an
    ad DESCRIPTION here, and the gate splits on `/`, so the closing tag `</li>`
    yields the segment `li` and ordinary HTML markup reads as a foreign country.
    A first version of this function did exactly that and would have silently
    dropped UK-eligible jobs at fetch, before anything could log them.

    So this refuses ONLY on a bare location string, and only when the whole
    trimmed value names a foreign place. Anything containing markup, or any text
    long enough to be prose rather than a place, is passed straight to the door,
    which has the source name and the full body and can judge properly.

    The invariant to preserve if you touch this: nothing dropped here may be
    something the door would have admitted. Guarded by
    `tests/test_sources.py::test_fetch_filter_never_drops_what_the_door_admits`.
    """
    if not location:
        return True  # Unknown — might be UK, the door decides

    text = location.strip()
    # Markup or prose is not a location. Hand it to the door untouched.
    if "<" in text or ">" in text or "\n" in text or len(text) > 120:
        return True
    return not names_foreign_place(text)


class BaseJobSource(ABC):
    name: str = "base"
    category: str = "unknown"  # keyed_api, free_json, ats, rss, scraper, other
    #
    # Pillar 2 Batch 2.4 — domain routing.
    # `DOMAINS` declares the professional domains a source primarily covers.
    # `_build_sources()` filters instances so a user with {"healthcare"} in
    # their classified domain set does not spin up `bcs_jobs` (tech-only),
    # etc. Sources tagged with "general" always run for every user — that's
    # the base class default. Subclasses that cover a specific domain should
    # override this attribute (tech, healthcare, academia, education,
    # climate, etc.). A single source may cover multiple domains; a
    # zero-profile user (no CV) bypasses the filter and gets every source.
    DOMAINS: set[str] = {"general"}

    def __init__(
        self, session: aiohttp.ClientSession, search_config: Optional[SearchConfig] = None
    ) -> None:
        self._session = session
        self._search_config = search_config
        cfg = RATE_LIMITS.get(self.name, {"concurrent": 2, "delay": 1.0})
        self._rate_limiter = RateLimiter(concurrent=cfg["concurrent"], delay=cfg["delay"])
        self._conditional_cache = ConditionalCache()

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
    def search_titles(self) -> list[str]:
        """The titles this source may put in a search request.

        Distinct from `job_titles`, which is the scorer's EVIDENCE list and
        holds raw CV strings ("… - R&D Department", "… (SDET)", bare "Intern")
        that no job board indexes. Falls back to `job_titles` so a config built
        before the split — or a no-profile default config — behaves exactly as
        it did.
        """
        if self._search_config is not None and self._search_config.search_titles:
            return self._search_config.search_titles
        return self.job_titles

    @property
    def search_queries(self) -> list[str]:
        if self._search_config is not None and self._search_config.search_queries:
            return self._search_config.search_queries
        return []

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        """Build request headers with User-Agent default."""
        h = {"User-Agent": USER_AGENT}
        if extra:
            h.update(extra)
        return h

    @abstractmethod
    async def fetch_jobs(self) -> list[Job]:
        ...

    async def _request(
        self, method: str, url: str, *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        as_text: bool = False,
    ) -> Optional[Union[dict[str, Any], list[Any], str]]:
        """Shared retry/rate-limit logic for all HTTP methods."""
        exceptions: tuple[type[BaseException], ...] = (aiohttp.ClientError, asyncio.TimeoutError)
        if not as_text:
            exceptions = (*exceptions, json.JSONDecodeError)

        for attempt in range(MAX_RETRIES):
            await self._rate_limiter.acquire()
            try:
                # dict[str, Any]: the values are a heterogeneous mix (headers dict,
                # ClientTimeout, params, json body) splatted into ClientSession.request —
                # Any keeps mypy from checking the splat against every keyword param.
                kwargs: dict[str, Any] = {
                    "headers": self._headers(headers),
                    "timeout": aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
                }
                if method == "GET":
                    kwargs["params"] = params
                else:
                    kwargs["json"] = body or {}

                async with self._session.request(method, url, **kwargs) as resp:
                    if resp.status in _NO_RETRY_STATUSES:
                        # Never log the request URL — keyed sources embed
                        # app_key/api_key in the query string, and these lines go
                        # to on-disk logs (CodeQL: clear-text-logging). self.name
                        # already identifies which source failed.
                        if resp.status in _AUTH_FAIL_STATUSES:
                            logger.warning("[%s] HTTP %s — likely an expired/invalid API key",
                                           self.name, resp.status)
                        else:
                            logger.debug("[%s] HTTP %s (no-retry status)", self.name, resp.status)
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
                        logger.warning("[%s] HTTP %s", self.name, resp.status)  # no URL — may embed api_key
                        if attempt < MAX_RETRIES - 1:
                            await asyncio.sleep(RETRY_BACKOFF[attempt])
                            continue
                        return None
                    if as_text:
                        return await resp.text()
                    json_body: Union[dict[str, Any], list[Any]] = await resp.json(content_type=None)
                    return json_body
            except exceptions as e:
                logger.warning("[%s] Request error: %s", self.name, e)
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(RETRY_BACKOFF[attempt])
            finally:
                self._rate_limiter.release()
        return None

    async def _get_json(self, url: str, params: dict[str, Any] | None = None,
                        headers: dict[str, str] | None = None) -> dict[str, Any] | list[Any] | None:
        # cast: _request's return type also covers `str` (the as_text=True path),
        # which this GET-JSON call never takes — narrow it back for callers.
        return cast(
            Optional[Union[dict[str, Any], list[Any]]],
            await self._request("GET", url, params=params, headers=headers),
        )

    async def _post_json(self, url: str, body: dict[str, Any] | None = None,
                         headers: dict[str, str] | None = None) -> dict[str, Any] | list[Any] | None:
        # cast: see _get_json — narrows _request's `str`-inclusive union back down.
        return cast(
            Optional[Union[dict[str, Any], list[Any]]],
            await self._request("POST", url, body=body, headers=headers),
        )

    async def _get_text(self, url: str, params: dict[str, Any] | None = None,
                        headers: dict[str, str] | None = None) -> str | None:
        # cast: _request's return type also covers dict/list (the JSON path),
        # which this GET-text call (as_text=True) never takes.
        return cast(
            Optional[str],
            await self._request("GET", url, params=params, headers=headers, as_text=True),
        )

    async def _get_json_conditional(self, url: str, params: dict[str, Any] | None = None,
                                     headers: dict[str, str] | None = None) -> dict[str, Any] | list[Any] | None:
        """Conditional GET returning JSON — see :meth:`_conditional_fetch`."""
        # cast: _conditional_fetch's return type also covers `str` (the
        # as_text=True path), which this call (as_text=False) never takes.
        return cast(
            Optional[Union[dict[str, Any], list[Any]]],
            await self._conditional_fetch(url, params=params, headers=headers, as_text=False),
        )

    async def _get_text_conditional(self, url: str, params: dict[str, Any] | None = None,
                                     headers: dict[str, str] | None = None) -> str | None:
        """Conditional GET returning text (RSS/XML).

        Batch 3.5.3 sibling of :meth:`_get_json_conditional`; same
        semantics with ``resp.text()`` instead of ``resp.json()``.
        """
        # cast: _conditional_fetch's return type also covers dict/list (the
        # JSON path), which this call (as_text=True) never takes.
        return cast(
            Optional[str],
            await self._conditional_fetch(url, params=params, headers=headers, as_text=True),
        )

    async def _conditional_fetch(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        as_text: bool = False,
    ) -> dict[str, Any] | list[Any] | str | None:
        """Shared body for the conditional JSON/text helpers.

        On first call, captures any ETag/Last-Modified header returned by
        the server. On subsequent calls, sends If-None-Match /
        If-Modified-Since so the server can reply 304 Not Modified; we
        then return the cached body without re-parsing. Zero-body 304s
        preserve bandwidth and parse cost for sources that change
        infrequently (ATS boards between polls, RSS feeds with honest
        Last-Modified, etc.).

        Falls back to a plain GET when the server provides no validator.
        """
        cache_key = (url, tuple(sorted((params or {}).items())))
        entry = self._conditional_cache.get(cache_key)
        extra_headers = dict(headers or {})
        if entry:
            if entry.etag:
                extra_headers["If-None-Match"] = entry.etag
            if entry.last_modified:
                extra_headers["If-Modified-Since"] = entry.last_modified

        await self._rate_limiter.acquire()
        try:
            # dict[str, Any]: same reason as in _request — heterogeneous values
            # splatted into ClientSession.request.
            kwargs: dict[str, Any] = {
                "headers": self._headers(extra_headers),
                "timeout": aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
                "params": params,
            }
            async with self._session.request("GET", url, **kwargs) as resp:
                if resp.status == 304 and entry is not None:
                    logger.debug("[%s] 304 Not Modified — using cached body for %s",
                                 self.name, url)
                    # cast: CachedEntry.body is stored as Any (it can hold the
                    # dict/list/str shapes of any past response) — narrow it
                    # back to this method's declared return type.
                    return cast(Union[dict[str, Any], list[Any], str, None], entry.body)
                if resp.status >= 400:
                    logger.warning("[%s] HTTP %s", self.name, resp.status)  # no URL — may embed api_key
                    return None
                body = (
                    await resp.text() if as_text
                    else await resp.json(content_type=None)
                )
                etag = resp.headers.get("ETag")
                last_modified = resp.headers.get("Last-Modified")
                if etag or last_modified:
                    self._conditional_cache.set(
                        cache_key,
                        CachedEntry(body=body, etag=etag, last_modified=last_modified),
                    )
                return body
        except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError) as e:
            logger.warning("[%s] conditional request error: %s", self.name, e)
            return None
        finally:
            self._rate_limiter.release()
