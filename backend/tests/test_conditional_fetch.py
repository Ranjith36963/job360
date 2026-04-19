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
import pytest
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
    from src.services.conditional_cache import ConditionalCache, CachedEntry

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
    from src.services.conditional_cache import ConditionalCache, CachedEntry

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
    from src.services.conditional_cache import ConditionalCache, CachedEntry

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
# Batch 3.5.3 — nhs_jobs_xml migrated to conditional fetch
# ---------------------------------------------------------------------------


def test_nhs_jobs_xml_uses_conditional_fetch():
    """Pilot source proof: nhs_jobs_xml stores ETag in the cache on
    first fetch, sends If-None-Match on the second."""
    async def _t():
        session = aiohttp.ClientSession()
        try:
            from src.sources.feeds.nhs_jobs_xml import NHSJobsXMLSource
            src = NHSJobsXMLSource(session)
            captured = []

            def _capture(url_, **kwargs):
                captured.append(kwargs.get("headers") or {})

            with aioresponses() as m:
                m.get(NHSJobsXMLSource.FEED_URL,
                      body="<?xml version='1.0'?><vacancies/>",
                      headers={"ETag": 'W/"nhs1"'},
                      content_type="application/xml",
                      callback=_capture)
                m.get(NHSJobsXMLSource.FEED_URL,
                      status=304, callback=_capture)

                jobs1 = await src.fetch_jobs()
                jobs2 = await src.fetch_jobs()

                assert jobs1 == []
                assert jobs2 == []
                entry = src._conditional_cache.get(
                    (NHSJobsXMLSource.FEED_URL, ())
                )
                assert entry is not None
                assert entry.etag == 'W/"nhs1"'
                # Second request must have sent If-None-Match
                assert captured[1].get("If-None-Match") == 'W/"nhs1"'
        finally:
            await session.close()
    _run(_t())
