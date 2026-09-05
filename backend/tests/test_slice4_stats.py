"""Slice 4 — stats (docs/plans/2026-09-05-contacts-stats/spec.md R5-R7, S1, S7, S10).

Frozen per spec.md §Frozen tests. `GET /applications/stats` is
COUNT(DISTINCT application_id) per status event type over the user's
applications, grouped by the CV variant LABEL named on the latest receipt and
by role. Nothing is inferred; every number below is a hand count of the
history each test builds. Helpers copied from test_application_spine.py.
"""
from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

_AD = {
    "title": "Data Engineer",
    "company": "Northwind",
    "location": "Remote",
    "apply_url": "https://northwind.example/careers/7",
    "description": "Build the pipelines. Python, dbt, Snowflake. Fully remote.",
}
_AD_2 = {
    "title": "Platform Engineer",
    "company": "Southwind",
    "location": "London",
    "apply_url": "https://southwind.example/careers/3",
    "description": "Own the platform. Kubernetes, Terraform, Go.",
}
_AD_3 = {
    "title": "  data engineer ",
    "company": "Eastwind",
    "location": "Leeds",
    "apply_url": "https://eastwind.example/careers/9",
    "description": "Batch and streaming. Spark, Kafka, Python.",
}

_COUNT_KEYS = {"brought", "applied", "replied", "interview", "offer", "rejected"}
_RATE_KEYS = {"reply_rate", "interview_rate", "offer_rate"}


async def _bring(client: AsyncClient, ad: dict[str, Any] = _AD) -> int:
    resp = await client.post("/api/jobs/bring", json=ad)
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _save_cv(client: AsyncClient, application_id: int, label: str) -> int:
    resp = await client.post(
        f"/api/applications/{application_id}/artifacts",
        json={"kind": "cv", "text": f"CV tailored — {label}", "label": label},
    )
    assert resp.status_code == 201, resp.text
    return int(resp.json()["artifact_id"])


async def _event(client: AsyncClient, application_id: int, event_type: str, **extra: Any) -> None:
    resp = await client.post(f"/api/applications/{application_id}/events", json={"event_type": event_type, **extra})
    assert resp.status_code == 201, resp.text


async def _receipt(client: AsyncClient, application_id: int, **body: Any) -> None:
    resp = await client.post(f"/api/applications/{application_id}/receipt", json=body)
    assert resp.status_code == 201, resp.text


async def _stats(client: AsyncClient, **params: Any):
    return await client.get("/api/applications/stats", params=params)


async def _second_user_session_cookie(email: str = "second@example.com") -> str:
    from fastapi.testclient import TestClient

    from src.api.main import app
    from src.core import settings
    from src.repositories import pgsync

    sync_client = TestClient(app)
    r = sync_client.post("/api/auth/register", json={"email": email, "password": "s3cretpassword"})
    assert r.status_code == 201, r.text
    conn = pgsync.connect(str(settings.DB_PATH))
    conn.execute("UPDATE users SET email_verified_at = ? WHERE email = ?", ("2026-01-01T00:00:00Z", email))
    conn.commit()
    conn.close()
    lr = sync_client.post("/api/auth/login", json={"email": email, "password": "s3cretpassword"})
    assert lr.status_code == 200, lr.text
    cookie = sync_client.cookies.get("job360_session")
    assert cookie, "failed to capture second user's session cookie"
    sync_client.close()
    return cookie


def _session_client(cookie: str) -> AsyncClient:
    from src.api.main import app

    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", cookies={"job360_session": cookie}
    )


async def _build_known_history(client: AsyncClient) -> dict[str, int]:
    """Three applications with a hand-countable history.

    A (Data Engineer):     applied, replied, interview_scheduled; receipt names CV "quant-heavy"
    B (Platform Engineer): applied (TWICE — must count once), rejected; receipt names CV "platform"
    C (" data engineer "): brought only, no receipt
    Expected overall: brought 3, applied 2, replied 1, interview 1, offer 0, rejected 1.
    """
    a = await _bring(client, _AD)
    b = await _bring(client, _AD_2)
    c = await _bring(client, _AD_3)

    cv_a = await _save_cv(client, a, "Quant-Heavy")
    await _receipt(client, a, cv_artifact_id=cv_a)
    await _event(client, a, "applied")
    await _event(client, a, "replied")
    await _event(client, a, "interview_scheduled")

    cv_b = await _save_cv(client, b, "platform")
    await _receipt(client, b, cv_artifact_id=cv_b)
    await _event(client, b, "applied")
    await _event(client, b, "applied", detail="re-applied via referral")
    await _event(client, b, "rejected")
    return {"a": a, "b": b, "c": c}


# ═══════════════════════════════════════════════════════════════════════════
# R5 — counts are a hand count of the log; rates from applied
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_overall_counts_match_a_hand_count(authenticated_async_context):
    async with authenticated_async_context() as client:
        await _build_known_history(client)
        resp = await _stats(client)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        overall = body["overall"]
        assert {k: overall[k] for k in _COUNT_KEYS} == {
            "brought": 3, "applied": 2, "replied": 1, "interview": 1, "offer": 0, "rejected": 1,
        }
        assert overall["reply_rate"] == 0.5
        assert overall["interview_rate"] == 0.5
        assert overall["offer_rate"] == 0.0
        assert body["since"] is None
        assert body["groups_truncated"] is False
        assert body["computed_at"]


@pytest.mark.asyncio
async def test_rates_are_null_when_nothing_was_applied(authenticated_async_context):
    """Rule #29 — an empty shelf stays silent: no applications → null, never 0%."""
    async with authenticated_async_context() as client:
        resp = await _stats(client)
        assert resp.status_code == 200, resp.text
        overall = resp.json()["overall"]
        assert {k: overall[k] for k in _COUNT_KEYS} == dict.fromkeys(_COUNT_KEYS, 0)
        assert all(overall[k] is None for k in _RATE_KEYS)

        await _bring(client)  # brought but never applied
        overall = (await _stats(client)).json()["overall"]
        assert overall["brought"] == 1 and overall["applied"] == 0
        assert all(overall[k] is None for k in _RATE_KEYS)


@pytest.mark.asyncio
async def test_every_interview_event_type_counts_as_interview_once(authenticated_async_context):
    async with authenticated_async_context() as client:
        a = await _bring(client, _AD)
        await _event(client, a, "applied")
        await _event(client, a, "interview_requested")
        await _event(client, a, "interview_scheduled")
        await _event(client, a, "interview_done")
        b = await _bring(client, _AD_2)
        await _event(client, b, "applied")
        await _event(client, b, "interview_done")
        overall = (await _stats(client)).json()["overall"]
        assert overall["interview"] == 2, "three interview events on A are ONE interviewed application"
        assert overall["interview_rate"] == 1.0


@pytest.mark.asyncio
async def test_a_corrected_or_duplicate_event_still_counts_once(authenticated_async_context):
    async with authenticated_async_context() as client:
        a = await _bring(client, _AD)
        await _event(client, a, "applied")
        await _event(client, a, "applied")
        await _event(client, a, "offer")
        overall = (await _stats(client)).json()["overall"]
        assert overall["applied"] == 1 and overall["offer"] == 1


# ═══════════════════════════════════════════════════════════════════════════
# R6 — grouping by CV label and by role
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_by_cv_version_groups_by_label_of_the_receipted_cv(authenticated_async_context):
    async with authenticated_async_context() as client:
        ids = await _build_known_history(client)
        # a second application receipted with the SAME label (case differs) joins that group
        d = await _bring(client, {**_AD, "company": "Westwind", "apply_url": "https://westwind.example/1"})
        cv_d = await _save_cv(client, d, "quant-heavy")
        await _receipt(client, d, cv_artifact_id=cv_d)
        await _event(client, d, "applied")
        await _event(client, d, "rejected")

        groups = {g["label"]: g for g in (await _stats(client)).json()["by_cv_version"]}
        assert set(groups) == {"Quant-Heavy", "platform", None}, groups.keys()
        quant = groups["Quant-Heavy"]
        assert (quant["brought"], quant["applied"], quant["replied"], quant["rejected"]) == (2, 2, 1, 1)
        assert quant["reply_rate"] == 0.5
        plat = groups["platform"]
        assert (plat["brought"], plat["applied"], plat["rejected"]) == (1, 1, 1)
        unl = groups[None]
        assert (unl["brought"], unl["applied"]) == (1, 0), "C has no receipt → the null group"
        assert all(unl[k] is None for k in _RATE_KEYS)
        assert isinstance(quant["profile_versions"], list)
        assert ids["c"]  # C exists (guards the fixture, not the route)


@pytest.mark.asyncio
async def test_by_cv_version_uses_the_latest_receipt(authenticated_async_context):
    async with authenticated_async_context() as client:
        a = await _bring(client, _AD)
        v1 = await _save_cv(client, a, "first-try")
        await _receipt(client, a, cv_artifact_id=v1)
        v2 = await _save_cv(client, a, "second-try")
        await _receipt(client, a, cv_artifact_id=v2, note="re-applied with the new CV")
        await _event(client, a, "applied")
        labels = [g["label"] for g in (await _stats(client)).json()["by_cv_version"]]
        assert labels == ["second-try"], labels


@pytest.mark.asyncio
async def test_by_role_is_case_and_space_insensitive(authenticated_async_context):
    async with authenticated_async_context() as client:
        ids = await _build_known_history(client)
        await _event(client, ids["c"], "applied")
        groups = {g["role"].strip().lower(): g for g in (await _stats(client)).json()["by_role"]}
        assert set(groups) == {"data engineer", "platform engineer"}
        de = groups["data engineer"]
        assert (de["brought"], de["applied"], de["replied"], de["interview"]) == (2, 2, 1, 1)
        assert de["reply_rate"] == 0.5
        pe = groups["platform engineer"]
        assert (pe["brought"], pe["applied"], pe["rejected"]) == (1, 1, 1)
        # display keeps a real title, not the normalised key
        assert "Data Engineer" in {g["role"] for g in (await _stats(client)).json()["by_role"]}


@pytest.mark.asyncio
async def test_groups_are_capped_and_say_so(authenticated_async_context, monkeypatch):
    from src.core import settings

    monkeypatch.setattr(settings, "STATS_MAX_GROUPS", 2)
    async with authenticated_async_context() as client:
        for i in range(3):
            app_id = await _bring(client, {**_AD, "title": f"Role {i}", "apply_url": f"https://x.example/{i}"})
            await _event(client, app_id, "applied")
        body = (await _stats(client)).json()
        assert len(body["by_role"]) == 2
        assert body["groups_truncated"] is True


@pytest.mark.asyncio
async def test_groups_are_ordered_by_applied_then_brought(authenticated_async_context):
    async with authenticated_async_context() as client:
        quiet = await _bring(client, {**_AD, "title": "Quiet Role", "apply_url": "https://x.example/q"})
        busy_1 = await _bring(client, {**_AD, "title": "Busy Role", "apply_url": "https://x.example/b1"})
        busy_2 = await _bring(client, {**_AD, "title": "Busy Role", "company": "Other", "apply_url": "https://x.example/b2"})
        await _event(client, busy_1, "applied")
        await _event(client, busy_2, "applied")
        assert quiet
        roles = [g["role"] for g in (await _stats(client)).json()["by_role"]]
        assert roles == ["Busy Role", "Quiet Role"]


# ═══════════════════════════════════════════════════════════════════════════
# R7 — since scopes the universe by bring time
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_since_excludes_applications_brought_before_it(authenticated_async_context):
    from datetime import datetime, timedelta, timezone

    async with authenticated_async_context() as client:
        old = await _bring(client, _AD)
        await _event(client, old, "applied")
        await _event(client, old, "replied")
        # move OLD's bring time into the past directly — created_at is bring time (spec R7)
        from src.api import dependencies as api_deps

        db = await api_deps.get_db()
        await db._conn.execute(
            "UPDATE applications SET created_at = ? WHERE id = ?",
            ((datetime.now(timezone.utc) - timedelta(days=30)).isoformat(), old),
        )
        await db._conn.commit()
        new = await _bring(client, _AD_2)
        await _event(client, new, "applied")

        since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        body = (await _stats(client, since=since)).json()
        assert body["since"] is not None
        assert (body["overall"]["brought"], body["overall"]["applied"], body["overall"]["replied"]) == (1, 1, 0)

        everything = (await _stats(client)).json()["overall"]
        assert (everything["brought"], everything["applied"], everything["replied"]) == (2, 2, 1)


@pytest.mark.asyncio
async def test_since_must_be_a_date(authenticated_async_context):
    async with authenticated_async_context() as client:
        resp = await _stats(client, since="last tuesday")
        assert resp.status_code == 422, resp.text


# ═══════════════════════════════════════════════════════════════════════════
# S1/S7/S10 — ownership, per-user rate limit, route shape
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_second_user_sees_only_their_own_zeros(authenticated_async_context):
    async with authenticated_async_context() as client:
        await _build_known_history(client)
    cookie = await _second_user_session_cookie("stats-other@example.com")
    async with _session_client(cookie) as other:
        body = (await _stats(other)).json()
        assert body["overall"]["brought"] == 0 and body["overall"]["applied"] == 0
        assert body["by_cv_version"] == [] and body["by_role"] == []


@pytest.mark.asyncio
async def test_stats_is_rate_limited_per_user(authenticated_async_context, monkeypatch):
    from src.core import settings

    monkeypatch.setattr(settings, "STATS_MAX_PER_HOUR", 2)
    async with authenticated_async_context() as client:
        assert (await _stats(client)).status_code == 200
        assert (await _stats(client)).status_code == 200
        assert (await _stats(client)).status_code == 429
    cookie = await _second_user_session_cookie("stats-limit-other@example.com")
    async with _session_client(cookie) as other:
        assert (await _stats(other)).status_code == 200, "the limit is per USER, not per IP/process"


def test_stats_route_is_declared_before_the_id_route():
    from src.api.main import app
    from tests._routes import route_paths

    # route_paths, not app.routes -- FastAPI 0.141 nests included routers.
    paths = route_paths(app)
    assert "/api/applications/stats" in paths
    assert paths.index("/api/applications/stats") < paths.index("/api/applications/{application_id}")


def test_stats_sql_uses_the_status_vocabulary_not_literals():
    """R5 — the event-type sets come from settings, never re-typed."""
    from pathlib import Path

    src = Path(__file__).resolve().parent.parent / "src" / "services" / "applications" / "stats.py"
    text = src.read_text(encoding="utf-8")
    assert "STATS_INTERVIEW_EVENT_TYPES" in text
    assert "'interview_scheduled'" not in text and '"interview_scheduled"' not in text


@pytest.mark.asyncio
async def test_anonymous_is_401():
    from src.api.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        resp = await anon.get("/api/applications/stats")
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_mcp_stats_calls_the_route(authenticated_async_context):
    pytest.importorskip("mcp")
    import json

    import httpx2
    from mcp.client import Client
    from mcp.client.streamable_http import streamable_http_client

    from src.api.main import app
    from src.api.mcp_server import mcp_runtime

    async with authenticated_async_context() as client:
        await _build_known_history(client)
        resp = await client.post("/api/tokens", json={"name": "agent"})
        token = resp.json()["token"]

    http = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    )
    async with mcp_runtime():
        async with Client(streamable_http_client("http://test/api/mcp", http_client=http)) as mcp:
            result = await mcp.call_tool("stats", {})
            assert not result.is_error, result.content[0].text
            body = json.loads(result.content[0].text)
            assert body["overall"]["applied"] == 2 and body["overall"]["replied"] == 1
