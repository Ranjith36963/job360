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
    # The global gate runs before any DB read, so no connection is needed.
    await analytics.capture_event(None, "user-1", "first_bring")
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
    # The global gate runs before any DB read, so no connection is needed.
    await analytics.capture_event(None, "user-1", "first_bring")
    await asyncio.sleep(0)
    assert sent == [], "no key configured — must stay a no-op even if enabled"


async def _set_consent(user_id: str, consented: bool):
    """Write the stored consent straight to the row; returns the connection."""
    from src.api import dependencies as api_deps

    db = await api_deps.get_db()
    stamp = "2026-10-20T09:00:00+00:00" if consented else None
    await db._db.execute("UPDATE users SET analytics_consent_at = ? WHERE id = ?", (stamp, user_id))
    await db._db.commit()
    return db._db


def _enable(monkeypatch):
    from src.core import settings

    monkeypatch.setattr(settings, "ANALYTICS_BACKEND_ENABLED", True)
    monkeypatch.setattr(settings, "POSTHOG_PROJECT_API_KEY", "phc_test_key")
    monkeypatch.setattr(settings, "POSTHOG_HOST", "https://mock.posthog.test")


@pytest.mark.asyncio
async def test_capture_event_posts_to_the_configured_posthog_project(
    authenticated_async_context, fixture_user_id, monkeypatch
):
    from src.services import analytics

    _enable(monkeypatch)
    conn = await _set_consent(fixture_user_id, True)

    with aioresponses() as m:
        m.post("https://mock.posthog.test/capture/", status=200, payload={"status": 1})
        await analytics.capture_event(conn, fixture_user_id, "first_bring", {"job_id": 7})
        # capture_event returns before any network I/O (fire-and-forget); let
        # the scheduled background task actually run before the mock exits.
        for _ in range(20):
            await asyncio.sleep(0)

    calls = m.requests[("POST", URL("https://mock.posthog.test/capture/"))]
    assert len(calls) == 1
    body = calls[0].kwargs["json"]
    assert body["api_key"] == "phc_test_key"
    assert body["event"] == "first_bring"
    assert body["distinct_id"] == fixture_user_id
    assert body["properties"] == {"job_id": 7}


@pytest.mark.asyncio
async def test_capture_event_sends_nothing_for_a_user_who_never_consented(
    authenticated_async_context, fixture_user_id, monkeypatch
):
    """Gap roadmap slice 4: the owner's global switch is ON, but this user never
    turned on "Help improve Job360" (`analytics_consent_at` is NULL) — nothing
    may leave for PostHog."""
    from src.services import analytics

    _enable(monkeypatch)
    conn = await _set_consent(fixture_user_id, False)

    with aioresponses() as m:
        m.post("https://mock.posthog.test/capture/", status=200, payload={"status": 1})
        await analytics.capture_event(conn, fixture_user_id, "first_bring", {"job_id": 7})
        for _ in range(20):
            await asyncio.sleep(0)

    assert m.requests == {}, "a non-consenting user must never be sent to PostHog"
    assert await analytics.has_analytics_consent(conn, fixture_user_id) is False


@pytest.mark.asyncio
async def test_capture_event_failure_never_raises(authenticated_async_context, fixture_user_id, monkeypatch):
    """A PostHog outage must never surface to the caller — that is the whole
    point of fire-and-forget."""
    from src.services import analytics

    _enable(monkeypatch)
    conn = await _set_consent(fixture_user_id, True)

    with aioresponses() as m:
        m.post("https://mock.posthog.test/capture/", exception=ConnectionError("boom"))
        await analytics.capture_event(conn, fixture_user_id, "first_bring")  # must not raise
        for _ in range(20):
            await asyncio.sleep(0)
    assert len(m.requests[("POST", URL("https://mock.posthog.test/capture/"))]) == 1, "the send was attempted"


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


@pytest.mark.asyncio
async def test_bring_job_survives_a_broken_analytics_claim(
    authenticated_async_context, monkeypatch
):
    """A drifted DB (missing column) or a connection blip in the
    `mark_first_bring` claim must never turn an already-successful bring
    into a 500 — the route wraps the call exactly like
    `_CaptureFirstToolCall._capture_once` does."""
    from src.services import analytics

    async def _broken_mark(conn, user_id):
        raise RuntimeError("column \"first_bring_at\" does not exist")

    monkeypatch.setattr(analytics, "mark_first_bring", _broken_mark)

    ad = {
        "title": "Platform Engineer",
        "company": "Northwind",
        "location": "Remote",
        "apply_url": "https://northwind.example/careers/9",
        "description": "Kubernetes, Go, Postgres.",
    }
    async with authenticated_async_context() as client:
        resp = await client.post("/api/jobs/bring", json=ad)
        assert resp.status_code == 200, resp.text
        assert resp.json()["job"]["title"] == "Platform Engineer"


# ---------------------------------------------------------------------------
# PUT /api/auth/me/analytics-consent — the user's own switch (gap slice 4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_consent_route_sets_and_clears_the_stored_flag(authenticated_async_context, fixture_user_id):
    from src.api import dependencies as api_deps

    async def _stored():
        db = await api_deps.get_db()
        cur = await db._db.execute("SELECT analytics_consent_at FROM users WHERE id = ?", (fixture_user_id,))
        return (await cur.fetchone())[0]

    async with authenticated_async_context() as client:
        before = await client.get("/api/auth/me")
        assert before.json()["analytics_consent"] is False, "off by default"
        assert await _stored() is None

        on = await client.put("/api/auth/me/analytics-consent", json={"consent": True})
        assert on.status_code == 200, on.text
        assert on.json() == {"analytics_consent": True}
        assert (await _stored()).startswith("20"), "an ISO timestamp of when they agreed"
        assert (await client.get("/api/auth/me")).json()["analytics_consent"] is True

        off = await client.put("/api/auth/me/analytics-consent", json={"consent": False})
        assert off.status_code == 200, off.text
        assert off.json() == {"analytics_consent": False}
        assert await _stored() is None, "withdrawing clears the column"
        assert (await client.get("/api/auth/me")).json()["analytics_consent"] is False


@pytest.mark.asyncio
async def test_consent_route_refuses_a_bearer_token(authenticated_async_context, fixture_user_id):
    """Consent is the person's own choice: an assistant's token may not give it."""
    from httpx import ASGITransport, AsyncClient

    from src.api import dependencies as api_deps
    from src.api.main import app
    from src.services import analytics

    async with authenticated_async_context() as client:
        token = (await client.post("/api/tokens", json={"name": "agent"})).json()["token"]
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    ) as bearer:
        resp = await bearer.put("/api/auth/me/analytics-consent", json={"consent": True})
        assert resp.status_code == 403 and resp.json()["detail"] == "session_required"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        assert (await anon.put("/api/auth/me/analytics-consent", json={"consent": True})).status_code == 401

    db = await api_deps.get_db()
    assert await analytics.has_analytics_consent(db._db, fixture_user_id) is False, "nothing was stored"
