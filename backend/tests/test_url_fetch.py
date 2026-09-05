"""Frozen tests for the URL-fetch route (docs/plans/2026-09-04-url-fetch/spec.md,
items 16-37): ``POST /api/jobs/fetch-url`` — extraction, redirects, caps,
rate limits, the MCP absence, the audit line, and the closed outcome enum.

These tests pin the route's behaviour: extraction, redirects, size/time
caps, per-user + global rate limits, the kill switch, the MCP absence, the
audit line's host-only contract, and the closed outcome enum — see
``src/services/fetch/fetcher.py`` / ``outcomes.py`` and the route itself
(``src/api/routes/bring.py::fetch_url_route``) for the implementation these
freeze in place.

WHY A RESOLVER/CLOCK SEAM ON THE FETCHER MODULE
------------------------------------------------
The route only ever takes ``{"url": str}`` over HTTP — there is no channel to
inject a fake DNS resolver or a fake clock through the request body, and
``aioresponses`` mocks aiohttp's request execution, NOT the guard's own
``resolve()`` call (spec R4/R6: the fetcher screens host+redirect targets
itself, independent of aiohttp's connector). So the fetcher module is assumed
to expose two live-read module globals the route consults with no argument:

    fetcher.DEFAULT_RESOLVE: ResolveFn        # def default: real DNS, lazy-imported
    fetcher.DEFAULT_CLOCK: Callable[[], float]  # def default: time.monotonic

Read INSIDE ``fetch_url`` at call time (never bound as a default argument
value), exactly like ``rate_limit.py``'s ``_redis_client()`` reads
``settings.RATE_LIMIT_REDIS`` live — so ``monkeypatch.setattr(fetcher,
"DEFAULT_RESOLVE", fake)`` takes effect on the very next call. This file's
autouse fixture (``_url_fetch_defaults``) sets both to safe, deterministic
fakes for every test; individual tests override the parts they need.

WHY ``VERIFY_PEERNAME_AFTER_CONNECT`` IS FORCED OFF HERE
----------------------------------------------------------
plan.md's own risk note: "the peername re-check may be None under
aioresponses. A mocked response has no real transport... the branch is driven
by an explicit injected flag, never by sniffing whether we are under test."
``fetcher.VERIFY_PEERNAME_AFTER_CONNECT`` is that flag (default True in
production). The mechanism itself — a real resolver, a real peer, a mismatch
denied — is proven at the unit level in ``test_url_fetch_guard.py`` (items 8
and 9); this file is not re-proving it, it is proving redirects/caps/
extraction/rate-limits, which do not need a real socket.
"""
from __future__ import annotations

import gzip
import logging
import socket
import time
from typing import Any, Optional

import pytest
from aioresponses import aioresponses
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

FETCH_URL_ROUTE = "/api/jobs/fetch-url"

# ---------------------------------------------------------------------------
# Shared fakes — no DNS, no network, no real sleep.
# ---------------------------------------------------------------------------


def _record(host: str, addr: str = "93.184.216.34", family: int = socket.AF_INET) -> dict[str, Any]:
    return {"hostname": host, "host": addr, "port": 0, "family": family, "proto": 0, "flags": 0}


def resolve_map(overrides: Optional[dict[str, str]] = None, default: str = "93.184.216.34"):
    """A fake ``ResolveFn``: every host resolves to ``default`` unless named in
    ``overrides`` (host -> address). Public by default so ordinary fixture
    hostnames pass the guard without needing a real DNS answer.
    """
    overrides = overrides or {}

    async def _resolve(host: str, port: int = 0, family: int = 0) -> list[dict[str, Any]]:
        return [_record(host, overrides.get(host, default))]

    return _resolve


def jumping_clock(start: float = 0.0, jump_to: float = 10_000.0):
    """A fake clock: the first call reports ``start``; every call after
    reports ``jump_to``. Lets a test prove a time-budget check fires WITHOUT
    a real sleep (spec: "injected clock, no real sleep").
    """
    calls = {"n": 0}

    def _clock() -> float:
        calls["n"] += 1
        return start if calls["n"] == 1 else jump_to

    return _clock


@pytest.fixture(autouse=True)
def _url_fetch_defaults(monkeypatch):
    """Generous, deterministic defaults for every test in this file.

    Individual tests override exactly the setting(s) their case is about —
    this fixture exists so the other dozen settings don't have to be repeated
    in every test body. ``raising=False`` throughout: these attributes do not
    exist on ``settings``/``fetcher`` yet (that is the whole point of RED).
    """
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

    from src.services.fetch import fetcher  # noqa: PLC0415 — the module under RED

    monkeypatch.setattr(fetcher, "DEFAULT_RESOLVE", resolve_map(), raising=False)
    monkeypatch.setattr(fetcher, "DEFAULT_CLOCK", time.monotonic, raising=False)
    monkeypatch.setattr(fetcher, "VERIFY_PEERNAME_AFTER_CONNECT", False, raising=False)


def _fixture(name: str) -> str:
    from pathlib import Path

    return (Path(__file__).parent / "fixtures" / "url_fetch" / name).read_text(encoding="utf-8")


async def _second_user_client(email: str = "second@example.com"):
    """A logged-in AsyncClient for a SECOND user on the SAME (already patched)
    DB_PATH — proves the rate limit is keyed by user, not by client/IP. Mirrors
    ``conftest.authenticated_async_context`` but registers a distinct email
    instead of reusing "test@example.com".
    """
    from src.api.main import app

    sync = TestClient(app)
    r = sync.post("/api/auth/register", json={"email": email, "password": "s3cretpassword"})
    assert r.status_code == 201, r.text
    lr = sync.post("/api/auth/login", json={"email": email, "password": "s3cretpassword"})
    assert lr.status_code == 200, lr.text
    cookie = sync.cookies.get("job360_session")
    sync.close()
    assert cookie, "second user: failed to capture session cookie"
    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", cookies={"job360_session": cookie}
    )


# ---------------------------------------------------------------------------
# 16-17: extraction ladder
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_json_ld_page_fills_all_four_fields(authenticated_async_context):
    url = "https://boards.greenhouse.io/acme/jobs/12345"
    with aioresponses() as m:
        m.get(url, status=200, body=_fixture("greenhouse_jobposting.html"), headers={"Content-Type": "text/html"})
        async with authenticated_async_context() as client:
            resp = await client.post(FETCH_URL_ROUTE, json={"url": url})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["outcome"] == "ok"
    assert body["source_hint"] == "json_ld"
    assert set(body["found"]) == {"title", "company", "location", "description"}
    # #21 — assert the VALUES, not merely that the keys are present.
    assert body["title"] == "Senior Backend Engineer"
    assert body["company"] == "Acme Corp"
    assert "London" in body["location"]
    assert "Senior Backend Engineer" in body["description"]
    assert "Postgres" in body["description"]
    assert "<" not in body["description"]  # tag-stripped, not raw HTML (S10)
    assert body["final_url"] == url
    assert body["redirects"] == 0


@pytest.mark.asyncio
async def test_a_plain_company_page_falls_back_to_the_heuristic(authenticated_async_context):
    url = "https://careers.northwind.example.test/roles/backend-engineer"
    with aioresponses() as m:
        m.get(url, status=200, body=_fixture("plain_company_page.html"), headers={"Content-Type": "text/html"})
        async with authenticated_async_context() as client:
            resp = await client.post(FETCH_URL_ROUTE, json={"url": url})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["outcome"] == "ok"
    assert body["source_hint"] == "heuristic"
    # No JSON-LD and no og:site_name on this fixture — company MUST stay empty
    # rather than guessed from nav/footer noise.
    assert body["company"] == ""
    assert "company" not in body["found"]
    assert "description" in body["found"]
    assert "Backend Engineer" in body["description"] or "warehouse" in body["description"].lower()


# ---------------------------------------------------------------------------
# 18-21: redirects
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_public_redirects_are_followed(authenticated_async_context):
    start = "https://a.example.test/start"
    mid = "https://b.example.test/mid"
    final = "https://c.example.test/final"
    with aioresponses() as m:
        m.get(start, status=302, headers={"Location": mid})
        m.get(mid, status=302, headers={"Location": final})
        m.get(final, status=200, body=_fixture("plain_company_page.html"), headers={"Content-Type": "text/html"})
        async with authenticated_async_context() as client:
            resp = await client.post(FETCH_URL_ROUTE, json={"url": start})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["outcome"] == "ok"
    assert body["redirects"] == 2
    assert body["final_url"] == final


@pytest.mark.asyncio
async def test_a_redirect_to_a_private_address_is_ssrf_denied(authenticated_async_context, monkeypatch):
    from src.services.fetch import fetcher

    start = "https://public.example.test/start"
    secret = "https://internal.example.test/secret"
    monkeypatch.setattr(fetcher, "DEFAULT_RESOLVE", resolve_map({"internal.example.test": "10.0.0.5"}))

    body_reads = {"n": 0}
    with aioresponses() as m:
        m.get(start, status=302, headers={"Location": secret})

        def _never(url, **kwargs):
            # Deliberately does not raise: a raise inside aioresponses' own
            # callback machinery can surface as a generic transport error
            # rather than as this assertion. Recording the call and checking
            # the counter afterward is the robust signal either way.
            body_reads["n"] += 1

        m.get(secret, callback=_never, status=200, body="<html><body>never</body></html>")
        async with authenticated_async_context() as client:
            resp = await client.post(FETCH_URL_ROUTE, json={"url": start})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["outcome"] == "ssrf_denied"
    assert body_reads["n"] == 0, "the denied hop's body must never be read"


@pytest.mark.asyncio
async def test_over_the_redirect_cap_is_unreachable(authenticated_async_context, monkeypatch):
    from src.core import settings

    monkeypatch.setattr(settings, "URL_FETCH_MAX_REDIRECTS", 2, raising=False)
    urls = [f"https://hop{i}.example.test/x" for i in range(5)]
    with aioresponses() as m:
        for i in range(len(urls) - 1):
            m.get(urls[i], status=302, headers={"Location": urls[i + 1]})
        m.get(urls[-1], status=200, body="<html><body>ok</body></html>", headers={"Content-Type": "text/html"})
        async with authenticated_async_context() as client:
            resp = await client.post(FETCH_URL_ROUTE, json={"url": urls[0]})
    assert resp.status_code == 200, resp.text
    assert resp.json()["outcome"] == "unreachable"


@pytest.mark.asyncio
async def test_a_redirect_cycle_is_unreachable(authenticated_async_context):
    a = "https://loop-a.example.test/x"
    b = "https://loop-b.example.test/x"
    with aioresponses() as m:
        # aioresponses replays the SAME registered response for every request
        # to a URL, so registering both directions once is enough for an
        # unbounded A<->B loop.
        m.get(a, status=302, headers={"Location": b}, repeat=True)
        m.get(b, status=302, headers={"Location": a}, repeat=True)
        async with authenticated_async_context() as client:
            resp = await client.post(FETCH_URL_ROUTE, json={"url": a})
    assert resp.status_code == 200, resp.text
    assert resp.json()["outcome"] == "unreachable"


@pytest.mark.asyncio
async def test_a_redirect_to_a_non_web_scheme_is_invalid_url(authenticated_async_context):
    start = "https://public.example.test/start"
    with aioresponses() as m:
        m.get(start, status=302, headers={"Location": "file:///etc/passwd"})
        async with authenticated_async_context() as client:
            resp = await client.post(FETCH_URL_ROUTE, json={"url": start})
    assert resp.status_code == 200, resp.text
    assert resp.json()["outcome"] == "invalid_url"


# ---------------------------------------------------------------------------
# 22-23: the site refuses us
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_403_is_blocked_and_the_message_says_paste(authenticated_async_context):
    url = "https://www.linkedin.com/jobs/view/123456"
    with aioresponses() as m:
        m.get(url, status=403, body="blocked")
        async with authenticated_async_context() as client:
            resp = await client.post(FETCH_URL_ROUTE, json={"url": url})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["outcome"] == "blocked"
    assert "paste" in body["message"].lower()


@pytest.mark.asyncio
async def test_a_429_is_blocked(authenticated_async_context):
    url = "https://jobs.example.test/posting/1"
    with aioresponses() as m:
        m.get(url, status=429, body="slow down")
        async with authenticated_async_context() as client:
            resp = await client.post(FETCH_URL_ROUTE, json={"url": url})
    assert resp.status_code == 200, resp.text
    assert resp.json()["outcome"] == "blocked"


# ---------------------------------------------------------------------------
# 24-26: size caps
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_oversized_content_length_is_refused_before_reading(authenticated_async_context, monkeypatch):
    from src.core import settings

    monkeypatch.setattr(settings, "URL_FETCH_MAX_BYTES", 1024, raising=False)
    url = "https://big.example.test/huge.html"
    with aioresponses() as m:
        m.get(
            url, status=200, body="<html></html>",
            headers={"Content-Type": "text/html", "Content-Length": "999999999"},
        )
        async with authenticated_async_context() as client:
            resp = await client.post(FETCH_URL_ROUTE, json={"url": url})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["outcome"] == "too_large"
    assert body["bytes_read"] == 0, "a declared-oversize body must be refused before a byte is read"


@pytest.mark.asyncio
async def test_a_lying_content_length_is_caught_by_the_streaming_cap(authenticated_async_context, monkeypatch):
    from src.core import settings

    cap = 1024
    monkeypatch.setattr(settings, "URL_FETCH_MAX_BYTES", cap, raising=False)
    url = "https://big.example.test/lying.html"
    real_body = "<html><body>" + ("x" * (cap * 4)) + "</body></html>"
    with aioresponses() as m:
        # Lies about Content-Length (says small) while the real body is 4x the cap.
        m.get(url, status=200, body=real_body, headers={"Content-Type": "text/html", "Content-Length": "10"})
        async with authenticated_async_context() as client:
            resp = await client.post(FETCH_URL_ROUTE, json={"url": url})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["outcome"] == "too_large"
    # Streaming in 65536-byte chunks: never more than one chunk past the cap.
    assert body["bytes_read"] <= cap + 65536


@pytest.mark.asyncio
async def test_a_gzip_bomb_is_too_large(authenticated_async_context, monkeypatch):
    from src.core import settings

    cap = 4096
    monkeypatch.setattr(settings, "URL_FETCH_MAX_BYTES", cap, raising=False)
    url = "https://big.example.test/bomb.html"
    decoded = "<html><body>" + ("y" * (cap * 8)) + "</body></html>"
    compressed = gzip.compress(decoded.encode("utf-8"))
    with aioresponses() as m:
        m.get(
            url, status=200, body=compressed,
            headers={"Content-Type": "text/html", "Content-Encoding": "gzip"},
        )
        async with authenticated_async_context() as client:
            resp = await client.post(FETCH_URL_ROUTE, json={"url": url})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["outcome"] == "too_large", "the cap is on DECODED bytes, so a small gzip body must still trip it"


# ---------------------------------------------------------------------------
# 27: the whole-journey time budget, no real sleep
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_slow_body_is_timeout(authenticated_async_context, monkeypatch):
    from src.services.fetch import fetcher

    # The clock reports it is already WAY past URL_FETCH_TOTAL_BUDGET_S by the
    # second read — no real delay anywhere, per spec's "injected clock, no
    # real sleep" instruction.
    monkeypatch.setattr(fetcher, "DEFAULT_CLOCK", jumping_clock())
    url = "https://slow.example.test/loris"
    with aioresponses() as m:
        m.get(url, status=200, body="<html><body>hello</body></html>", headers={"Content-Type": "text/html"})
        async with authenticated_async_context() as client:
            resp = await client.post(FETCH_URL_ROUTE, json={"url": url})
    assert resp.status_code == 200, resp.text
    assert resp.json()["outcome"] == "timeout"


# ---------------------------------------------------------------------------
# 28-29: content type + parser bombs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_pdf_is_unsupported_content(authenticated_async_context):
    url = "https://files.example.test/job.pdf"
    with aioresponses() as m:
        m.get(url, status=200, body=b"%PDF-1.4 fake pdf body", headers={"Content-Type": "application/pdf"})
        async with authenticated_async_context() as client:
            resp = await client.post(FETCH_URL_ROUTE, json={"url": url})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["outcome"] == "unsupported_content"
    assert body["bytes_read"] == 0, "a non-HTML content type must refuse before the body is read"


@pytest.mark.asyncio
async def test_a_deeply_nested_html_bomb_does_not_hang(authenticated_async_context):
    depth = 5000
    bomb = "<html><body>" + ("<div>" * depth) + "text" + ("</div>" * depth) + "</body></html>"
    url = "https://bomb.example.test/nested.html"
    start = time.monotonic()
    with aioresponses() as m:
        m.get(url, status=200, body=bomb, headers={"Content-Type": "text/html"})
        async with authenticated_async_context() as client:
            resp = await client.post(FETCH_URL_ROUTE, json={"url": url})
    elapsed = time.monotonic() - start
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # It must ANSWER (not hang) — the depth ceiling/extract budget stops
    # extraction early and returns whatever was found, still `ok`.
    assert body["outcome"] == "ok"
    assert elapsed < 10, "a nesting bomb must not make extraction hang the request"


# ---------------------------------------------------------------------------
# 30-33: auth, rate limits, the kill switch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_anonymous_is_401():
    from src.api.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(FETCH_URL_ROUTE, json={"url": "https://example.test/job"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_rate_limited_per_user_not_per_ip(authenticated_async_context, monkeypatch):
    from src.core import settings

    monkeypatch.setattr(settings, "URL_FETCH_MAX_PER_MINUTE", 1, raising=False)
    url = "https://example.test/job"
    with aioresponses() as m:
        m.get(url, status=200, body="<html><body>ok</body></html>", headers={"Content-Type": "text/html"}, repeat=True)
        async with authenticated_async_context() as client:
            first = await client.post(FETCH_URL_ROUTE, json={"url": url})
            second = await client.post(FETCH_URL_ROUTE, json={"url": url})
        assert first.status_code == 200, first.text
        assert second.status_code == 429, second.text

        # A second, distinct user hitting the SAME per-minute cap must be
        # unaffected — this is what proves the bucket key is the user, not
        # the shared TestClient IP.
        second_user = await _second_user_client()
        async with second_user as client2:
            third = await client2.post(FETCH_URL_ROUTE, json={"url": url})
        assert third.status_code == 200, third.text


@pytest.mark.asyncio
async def test_the_global_budget_stops_a_fresh_user(authenticated_async_context, monkeypatch):
    from src.core import settings

    monkeypatch.setattr(settings, "URL_FETCH_MAX_PER_HOUR_GLOBAL", 1, raising=False)
    url = "https://example.test/job"
    with aioresponses() as m:
        m.get(url, status=200, body="<html><body>ok</body></html>", headers={"Content-Type": "text/html"}, repeat=True)
        async with authenticated_async_context() as client:
            first = await client.post(FETCH_URL_ROUTE, json={"url": url})
        assert first.status_code == 200, first.text

        # A user who has NEVER called this route before is still stopped —
        # the global bucket, not the per-user one, is what refuses them.
        fresh_user = await _second_user_client("fresh@example.com")
        async with fresh_user as client2:
            second = await client2.post(FETCH_URL_ROUTE, json={"url": url})
        assert second.status_code == 429, second.text


@pytest.mark.asyncio
async def test_the_route_404s_when_url_fetch_is_disabled(authenticated_async_context, monkeypatch):
    from src.core import settings

    monkeypatch.setattr(settings, "URL_FETCH_ENABLED", False, raising=False)
    async with authenticated_async_context() as client:
        resp = await client.post(FETCH_URL_ROUTE, json={"url": "https://example.test/job"})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 34: no MCP tool, ever (VISION rule 5 / R10)
# ---------------------------------------------------------------------------


def test_no_mcp_tool_fetches_a_url():
    pytest.importorskip("mcp")
    from src.api.mcp_server import build_server

    tool_names = {t.name for t in build_server()._tool_manager.list_tools()}
    assert "fetch_url" not in tool_names

    from tests.test_mcp_gate_parity import TOOL_ROUTES

    assert "fetch_url" not in TOOL_ROUTES, "fetch-url must have no TOOL_ROUTES row — R10 is enforced by CI, not prose"


# ---------------------------------------------------------------------------
# 35: extracted text is text (S10 / A10)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_raw_html_survives_into_the_response(authenticated_async_context):
    url = "https://vertex.example.test/careers/data-engineer"
    with aioresponses() as m:
        m.get(url, status=200, body=_fixture("markup_soup.html"), headers={"Content-Type": "text/html"})
        async with authenticated_async_context() as client:
            resp = await client.post(FETCH_URL_ROUTE, json={"url": url})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["outcome"] == "ok"
    for field in ("title", "company", "location", "description"):
        assert "<" not in body[field], f"{field!r} carries raw HTML: {body[field]!r}"


# ---------------------------------------------------------------------------
# 36: the audit line carries the host, never the query or the body (A12/S12)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_audit_record_carries_the_host_and_not_the_query_or_the_body(authenticated_async_context):
    secret_token = "SUPERSECRET123"
    url = f"https://example.test/job?token={secret_token}"
    audit = logging.getLogger("job360.audit")

    class _Capture(logging.Handler):
        def __init__(self):
            super().__init__()
            self.records: list[logging.LogRecord] = []

        def emit(self, record: logging.LogRecord) -> None:
            self.records.append(record)

    handler = _Capture()
    audit.addHandler(handler)
    audit.setLevel(logging.INFO)
    try:
        secret_description = "the ad text nobody else should see in a log line"
        with aioresponses() as m:
            m.get(
                url, status=200,
                body=f"<html><body><p>{secret_description}</p></body></html>",
                headers={"Content-Type": "text/html"},
            )
            async with authenticated_async_context() as client:
                resp = await client.post(FETCH_URL_ROUTE, json={"url": url})
        assert resp.status_code == 200, resp.text
    finally:
        audit.removeHandler(handler)

    fetched = [r for r in handler.records if getattr(r, "event", "") == "url_fetched"]
    assert fetched, "no url_fetched audit record was emitted"
    rec = fetched[-1]
    assert getattr(rec, "host", "") == "example.test"
    # Nothing in the whole record (any extra field, or the formatted message)
    # may carry the query string, the token, or the fetched body text.
    blob = repr(rec.__dict__)
    assert secret_token not in blob
    assert "token=" not in blob
    assert secret_description not in blob


# ---------------------------------------------------------------------------
# 37: the outcome enum is closed and single-sourced
# ---------------------------------------------------------------------------


def test_the_outcome_enum_is_closed_and_single_sourced():
    import typing

    from src.api.routes.bring import FetchUrlResponse
    from src.services.fetch import outcomes

    field = FetchUrlResponse.model_fields["outcome"]
    literal_values = set(typing.get_args(field.annotation))
    assert literal_values == set(outcomes.OUTCOMES)
    assert len(outcomes.OUTCOMES) == 8
    for value in outcomes.OUTCOMES:
        assert outcomes.MESSAGES[value], f"outcome {value!r} has no message"
