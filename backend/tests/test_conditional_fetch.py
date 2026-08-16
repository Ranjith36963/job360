"""Tests for BaseJobSource conditional-fetch layer (ETag / Last-Modified).

Verifies that sources can opt into bandwidth-saving conditional GETs.
Per pillar_3_batch_3.md §"Conditional fetching can cut bandwidth 60-90%":
many servers honour `If-None-Match` / `If-Modified-Since` even when
their API docs don't advertise it.

Contract: `BaseJobSource._get_json_conditional(url, ...)` returns the
body from the server on first fetch (storing ETag/Last-Modified) and
returns the cached body on subsequent 304 responses.
"""
import asyncio

import aiohttp
from aioresponses import aioresponses

from src.sources.base import BaseJobSource


def _run(coro):
    asyncio.new_event_loop().run_until_complete(coro)


class _Probe(BaseJobSource):
    """Minimal concrete subclass for exercising BaseJobSource helpers."""
    name = "probe"
    category = "free_json"

    async def fetch_jobs(self):
        return []


def test_first_fetch_stores_etag():
    async def _t():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(
                    "https://api.example.test/jobs",
                    payload={"jobs": [{"id": 1}]},
                    headers={"ETag": 'W/"abc-123"'},
                )
                src = _Probe(session)
                body = await src._get_json_conditional("https://api.example.test/jobs")
                assert body == {"jobs": [{"id": 1}]}
                entry = src._conditional_cache.get(
                    ("https://api.example.test/jobs", ())
                )
                assert entry is not None
                assert entry.etag == 'W/"abc-123"'
        finally:
            await session.close()
    _run(_t())


def test_second_fetch_sends_if_none_match_and_gets_304_returns_cached_body():
    async def _t():
        session = aiohttp.ClientSession()
        try:
            url = "https://api.example.test/jobs"
            captured_headers = []

            def _capture(url_, **kwargs):
                captured_headers.append(kwargs.get("headers") or {})

            with aioresponses() as m:
                # First response: 200 with ETag + body
                m.get(url, payload={"jobs": [{"id": 1}]},
                      headers={"ETag": 'W/"v1"'}, callback=_capture)
                # Second response: 304 with no body
                m.get(url, status=304, callback=_capture)

                src = _Probe(session)
                first = await src._get_json_conditional(url)
                second = await src._get_json_conditional(url)

                assert first == {"jobs": [{"id": 1}]}
                # 304 → returns the cached body, not None
                assert second == {"jobs": [{"id": 1}]}

                # Second call must have sent If-None-Match
                assert len(captured_headers) == 2
                assert captured_headers[1].get("If-None-Match") == 'W/"v1"'
        finally:
            await session.close()
    _run(_t())


def test_last_modified_roundtrip():
    async def _t():
        session = aiohttp.ClientSession()
        try:
            url = "https://api.example.test/feed.xml"
            captured_headers = []

            def _capture(url_, **kwargs):
                captured_headers.append(kwargs.get("headers") or {})

            with aioresponses() as m:
                m.get(url, payload={"ok": True},
                      headers={"Last-Modified": "Wed, 15 Jan 2026 12:00:00 GMT"},
                      callback=_capture)
                m.get(url, status=304, callback=_capture)

                src = _Probe(session)
                first = await src._get_json_conditional(url)
                second = await src._get_json_conditional(url)

                assert first == {"ok": True}
                assert second == {"ok": True}
                assert (captured_headers[1].get("If-Modified-Since")
                        == "Wed, 15 Jan 2026 12:00:00 GMT")
        finally:
            await session.close()
    _run(_t())


def test_malformed_json_returns_none_not_raise():
    """S6 fix (docs/FABLE_FINDINGS.md): `_conditional_fetch`'s except only
    caught `(aiohttp.ClientError, asyncio.TimeoutError)` even though it calls
    `resp.json()` for the JSON-flavored helper — unlike `_request`, which
    already includes `json.JSONDecodeError`. A malformed/HTML-error body on
    a conditional-fetch source used to raise out of `fetch_jobs()` uncaught.
    Must now degrade gracefully to `None`."""
    async def _t():
        session = aiohttp.ClientSession()
        try:
            url = "https://api.example.test/broken-json"
            with aioresponses() as m:
                # Server returns a non-JSON body (e.g. an HTML error page)
                # but with a JSON content-type, forcing resp.json() to raise.
                m.get(url, body="<html>502 Bad Gateway</html>",
                      content_type="application/json")
                src = _Probe(session)
                body = await src._get_json_conditional(url)
                assert body is None
        finally:
            await session.close()
    _run(_t())


def test_no_cache_when_no_validator_header():
    """Server returned neither ETag nor Last-Modified → nothing cached."""
    async def _t():
        session = aiohttp.ClientSession()
        try:
            url = "https://api.example.test/nocache"
            with aioresponses() as m:
                m.get(url, payload={"jobs": []})
                src = _Probe(session)
                body = await src._get_json_conditional(url)
                assert body == {"jobs": []}
                entry = src._conditional_cache.get((url, ()))
                assert entry is None
        finally:
            await session.close()
    _run(_t())


# ---------------------------------------------------------------------------
# Batch 3.5.3 — aioresponses 304 primitive sanity check
# ---------------------------------------------------------------------------


def test_aioresponses_304_primitive_works():
    """Sanity: aioresponses can mock a 304 response + callback captures
    outbound headers. This pins the test-primitive choice for the rest
    of the conditional-fetch tests; if aioresponses stopped supporting
    either feature, we'd need to switch to httpx_mock or a session
    monkeypatch. Currently works out of the box.
    """
    async def _t():
        session = aiohttp.ClientSession()
        try:
            url = "https://example.test/probe"
            captured = []

            def _capture(url_, **kwargs):
                captured.append(kwargs.get("headers") or {})

            with aioresponses() as m:
                m.get(url, body="hello",
                      headers={"ETag": '"tag1"'},
                      content_type="text/plain",
                      callback=_capture)
                m.get(url, status=304, callback=_capture)

                async with session.get(url) as r1:
                    assert r1.status == 200
                    assert r1.headers.get("ETag") == '"tag1"'

                async with session.get(url, headers={"If-None-Match": '"tag1"'}) as r2:
                    assert r2.status == 304

                # The 304 path does NOT run the callback (aioresponses
                # calls the callback on request-setup; status is server
                # side). Verify only that the first call captured the
                # request headers — that's what we actually rely on.
                assert len(captured) >= 1
        finally:
            await session.close()
    _run(_t())


# ---------------------------------------------------------------------------
# Batch 3.5.3 — _get_text_conditional (sibling of _get_json_conditional)
# ---------------------------------------------------------------------------


def test_get_text_conditional_roundtrip_with_etag():
    """RSS/XML sources need a text-returning conditional helper."""
    async def _t():
        session = aiohttp.ClientSession()
        try:
            url = "https://example.test/rss.xml"
            captured = []

            def _capture(url_, **kwargs):
                captured.append(kwargs.get("headers") or {})

            with aioresponses() as m:
                m.get(url, body="<rss>one</rss>",
                      headers={"ETag": 'W/"v1"'},
                      content_type="application/xml",
                      callback=_capture)
                m.get(url, status=304, callback=_capture)

                src = _Probe(session)
                first = await src._get_text_conditional(url)
                second = await src._get_text_conditional(url)

                assert first == "<rss>one</rss>"
                assert second == "<rss>one</rss>"  # cached body, not re-parsed
                assert captured[1].get("If-None-Match") == 'W/"v1"'
        finally:
            await session.close()
    _run(_t())


def test_get_text_conditional_no_validator_does_not_cache():
    async def _t():
        session = aiohttp.ClientSession()
        try:
            url = "https://example.test/nocache.xml"
            with aioresponses() as m:
                m.get(url, body="<rss/>", content_type="application/xml")
                src = _Probe(session)
                body = await src._get_text_conditional(url)
                assert body == "<rss/>"
                entry = src._conditional_cache.get((url, ()))
                assert entry is None
        finally:
            await session.close()
    _run(_t())


# ---------------------------------------------------------------------------
# Batch 3.5.3 — Cache eviction at 256-entry FIFO boundary
# ---------------------------------------------------------------------------


def test_cache_eviction_at_fifo_boundary():
    """Evicted entry must re-fetch 200, not 304."""
    from src.services.conditional_cache import CachedEntry, ConditionalCache

    cache = ConditionalCache(max_entries=3)
    cache.set(("a", ()), CachedEntry(body="A", etag='"a"'))
    cache.set(("b", ()), CachedEntry(body="B", etag='"b"'))
    cache.set(("c", ()), CachedEntry(body="C", etag='"c"'))
    assert len(cache) == 3
    # Setting a fourth evicts the oldest ("a")
    cache.set(("d", ()), CachedEntry(body="D", etag='"d"'))
    assert len(cache) == 3
    assert cache.get(("a", ())) is None
    assert cache.get(("d", ())) is not None


# ---------------------------------------------------------------------------
# Batch 3.5.3 — Cache hit/miss instrumentation
# ---------------------------------------------------------------------------


def test_cache_metrics_count_hits_and_misses():
    """get() bumps hit_count on success and miss_count on miss;
    get_metrics() exposes {hits, misses, size}."""
    from src.services.conditional_cache import CachedEntry, ConditionalCache

    cache = ConditionalCache(max_entries=256)
    # 5 lookups: 1 miss, then set, then 4 hits
    assert cache.get(("k", ())) is None  # miss #1
    cache.set(("k", ()), CachedEntry(body="X"))
    assert cache.get(("k", ())) is not None  # hit #1
    assert cache.get(("k", ())) is not None  # hit #2
    assert cache.get(("k", ())) is not None  # hit #3
    assert cache.get(("k", ())) is not None  # hit #4

    metrics = cache.get_metrics()
    assert metrics["hits"] == 4
    assert metrics["misses"] == 1
    assert metrics["size"] == 1


def test_cache_reset_metrics():
    """reset_metrics() zeroes the counters for test isolation."""
    from src.services.conditional_cache import CachedEntry, ConditionalCache

    cache = ConditionalCache()
    cache.get(("k", ()))  # miss
    cache.set(("k", ()), CachedEntry(body="X"))
    cache.get(("k", ()))  # hit
    assert cache.get_metrics()["hits"] == 1

    cache.reset_metrics()
    m = cache.get_metrics()
    assert m["hits"] == 0 and m["misses"] == 0
    # size stays (reset metrics, not contents)
    assert m["size"] == 1


# ---------------------------------------------------------------------------
# Batch 3.5.3 — the nhs_jobs_xml pilot test was removed 2026-08-10 with the
# source itself (feed retired, serves HTML). No production source opts into
# conditional fetch right now; the _Probe tests above still pin the contract
# so the next opt-in source inherits working behaviour.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# S9 — auth failures (401/403) logged louder than routine 404/422
# ---------------------------------------------------------------------------


def test_auth_failure_statuses_logged_at_warning(caplog):
    """S9 — a 401/403 from upstream (expired/bad API key) must be visible at
    WARNING, not buried at DEBUG like a routine 404/422."""
    import logging

    # Use a URL carrying a secret api_key in the query string — keyed sources
    # (Adzuna/SerpApi) do exactly this — to prove the redaction (CodeQL fix).
    _url = "https://api.example.test/401?api_key=SUPERSECRET123"

    async def _t():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get(_url, status=401)
                src = _Probe(session)
                with caplog.at_level(logging.WARNING, logger="job360.sources"):
                    result = await src._request("GET", _url)
            assert result is None
        finally:
            await session.close()
    _run(_t())

    warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("401" in r.getMessage() for r in warning_records), (
        f"expected a WARNING-level log for the 401 status; saw {[r.getMessage() for r in caplog.records]}"
    )
    # The api_key MUST NOT leak into the log line (clear-text-logging / CodeQL).
    for r in caplog.records:
        assert "SUPERSECRET123" not in r.getMessage(), "api_key leaked into the source log"
        assert "api_key" not in r.getMessage()


def test_not_found_status_stays_at_debug(caplog):
    """S9 (negative control) — a routine 404 must NOT be promoted to WARNING;
    only 401/403 (auth failures) get the louder log level."""
    import logging

    async def _t():
        session = aiohttp.ClientSession()
        try:
            with aioresponses() as m:
                m.get("https://api.example.test/404", status=404)
                src = _Probe(session)
                with caplog.at_level(logging.DEBUG, logger="job360.sources"):
                    result = await src._request("GET", "https://api.example.test/404")
            assert result is None
        finally:
            await session.close()
    _run(_t())

    warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert not any("404" in r.getMessage() for r in warning_records), (
        f"a 404 must stay at DEBUG, not be promoted to WARNING; saw {[r.getMessage() for r in warning_records]}"
    )
    _run(_t())
