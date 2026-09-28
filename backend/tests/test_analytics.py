"""Server-side PostHog capture (owner decision, 2026-09-28) — two funnel
events only the backend can see: `first_tool_call` (any MCP tool, first ever)
and `first_bring` (first successful `bring_job`, web or MCP). Off by default;
fires at most once per user however many times the surface is hit.

HTTP is mocked with aioresponses (root CLAUDE.md — the suite runs offline).
"""
from __future__ import annotations

import asyncio

import pytest
from aioresponses import aioresponses
from yarl import URL

pytest.importorskip("mcp")


# ---------------------------------------------------------------------------
# capture_event — the low-level PostHog call
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_capture_event_is_a_noop_when_disabled(monkeypatch):
    from src.core import settings
    from src.services import analytics

    monkeypatch.setattr(settings, "ANALYTICS_BACKEND_ENABLED", False)
    monkeypatch.setattr(settings, "POSTHOG_PROJECT_API_KEY", "phc_test_key")

    sent = []

    async def _fake_send(*a, **kw):
        sent.append((a, kw))

    monkeypatch.setattr(analytics, "_send", _fake_send)
    analytics.capture_event("user-1", "first_bring")
    await asyncio.sleep(0)
    assert sent == [], "disabled by default — nothing should ever be scheduled"


@pytest.mark.asyncio
async def test_capture_event_is_a_noop_with_no_project_key(monkeypatch):
    from src.core import settings
    from src.services import analytics

    monkeypatch.setattr(settings, "ANALYTICS_BACKEND_ENABLED", True)
    monkeypatch.setattr(settings, "POSTHOG_PROJECT_API_KEY", "")

    sent = []

    async def _fake_send(*a, **kw):
        sent.append((a, kw))

    monkeypatch.setattr(analytics, "_send", _fake_send)
    analytics.capture_event("user-1", "first_bring")
    await asyncio.sleep(0)
    assert sent == [], "no key configured — must stay a no-op even if enabled"


@pytest.mark.asyncio
async def test_capture_event_posts_to_the_configured_posthog_project(monkeypatch):
    from src.core import settings
    from src.services import analytics

    monkeypatch.setattr(settings, "ANALYTICS_BACKEND_ENABLED", True)
    monkeypatch.setattr(settings, "POSTHOG_PROJECT_API_KEY", "phc_test_key")
    monkeypatch.setattr(settings, "POSTHOG_HOST", "https://mock.posthog.test")

    with aioresponses() as m:
        m.post("https://mock.posthog.test/capture/", status=200, payload={"status": 1})
        analytics.capture_event("user-1", "first_bring", {"job_id": 7})
        # capture_event returns before any network I/O (fire-and-forget); let
        # the scheduled background task actually run before the mock exits.
        for _ in range(20):
            await asyncio.sleep(0)

    calls = m.requests[("POST", URL("https://mock.posthog.test/capture/"))]
    assert len(calls) == 1
    body = calls[0].kwargs["json"]
    assert body["api_key"] == "phc_test_key"
    assert body["event"] == "first_bring"
    assert body["distinct_id"] == "user-1"
    assert body["properties"] == {"job_id": 7}


@pytest.mark.asyncio
async def test_capture_event_failure_never_raises(monkeypatch):
    """A PostHog outage must never surface to the caller — that is the whole
    point of fire-and-forget."""
    from src.core import settings
    from src.services import analytics

    monkeypatch.setattr(settings, "ANALYTICS_BACKEND_ENABLED", True)
    monkeypatch.setattr(settings, "POSTHOG_PROJECT_API_KEY", "phc_test_key")
    monkeypatch.setattr(settings, "POSTHOG_HOST", "https://mock.posthog.test")

    with aioresponses() as m:
        m.post("https://mock.posthog.test/capture/", exception=ConnectionError("boom"))
        analytics.capture_event("user-1", "first_bring")  # must not raise
        for _ in range(20):
            await asyncio.sleep(0)


# ---------------------------------------------------------------------------
# mark_first_tool_call / mark_first_bring — fire once, atomically
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mark_first_tool_call_fires_once_not_twice(authenticated_async_context, fixture_user_id):
    from src.api import dependencies as api_deps
    from src.services import analytics

    db = await api_deps.get_db()
    first = await analytics.mark_first_tool_call(db._db, fixture_user_id)
    second = await analytics.mark_first_tool_call(db._db, fixture_user_id)
    assert first is True, "the first claim must win"
    assert second is False, "a second claim for the same user must lose"


@pytest.mark.asyncio
async def test_mark_first_bring_fires_once_not_twice(authenticated_async_context, fixture_user_id):
    from src.api import dependencies as api_deps
    from src.services import analytics

    db = await api_deps.get_db()
    first = await analytics.mark_first_bring(db._db, fixture_user_id)
    second = await analytics.mark_first_bring(db._db, fixture_user_id)
    assert first is True
    assert second is False


@pytest.mark.asyncio
async def test_bring_job_captures_first_bring_once_across_two_brings(
    authenticated_async_context, fixture_user_id, monkeypatch
):
    """The real `/jobs/bring` door — both a first bring and a second (even to
    a different job) fire the DB claim; only the first actually wins it."""
    from src.core import settings
    from src.services import analytics

    monkeypatch.setattr(settings, "ANALYTICS_BACKEND_ENABLED", True)
    monkeypatch.setattr(settings, "POSTHOG_PROJECT_API_KEY", "phc_test_key")
    monkeypatch.setattr(settings, "POSTHOG_HOST", "https://mock.posthog.test")

    captured: list[str] = []
    real_mark = analytics.mark_first_bring

    async def _spy_mark(conn, user_id):
        won = await real_mark(conn, user_id)
        if won:
            captured.append(user_id)
        return won

    monkeypatch.setattr(analytics, "mark_first_bring", _spy_mark)

    ad_a = {
        "title": "Platform Engineer",
        "company": "Northwind",
        "location": "Remote",
        "apply_url": "https://northwind.example/careers/9",
        "description": "Kubernetes, Go, Postgres.",
    }
    ad_b = {
        "title": "Data Engineer",
        "company": "Contoso",
        "location": "Manchester",
        "apply_url": "https://contoso.example/jobs/12",
        "description": "Airflow, dbt, Postgres.",
    }
    with aioresponses() as m:
        m.post("https://mock.posthog.test/capture/", status=200, payload={"status": 1}, repeat=True)
        async with authenticated_async_context() as client:
            r1 = await client.post("/api/jobs/bring", json=ad_a)
            assert r1.status_code == 200, r1.text
            r2 = await client.post("/api/jobs/bring", json=ad_b)
            assert r2.status_code == 200, r2.text
        for _ in range(20):
            await asyncio.sleep(0)

    assert captured == [fixture_user_id], "first_bring must be claimed exactly once, on the FIRST bring"
