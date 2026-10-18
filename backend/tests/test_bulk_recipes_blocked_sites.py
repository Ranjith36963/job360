"""Bulk-apply wave 1c: the `ats` recipe (F1), more blocked reasons (N1), `stats view=sites` (P1)."""
from __future__ import annotations

import json
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.services.applications import blocked as blocked_service

NEW_REASONS = (
    "posting_closed", "account_creation", "otp_needed", "id_or_payment_asked", "assessment_required",
    "manual_only", "site_limit_reached",
)


async def _bring(client: AsyncClient, n: int) -> int:
    ad = {
        "title": f"Data Engineer {n}", "company": f"Co{n}", "location": "Remote",
        "apply_url": f"https://co{n}.example/careers/{n}",
        "description": "Build the pipelines. Python, dbt, Snowflake. Fully remote.",
    }
    resp = await client.post("/api/jobs/bring", json=ad)
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _event(client: AsyncClient, app_id: int, event_type: str, payload: dict[str, Any] | None = None):
    body: dict[str, Any] = {"event_type": event_type}
    if payload is not None:
        body["payload"] = payload
    return await client.post(f"/api/applications/{app_id}/events", json=body)


async def _filled(client: AsyncClient, app_id: int, host: str) -> None:
    resp = await _event(client, app_id, "form_filled", {"form_url": f"https://{host}/apply/{app_id}", "fields_count": 1})
    assert resp.status_code == 201, resp.text


# ── F1: the ats recipe ───────────────────────────────────────────────────────


def test_ats_recipe_is_registered_short_and_marks_every_row_with_its_source():
    from src.api.routes.recipes import RECIPE_NAMES, load_recipe

    assert "ats" in RECIPE_NAMES and RECIPE_NAMES[-1] == "rules"
    text = load_recipe("ats").text
    assert len(text.splitlines()) <= 70
    assert "Print this checklist and tick each step:" in text
    rows = [ln for ln in text.splitlines() if ln.startswith("| ") and not ln.startswith(("| Site", "|---"))]
    assert rows
    assert all("BORROWED" in r or "SEEN" in r or "blocked" in r for r in rows), rows
    for needle in ("Workday", "react-select", "1000+", "fresh", "SuccessFactors", "Ashby", "Never invent an alias",
                   "CAPTCHA", "site_limit_reached"):
        assert needle in text, needle


def test_forms_recipe_points_step_2_at_ats():
    from src.api.routes.recipes import load_recipe

    forms = load_recipe("forms").text
    assert 'get_recipe("ats")' in forms and len(forms.splitlines()) <= 70


# ── N1: the new blocked reasons ──────────────────────────────────────────────


def test_every_new_reason_is_in_the_closed_set_with_words_and_a_todo():
    for reason in NEW_REASONS:
        assert reason in blocked_service.BLOCKED_REASONS
        todo = blocked_service.ask_question(
            {"reason": reason, "step": "form", "page_host": "h.example", "detail": ""}
        )
        assert "h.example" in todo and "Check the form on" not in todo, reason
    assert "id_or_payment_asked" in blocked_service.NEVER_RETRIED
    assert "site_limit_reached" not in blocked_service.NEVER_RETRIED


def test_rules_recipe_and_mcp_docstring_name_every_reason():
    from src.api.routes.recipes import load_recipe

    rules = load_recipe("rules").text
    batch = load_recipe("batch").text
    mcp_src = open("src/api/mcp_server.py", encoding="utf-8").read()
    for reason in blocked_service.BLOCKED_REASONS:
        assert reason in rules, reason
        if reason in ("site_limit_reached", "id_or_payment_asked"):
            assert reason in mcp_src, reason  # the rest: "5 more: rules"
    assert "site_limit_reached" in batch and "every other row on that host" in batch


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", NEW_REASONS)
async def test_new_reasons_record_open_one_ask_and_unblock(authenticated_async_context, reason):
    async with authenticated_async_context() as client:
        app_id = await _bring(client, 1)
        resp = await _event(client, app_id, "blocked", {"reason": reason, "step": "form", "page_host": "x.example.com"})
        assert resp.status_code == 201, resp.text
        asks = (await client.get("/api/asks", params={"status": "open"})).json()["asks"]
        assert len([a for a in asks if a["application_id"] == app_id]) == 1
        resp = await _event(client, app_id, "unblocked", {"resolution": "skipped"})
        assert resp.status_code == 201, resp.text


@pytest.mark.asyncio
async def test_id_or_payment_asked_is_never_retried_but_can_be_skipped(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client, 2)
        blocked = {"reason": "id_or_payment_asked", "step": "selfie", "page_host": "x.example.com"}
        assert (await _event(client, app_id, "blocked", blocked)).status_code == 201
        retried = await _event(client, app_id, "unblocked", {"resolution": "retried"})
        assert retried.status_code == 422 and "never retried" in retried.text
        assert (await _event(client, app_id, "unblocked", {"resolution": "user_did_it"})).status_code == 201


@pytest.mark.asyncio
async def test_unknown_reason_is_422_and_lists_the_new_ones(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client, 3)
        resp = await _event(client, app_id, "blocked", {"reason": "nope"})
        assert resp.status_code == 422
        assert "site_limit_reached" in resp.text and "id_or_payment_asked" in resp.text


@pytest.mark.asyncio
async def test_morning_check_groups_blocked_by_reason(authenticated_async_context):
    async with authenticated_async_context() as client:
        for n, reason in enumerate(("site_limit_reached", "site_limit_reached", "otp_needed"), start=10):
            app_id = await _bring(client, n)
            assert (await _event(client, app_id, "blocked", {"reason": reason, "page_host": "a.example.com"})
                    ).status_code == 201
        resp = await client.get("/api/morning-check")
        assert resp.status_code == 200, resp.text
        blocked = resp.json()["tally"]["blocked"]
        assert blocked["count"] == 3
        assert blocked["reasons"] == {"site_limit_reached": 2, "otp_needed": 1}


# ── P1: stats view=sites ─────────────────────────────────────────────────────


async def _seed_sites(client: AsyncClient) -> None:
    a = await _bring(client, 20)
    await _filled(client, a, "jobs.alpha.example")
    assert (await _event(client, a, "applied")).status_code == 201
    b = await _bring(client, 21)
    await _filled(client, b, "jobs.alpha.example")
    assert (await _event(client, b, "blocked", {"reason": "captcha", "page_host": "jobs.alpha.example"})
            ).status_code == 201
    c = await _bring(client, 22)
    await _filled(client, c, "careers.beta.example")
    assert (await _event(client, c, "blocked", {"reason": "site_limit_reached"})).status_code == 201
    await _bring(client, 23)  # no form yet: counted as without a host


@pytest.mark.asyncio
async def test_sites_view_counts_per_host_and_nothing_else(authenticated_async_context):
    async with authenticated_async_context() as client:
        await _seed_sites(client)
        resp = await client.get("/api/applications/stats", params={"view": "sites"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["funnel"] is None and body["patterns"] is None
        sites = {s["host"]: s for s in body["sites"]["sites"]}
        assert sites["jobs.alpha.example"] == {
            "host": "jobs.alpha.example", "n": 2, "forms_filled": 2, "applied": 1,
            "blocked": {"captcha": 1}, "blocked_total": 1,
        }
        # a blocked with no page_host falls back to the application's form host
        assert sites["careers.beta.example"]["blocked"] == {"site_limit_reached": 1}
        assert sites["careers.beta.example"]["applied"] == 0
        assert body["sites"]["applications_without_host"] == 1
        assert "never rank" in body["sites"]["note"].lower()
        assert not any(k in sites["jobs.alpha.example"] for k in ("rate", "score", "rank"))


@pytest.mark.asyncio
async def test_sites_view_is_not_set_without_the_view_and_is_in_all(authenticated_async_context):
    async with authenticated_async_context() as client:
        await _seed_sites(client)
        plain = (await client.get("/api/applications/stats")).json()
        assert plain["sites"] is None
        every = (await client.get("/api/applications/stats", params={"view": "all"})).json()
        assert every["sites"] is not None and every["sites"]["hosts"] == 2


@pytest.mark.asyncio
async def test_sites_view_never_shows_another_users_hosts(authenticated_async_context):
    async with authenticated_async_context() as client:
        await _seed_sites(client)
    from fastapi.testclient import TestClient

    from src.api.main import app
    from src.core import settings
    from src.repositories import pgsync

    email = "other-sites@example.com"
    sync_client = TestClient(app)
    assert sync_client.post("/api/auth/register", json={"email": email, "password": "s3cretpassword"}).status_code == 201
    conn = pgsync.connect(str(settings.DB_PATH))
    conn.execute("UPDATE users SET email_verified_at = ? WHERE email = ?", ("2026-01-01T00:00:00Z", email))
    conn.commit()
    conn.close()
    login = sync_client.post("/api/auth/login", json={"email": email, "password": "s3cretpassword"})
    assert login.status_code == 200, login.text
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", cookies={"job360_session": login.cookies["job360_session"]}
    ) as other:
        body = (await other.get("/api/applications/stats", params={"view": "sites"})).json()
        assert body["sites"]["sites"] == [] and body["sites"]["hosts"] == 0


@pytest.mark.asyncio
async def test_mcp_stats_sites_matches_http(authenticated_async_context):
    pytest.importorskip("mcp")
    import httpx2
    from mcp.client import Client
    from mcp.client.streamable_http import streamable_http_client

    from src.api.main import app
    from src.api.mcp_server import mcp_runtime

    async with authenticated_async_context() as client:
        await _seed_sites(client)
        token = (await client.post("/api/tokens", json={"name": "agent"})).json()["token"]
        http_sites = (await client.get("/api/applications/stats", params={"view": "sites"})).json()["sites"]
        http = httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://test",
            headers={"Authorization": f"Bearer {token}"},
        )
        async with mcp_runtime():
            async with Client(streamable_http_client("http://test/api/mcp", http_client=http)) as mcp:
                out = await mcp.call_tool("stats", {"view": "sites"})
                assert not out.is_error, out.content
                assert json.loads(out.content[0].text)["sites"] == http_sites
