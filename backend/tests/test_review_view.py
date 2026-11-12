"""get_application(view="review"): the lean read a CV review needs (production 2026-11-10, 0 of 60 recorded).

The scheduled CV review gave each reviewer the exact lines from get_application's ``review`` block, but the call
returned the WHOLE application (job ad, every event, contacts, asks: 30-130 KB), so 60 CVs overflowed the client,
which saved results to files and the parallel workers rebuilt the lines by hand. ``view="review"`` returns only
id, job title and company, country and ``review`` (+ the retry budget and the last downgrade codes). Real doors
(HTTP + MCP), values asserted (rule #21).
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from src.services.applications import cv_review
from tests.test_application_spine import _second_user_session_cookie, _session_client
from tests.test_cv_review import CV, _event, _mcp_client, _payload, _review, _save_cv, _seed, _setup, _stored, sha

KEYS = {"id", "job_title", "job_company", "country", "review"}
REVIEW_KEYS = {"artifact_id", "sha256", "lines", "held", "state", "reviews_used", "tries_left", "last_downgraded",
               "last_where"}


async def _get(client: Any, app_id: int, **params: Any):
    return await client.get(f"/api/applications/{app_id}", params=params)


async def _pad_events(client: Any, app_id: int, n: int = 60) -> None:
    for i in range(n):
        resp = await _event(client, app_id, "note", {"detail": f"padding note {i} " + "x" * 300})
        assert resp.status_code == 201, resp.text


@pytest.mark.asyncio
async def test_review_view_is_lean_and_carries_the_full_views_review_lines(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        app_id, cv_id = await _setup(client)
        await _pad_events(client, app_id)
        full = (await _get(client, app_id)).json()
        resp = await _get(client, app_id, view="review")
        assert resp.status_code == 200, resp.text
        lean = resp.json()
        assert set(lean) == KEYS and set(lean["review"]) == REVIEW_KEYS
        assert (lean["id"], lean["job_title"], lean["job_company"]) == (
            app_id, full["job"]["job_title"], full["job"]["job_company"])
        assert lean["country"] == full["job"]["country"]
        for k in ("artifact_id", "sha256", "lines", "state"):
            assert lean["review"][k] == full["review"][k]
        assert lean["review"]["artifact_id"] == cv_id and lean["review"]["sha256"] == sha(CV)
        assert len(lean["review"]["lines"]) == 2
        assert (lean["review"]["reviews_used"], lean["review"]["tries_left"], lean["review"]["last_downgraded"]) == (
            0, cv_review.MAX_WEAK, [])
        small, big = len(json.dumps(lean)), len(json.dumps(full))
        assert small < 2000 and big > 10 * small, (small, big)


@pytest.mark.asyncio
async def test_review_view_counts_tries_and_names_the_last_downgrade(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        app_id, cv_id = await _setup(client)
        bad = _review(cv_id, lines=[{"n": 2, "verdict": "unsupported", "evidence": []}])
        stored = await _stored(client, app_id, bad)
        assert stored["where"] == {"line_uncovered": [1], "line_unsupported": [2], "no_evidence": [2]}, stored
        review = (await _get(client, app_id, view="review")).json()["review"]
        # a re-review of the version just judged is no new try; the NEXT version has the other tries left
        assert (review["reviews_used"], review["tries_left"]) == (0, cv_review.MAX_WEAK)
        assert review["last_downgraded"] == stored["downgraded"] and review["state"] == "weak"
        assert review["last_where"] == stored["where"]  # the exact lines to rewrite
        v2 = CV.replace("- Data engineer", "* Data engineer")
        await _save_cv(client, app_id, v2)
        review = (await _get(client, app_id, view="review")).json()["review"]
        assert (review["reviews_used"], review["tries_left"]) == (1, cv_review.MAX_WEAK - 1)
        assert review["last_downgraded"] == [] and review["last_where"] == {} and review["state"] == "none"


@pytest.mark.asyncio
async def test_review_view_without_a_cv_has_no_review_and_default_view_is_unchanged(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        brought = await client.post("/api/jobs/bring", json={
            "title": "Analyst", "company": "Acme", "apply_url": "https://example.com/jobs/1",
            "description": "Analyse things."})
        assert brought.status_code == 200, brought.text
        app_id = int(brought.json()["application_id"])
        lean = (await _get(client, app_id, view="review")).json()
        assert set(lean) == KEYS and lean["review"] is None
        full = (await _get(client, app_id)).json()
        assert {"events", "job", "contacts", "asks", "next_step", "review"} <= set(full)


@pytest.mark.asyncio
async def test_an_unknown_view_is_422_and_names_the_valid_one(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        app_id, _ = await _setup(client)
        resp = await _get(client, app_id, view="everything")
        assert resp.status_code == 422 and "review" in resp.text


@pytest.mark.asyncio
async def test_another_users_application_is_the_same_404_in_the_review_view(authenticated_async_context):
    async with authenticated_async_context() as owner:
        brought = await owner.post("/api/jobs/bring", json={
            "title": "Analyst", "company": "Acme", "apply_url": "https://example.com/jobs/2", "description": "x"})
        app_id = int(brought.json()["application_id"])
        plain = await _get(owner, 987654321)
    cookie = await _second_user_session_cookie("review-view-intruder@example.com")
    async with _session_client(cookie) as intruder:
        full, lean = await _get(intruder, app_id), await _get(intruder, app_id, view="review")
        assert (full.status_code, lean.status_code) == (404, 404)
        assert lean.json() == full.json() == plain.json()


@pytest.mark.asyncio
async def test_the_mcp_tool_serves_the_review_view_and_refuses_an_unknown_one(
    authenticated_async_context, fixture_user_id
):
    from src.api.mcp_server import mcp_runtime

    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        app_id, cv_id = await _setup(client)
        token = (await client.post("/api/tokens", json={"name": "claude-code"})).json()["token"]
        http_review = (await _get(client, app_id, view="review")).json()
    async with mcp_runtime():
        async with _mcp_client(token) as mcp:
            got = _payload(await mcp.call_tool("get_application", {"application_id": app_id, "view": "review"}))
            bad = await mcp.call_tool("get_application", {"application_id": app_id, "view": "everything"})
    assert got == http_review and set(got) == KEYS and got["review"]["artifact_id"] == cv_id
    assert bad.is_error and "review" in bad.content[0].text
