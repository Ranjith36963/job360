"""Owner issue #8: a re-bring must say what happened. Whenever bring returns an application the
caller already has, the reply is explicit and closed: `existing`, `application_id`, `matched_on`
(closed set `BRING_MATCHED_ON`), `differs` (field NAMES only) and a one-line `next`; the 409 for a
different posting carries the same fields. Real HTTP + MCP doors on the real Postgres.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from tests.test_bring_duplicate_guard import _AD, _GLASGOW, _count_apps, _mcp_client, _payload, _token

_JURO: dict[str, Any] = {
    "title": "Backend Engineer", "company": "Juro", "location": "London, UK",
    "apply_url": "https://juro.example/jobs/55", "description": "Build contracts. SECRET-AD-TEXT.",
    "country": "GB", "remote": False, "found_on": "company_careers",
}


@pytest.mark.asyncio
async def test_closed_set_of_matched_on_is_pinned() -> None:
    from src.api.routes.bring import BRING_MATCHED_ON

    assert BRING_MATCHED_ON == ("same_title_and_company", "same_url", "same_company_and_role", "repost")


@pytest.mark.asyncio
async def test_same_job_rebring_says_existing_and_what_differs(authenticated_async_context, fixture_user_id):
    from src.api.mcp_server import mcp_runtime

    async with authenticated_async_context() as client:
        first = (await client.post("/api/jobs/bring", json=_JURO)).json()
        token = await _token(client)
    before = await _count_apps()
    async with mcp_runtime(), _mcp_client(token) as mcp:
        out = _payload(await mcp.call_tool("bring_job", {**_JURO, "remote": True, "found_on": "linkedin"}))
    assert out["existing"] is True and out["application_id"] == first["application_id"]
    assert out["matched_on"] == "same_title_and_company"
    assert out["differs"] == ["remote", "found_on"]
    assert f"#{first['application_id']}" in out["next"] and "update_job" in out["next"]
    assert "SECRET-AD-TEXT" not in out["next"] and "juro.example" not in out["next"]
    # the re-bring already saved remote and found_on: `next` says so, never "fix them" as if still old
    assert "Overwritten with this ad's values: remote, found_on" in out["next"]
    assert "differs from this ad in" not in out["next"]
    assert await _count_apps() == before


@pytest.mark.asyncio
async def test_open_role_guard_reply_names_the_match_and_the_fields(authenticated_async_context, fixture_user_id):
    from src.api.mcp_server import mcp_runtime

    async with authenticated_async_context() as client:
        first = (await client.post("/api/jobs/bring", json={**_AD, "remote": False})).json()
        token = await _token(client)
    async with mcp_runtime(), _mcp_client(token) as mcp:
        out = _payload(await mcp.call_tool("bring_job", {**_GLASGOW, "remote": True}))
    assert out["duplicate_of"] == first["application_id"] == out["application_id"]
    assert out["existing"] is True and out["matched_on"] == "same_company_and_role"
    assert out["differs"] == ["apply_url", "location", "title", "remote"]
    assert out["remote"] is False, "the guard changed nothing: the stored value still shows"
    assert "nothing new was created or changed" in out["next"] and "update_job" in out["next"]


@pytest.mark.asyncio
async def test_near_duplicate_409_is_structured(authenticated_async_context, fixture_user_id):
    from src.api.mcp_server import mcp_runtime

    async with authenticated_async_context() as client:
        first = (await client.post("/api/jobs/bring", json=_JURO)).json()
        resp = await client.post("/api/jobs/bring", json={**_JURO, "location": "New York, US", "remote": True})
        assert resp.status_code == 409, resp.text
        body = resp.json()
        token = await _token(client)
    assert "differs from it in: location" in body["detail"], "the sentence clients read is unchanged"
    assert body["existing"] is True and body["application_id"] == first["application_id"]
    assert body["matched_on"] == "same_title_and_company"
    assert body["differs"] == ["location", "remote"]
    assert f"#{first['application_id']}" in body["next"] and "SECRET-AD-TEXT" not in json.dumps(body)
    async with mcp_runtime(), _mcp_client(token) as mcp:
        res = await mcp.call_tool("bring_job", {**_JURO, "location": "New York, US"})
    text = res.content[0].text
    assert res.is_error and "bring_job: 409:" in text
    assert f'"application_id": {first["application_id"]}' in text and '"matched_on": "same_title_and_company"' in text
    assert '"differs": ["location"]' in text


@pytest.mark.asyncio
async def test_a_new_job_is_still_created(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        first = (await client.post("/api/jobs/bring", json=_JURO)).json()
        assert first["existing"] is False and first["matched_on"] is None
        assert first["differs"] == [] and first["next"] is None
        other = (await client.post("/api/jobs/bring", json={**_JURO, "company": "Evaboot", "title": "Data Engineer"})).json()
    assert other["existing"] is False and other["application_id"] != first["application_id"]
    assert other["matched_on"] is None and other["differs"] == [] and other["next"] is None
