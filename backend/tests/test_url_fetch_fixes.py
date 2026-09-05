"""Pins for the adversarial-review fixes to URL fetch (slice 3,
docs/plans/2026-09-04-url-fetch/spec.md). One group of tests per bug id from
the review, named so a regression here points straight back at the finding
it re-opened.

Deliberately does NOT import a fixture from test_url_fetch.py or
test_url_fetch_guard.py — the project rule (see feedback_ archive) is that
importing a test fixture across modules can double-register it and break
per-test Postgres schema isolation. The small autouse fixture below is a
local, self-contained duplicate of the essentials test_url_fetch.py's own
``_url_fetch_defaults`` sets, not a shared import.
"""
from __future__ import annotations

import asyncio
import socket
import time
from ipaddress import ip_address
from typing import Any, Optional

import pytest
from aioresponses import aioresponses

FETCH_URL_ROUTE = "/api/jobs/fetch-url"


def _record(host: str, addr: str = "93.184.216.34", family: int = socket.AF_INET) -> dict[str, Any]:
    return {"hostname": host, "host": addr, "port": 0, "family": family, "proto": 0, "flags": 0}


def _resolve_map(overrides: Optional[dict[str, str]] = None, default: str = "93.184.216.34"):
    overrides = overrides or {}

    async def _resolve(host: str, port: int = 0, family: int = 0) -> list[dict[str, Any]]:
        return [_record(host, overrides.get(host, default))]

    return _resolve


@pytest.fixture(autouse=True)
def _fixes_defaults(monkeypatch):
    """Generous, deterministic defaults local to this file only."""
    from src.core import settings

    monkeypatch.setattr(settings, "URL_FETCH_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "URL_FETCH_MAX_BYTES", 2 * 1024 * 1024, raising=False)
    monkeypatch.setattr(settings, "URL_FETCH_TIMEOUT_S", 10, raising=False)
    monkeypatch.setattr(settings, "URL_FETCH_TOTAL_BUDGET_S", 20, raising=False)
    monkeypatch.setattr(settings, "URL_FETCH_EXTRACT_BUDGET_S", 3, raising=False)
    monkeypatch.setattr(settings, "URL_FETCH_MAX_REDIRECTS", 5, raising=False)
    monkeypatch.setattr(settings, "URL_FETCH_MAX_HTML_DEPTH", 200, raising=False)
    monkeypatch.setattr(settings, "URL_FETCH_MAX_PER_MINUTE", 1000, raising=False)
    monkeypatch.setattr(settings, "URL_FETCH_MAX_PER_HOUR", 1000, raising=False)
    monkeypatch.setattr(settings, "URL_FETCH_MAX_PER_HOUR_GLOBAL", 100_000, raising=False)
    monkeypatch.setattr(
        settings, "URL_FETCH_ALLOWED_CONTENT_TYPES", ("text/html", "application/xhtml+xml"), raising=False
    )
    monkeypatch.setattr(settings, "URL_FETCH_EXTRA_DENY_NETS", (), raising=False)
    monkeypatch.setattr(settings, "URL_FETCH_ALLOW_NETS", (), raising=False)

    from src.services.fetch import fetcher

    monkeypatch.setattr(fetcher, "DEFAULT_RESOLVE", _resolve_map(), raising=False)
    monkeypatch.setattr(fetcher, "DEFAULT_CLOCK", time.monotonic, raising=False)
    monkeypatch.setattr(fetcher, "VERIFY_PEERNAME_AFTER_CONNECT", False, raising=False)


# ---------------------------------------------------------------------------
# B1 — screen_host sat outside the try; a DNS failure (socket.gaierror) on a
# typo'd host escaped the closed-outcome contract as an HTTP 500.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_b1_a_resolver_dns_failure_is_unreachable_not_500(authenticated_async_context, monkeypatch):
    from src.services.fetch import fetcher

    async def _raising_resolve(host: str, port: int = 0, family: int = 0) -> list[dict[str, Any]]:
        raise socket.gaierror(-2, "Name or service not known")

    monkeypatch.setattr(fetcher, "DEFAULT_RESOLVE", _raising_resolve)
    url = "https://typo-that-does-not-resolve.example.test/job"
    async with authenticated_async_context() as client:
        resp = await client.post(FETCH_URL_ROUTE, json={"url": url})
    assert resp.status_code == 200, resp.text
    assert resp.json()["outcome"] == "unreachable"


@pytest.mark.asyncio
async def test_b1_a_resolver_timeout_is_the_timeout_outcome_not_500(authenticated_async_context, monkeypatch):
    from src.services.fetch import fetcher

    async def _hanging_resolve(host: str, port: int = 0, family: int = 0) -> list[dict[str, Any]]:
        raise asyncio.TimeoutError()

    monkeypatch.setattr(fetcher, "DEFAULT_RESOLVE", _hanging_resolve)
    url = "https://slow-dns.example.test/job"
    async with authenticated_async_context() as client:
        resp = await client.post(FETCH_URL_ROUTE, json={"url": url})
    assert resp.status_code == 200, resp.text
    assert resp.json()["outcome"] == "timeout"


# ---------------------------------------------------------------------------
# B2 — the v6 arm of the guard had no stdlib-predicate belt at all (v4 always
# had one): six addresses the review measured passed the guard.
# ---------------------------------------------------------------------------

_B2_REVIEW_FOUND_GAPS = ["fec0::1", "64:ff9b:1::7f00:1", "2001:10::1", "::a9fe:a9fe", "::7f00:1", "5f00::1"]


@pytest.mark.parametrize("addr", _B2_REVIEW_FOUND_GAPS)
def test_b2_the_six_review_found_v6_addresses_are_now_denied(addr):
    from src.services.fetch.guard import screen_ip

    assert screen_ip(ip_address(addr)) is not None, f"{addr} should be denied"


def test_b2_the_nat64_wellknown_prefix_is_still_denied():
    from src.services.fetch.guard import screen_ip

    assert screen_ip(ip_address("64:ff9b::7f00:1")) is not None


def test_b2_public_v6_is_still_allowed_after_the_predicate_belt():
    from src.services.fetch.guard import screen_ip

    assert screen_ip(ip_address("2606:2800:220:1:248:1893:25c8:1946")) is None


# ---------------------------------------------------------------------------
# B3 — a JSON-LD ``json.loads`` caught ValueError but not RecursionError (a
# RuntimeError): a ``"["*60000`` bomb inside the script tag 500'd the route.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_b3_a_jsonld_recursion_bomb_does_not_500_the_route(authenticated_async_context):
    bomb = "[" * 60_000
    html = (
        '<html><head><script type="application/ld+json">' + bomb + "</script></head>"
        "<body><p>Genuine job text so the heuristic rung still finds something real.</p></body></html>"
    )
    url = "https://bomb.example.test/jsonld.html"
    with aioresponses() as m:
        m.get(url, status=200, body=html, headers={"Content-Type": "text/html"})
        async with authenticated_async_context() as client:
            resp = await client.post(FETCH_URL_ROUTE, json={"url": url})
    assert resp.status_code == 200, resp.text
    assert resp.json()["outcome"] == "ok"


def test_b3_the_recursion_bomb_is_caught_directly_by_extract_job_fields():
    from src.services.fetch.extract import extract_job_fields

    bomb = "[" * 60_000
    html = f'<html><head><script type="application/ld+json">{bomb}</script></head><body>hi there</body></html>'
    result = extract_job_fields(html, max_depth=200, budget_s=3.0)  # must not raise
    assert "hi there" in result.description


def test_b3_an_oversized_jsonld_block_is_skipped_before_json_loads_ever_runs(monkeypatch):
    from src.services.fetch import extract

    # Patched on extract's OWN module attribute (a plain global, read fresh
    # each call) — NOT via settings, since extract.py binds this at IMPORT
    # time (same house pattern as the pre-existing _MAX_FIELD/_MAX_TEXT).
    monkeypatch.setattr(extract, "_MAX_JSONLD_BYTES", 100, raising=False)
    oversized_but_otherwise_valid = '{"@type": "JobPosting", "title": "' + ("x" * 500) + '"}'
    html = (
        f'<html><head><script type="application/ld+json">{oversized_but_otherwise_valid}</script></head>'
        "<body>fallback text</body></html>"
    )
    result = extract.extract_job_fields(html, max_depth=200, budget_s=3.0)
    assert result.source_hint != "json_ld", "an over-cap block must be skipped, never parsed"


# ---------------------------------------------------------------------------
# B4 — extraction ran inline on the event loop (~3s stall); a single text
# chunk was duplicated into every open container frame (up to max_depth).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_b4_extract_job_fields_called_directly_on_the_loop_raises_loop_block_error():
    from src.services.fetch.extract import extract_job_fields
    from src.utils.loop_guard import LoopBlockError

    with pytest.raises(LoopBlockError):
        extract_job_fields("<html><body>hi</body></html>", max_depth=10, budget_s=1.0)


@pytest.mark.asyncio
async def test_b4_the_route_runs_extraction_via_asyncio_to_thread(authenticated_async_context, monkeypatch):
    calls = {"n": 0}
    real_to_thread = asyncio.to_thread

    async def _spy_to_thread(fn, *args, **kwargs):
        calls["n"] += 1
        return await real_to_thread(fn, *args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _spy_to_thread)
    url = "https://careers.example.test/role"
    with aioresponses() as m:
        m.get(
            url, status=200, body="<html><body>hello there, a real job posting</body></html>",
            headers={"Content-Type": "text/html"},
        )
        async with authenticated_async_context() as client:
            resp = await client.post(FETCH_URL_ROUTE, json={"url": url})
    assert resp.status_code == 200, resp.text
    assert resp.json()["outcome"] == "ok"
    assert calls["n"] == 1, "extraction must run through asyncio.to_thread exactly once"


def test_b4_heuristic_parser_caps_text_duplication_across_nested_frames():
    from src.services.fetch.extract import _MAX_TEXT_ACCUM_FRAMES, _HeuristicParser

    depth = 200
    chunk = "y" * 1000
    opening = "<html><body>" + ("<div>" * depth)
    parser = _HeuristicParser(max_depth=depth, budget_s=3.0)
    parser.feed(opening + chunk)  # every tag left open on purpose — frames stay live
    total_copied = sum(len("".join(f.text_parts)) for f in parser._frames)
    # Before the fix EVERY one of the (up to `depth`) open frames copied the
    # chunk; now only the innermost _MAX_TEXT_ACCUM_FRAMES do.
    assert total_copied <= _MAX_TEXT_ACCUM_FRAMES * len(chunk)
    assert total_copied < depth * len(chunk)


# ---------------------------------------------------------------------------
# B6 — GuardedResolver's approved-set keyed on aiohttp's RAW host: a raw-IP
# URL never reaches the resolver at all (aiohttp bypasses it for a literal
# IP host), and a trailing-dot host mismatched the normalised key.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_b6_a_raw_ip_literal_url_is_not_falsely_ssrf_denied(authenticated_async_context, monkeypatch):
    from src.services.fetch import fetcher

    # verify_peer=True is what exercises the bug (with it off — this file's
    # default, matching test_url_fetch.py's own reasoning about aioresponses
    # having no real transport — the peer check never runs at all).
    monkeypatch.setattr(fetcher, "VERIFY_PEERNAME_AFTER_CONNECT", True)
    # aioresponses never drives the real TCPConnector/GuardedResolver, so
    # `guarded_resolver.approved_for(...)` is always empty here — standing in
    # for aiohttp's real "skip the resolver for an already-numeric host"
    # behaviour, which leaves it exactly as empty in production. `_peer_ip`
    # also reads nothing real under aioresponses (no real socket), so it is
    # stubbed to the address actually fetched.
    monkeypatch.setattr(fetcher, "_peer_ip", lambda resp: "93.184.216.34")

    url = "https://93.184.216.34/job"
    with aioresponses() as m:
        m.get(url, status=200, body="<html><body>ok</body></html>", headers={"Content-Type": "text/html"})
        async with authenticated_async_context() as client:
            resp = await client.post(FETCH_URL_ROUTE, json={"url": url})
    assert resp.status_code == 200, resp.text
    assert resp.json()["outcome"] != "ssrf_denied"


async def test_b6_guarded_resolver_approved_for_survives_a_trailing_dot_mismatch():
    from src.services.fetch.guard import GuardedResolver

    async def resolve(host: str, port: int = 0, family: int = 0) -> list[dict[str, Any]]:
        return [{"hostname": host, "host": "93.184.216.34", "port": port, "family": socket.AF_INET}]

    resolver = GuardedResolver(resolve=resolve)
    try:
        # aiohttp hands the resolver whatever RAW host it parsed off the
        # URL — here, WITH a trailing dot.
        await resolver.resolve("evil.example.test.", 443, socket.AF_UNSPEC)
    finally:
        await resolver.close()

    # fetcher.py's peer check looks the host up under screen_url's OWN
    # normalised form (trailing dot stripped) — before the fix these two
    # keys never matched, so approved_for came back empty.
    approved = resolver.approved_for("evil.example.test")
    assert approved == frozenset({"93.184.216.34"})


# ---------------------------------------------------------------------------
# B7 — a per-hop timeout of the FULL URL_FETCH_TIMEOUT_S on every hop let N
# hops cost up to N*URL_FETCH_TIMEOUT_S, defeating URL_FETCH_TOTAL_BUDGET_S.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_b7_the_third_hops_per_request_timeout_shrinks_with_the_remaining_budget(
    authenticated_async_context, monkeypatch,
):
    import aiohttp

    from src.core import settings
    from src.services.fetch import fetcher

    monkeypatch.setattr(settings, "URL_FETCH_TIMEOUT_S", 10, raising=False)
    monkeypatch.setattr(settings, "URL_FETCH_TOTAL_BUDGET_S", 20, raising=False)

    # clock() is read twice per hop (the whole-journey budget check, then the
    # hop-timeout calculation) plus once more at start and once more at the
    # final "ok" for elapsed_ms. By hop 3 the clock has advanced to 18s of
    # the 20s budget, so only ~2s is left even though URL_FETCH_TIMEOUT_S
    # itself is 10s — no real sleep anywhere (spec's own "injected clock"
    # instruction).
    schedule = iter([0.0, 1.0, 2.0, 5.0, 6.0, 17.0, 18.0, 19.0])

    def fake_clock() -> float:
        try:
            return next(schedule)
        except StopIteration:  # pragma: no cover - safety net only
            return 19.0

    monkeypatch.setattr(fetcher, "DEFAULT_CLOCK", fake_clock)

    recorded_timeouts: list[float] = []
    real_get = aiohttp.ClientSession.get

    def _spy_get(self, url, **kwargs):
        recorded_timeouts.append(kwargs["timeout"].total)
        return real_get(self, url, **kwargs)

    monkeypatch.setattr(aiohttp.ClientSession, "get", _spy_get)

    hop1 = "https://hop1.example.test/a"
    hop2 = "https://hop2.example.test/b"
    hop3 = "https://hop3.example.test/c"
    with aioresponses() as m:
        m.get(hop1, status=302, headers={"Location": hop2})
        m.get(hop2, status=302, headers={"Location": hop3})
        m.get(hop3, status=200, body="<html><body>ok</body></html>", headers={"Content-Type": "text/html"})
        async with authenticated_async_context() as client:
            resp = await client.post(FETCH_URL_ROUTE, json={"url": hop1})
    assert resp.status_code == 200, resp.text
    assert resp.json()["outcome"] == "ok"
    assert len(recorded_timeouts) == 3
    assert recorded_timeouts[0] == pytest.approx(10.0)
    assert recorded_timeouts[1] == pytest.approx(10.0)
    assert recorded_timeouts[2] == pytest.approx(2.0)
    assert recorded_timeouts[2] < recorded_timeouts[0], "the third hop's own budget must shrink"
