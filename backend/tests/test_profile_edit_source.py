"""Feature 9 - confirmed vs inferred facts (migration 0057_profile_edit_source).

``set_by`` says who WROTE a fact; ``source`` says whether the USER said it.
Helpers are copied, never imported from another test module (a cross-module
fixture import breaks schema isolation).
"""
from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

LOGI = "user_info.logistics"
NOTICE = {"notice_period": "1 month"}


def _seed_profile(user_id: str) -> None:
    from src.services.profile.models import CVData, UserPreferences, UserProfile
    from src.services.profile.storage import save_profile

    save_profile(
        UserProfile(
            cv_data=CVData(raw_text="Python data engineer.", name="Ada Lovelace", skills=["Python"]),
            preferences=UserPreferences(target_job_titles=["Data Engineer"]),
        ),
        user_id,
        source_action="cv_upload",
    )


async def _mint_token(client: AsyncClient) -> str:
    resp = await client.post("/api/tokens", json={"name": "claude-code"})
    assert resp.status_code == 201, resp.text
    return resp.json()["token"]


def _bearer_client(token: str) -> AsyncClient:
    from src.api.main import app

    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    )


def _edit(source: str | None = None, value: Any = NOTICE) -> dict[str, Any]:
    row: dict[str, Any] = {"path": LOGI, "value": value}
    if source is not None:
        row["source"] = source
    return row


@pytest.mark.asyncio
async def test_agent_edit_defaults_to_agent_inferred(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        token = await _mint_token(client)
    async with _bearer_client(token) as agent:
        resp = await agent.patch("/api/profile", json={"edits": [_edit()]})
        assert resp.status_code == 200, resp.text
        body = (await agent.get("/api/profile")).json()
    row = next(r for r in body["agent_edits"] if r["path"] == LOGI)
    assert row["source"] == "agent_inferred"
    assert row["confirmed"] is False


@pytest.mark.asyncio
async def test_agent_can_mark_user_said(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        token = await _mint_token(client)
    async with _bearer_client(token) as agent:
        resp = await agent.patch("/api/profile", json={"edits": [_edit("user_said")]})
        assert resp.status_code == 200, resp.text
        body = (await agent.get("/api/profile")).json()
    row = next(r for r in body["agent_edits"] if r["path"] == LOGI)
    assert row["source"] == "user_said"
    assert row["confirmed"] is True


@pytest.mark.asyncio
async def test_web_edit_is_user_said_and_refuses_inferred(authenticated_async_context, fixture_user_id):
    from src.services.profile import edits

    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        refused = await client.patch("/api/profile", json={"edits": [_edit("agent_inferred")]})
        assert refused.status_code == 422, refused.text
        assert edits.current_overlay(fixture_user_id) == [], "a refused call writes nothing"
        ok = await client.patch("/api/profile", json={"edits": [_edit()]})
        assert ok.status_code == 200, ok.text
        rows = edits.path_history(fixture_user_id, LOGI, 5)
    assert [r["source"] for r in rows] == ["user_said"]
    assert rows[0]["set_by"] == "web"


@pytest.mark.asyncio
async def test_get_profile_unconfirmed_absent_when_none(authenticated_async_context, fixture_user_id):
    pytest.importorskip("mcp")
    from src.api import mcp_server

    _seed_profile(fixture_user_id)
    async with authenticated_async_context():
        tool = mcp_server.build_server()._tool_manager.get_tool("get_profile")
        assert tool is not None
        mcp_server._current_user.set(mcp_server.CurrentUser(
                id=fixture_user_id, email="e2e@example.com", auth_via="token", actor_name="claude-code"
            ))
        try:
            bare = await tool.fn()
            update = mcp_server.build_server()._tool_manager.get_tool("update_profile")
            assert update is not None
            await update.fn([_edit("user_said")])
            said = await tool.fn()
            await update.fn([_edit(None, {"notice_period": "2 months"})])
            inferred = await tool.fn()
        finally:
            mcp_server._current_user.set(None)
    assert "unconfirmed" not in bare
    assert "unconfirmed" not in said
    assert inferred["unconfirmed"] == [LOGI]


@pytest.mark.asyncio
async def test_keep_confirms_inferred_fact(authenticated_async_context, fixture_user_id):
    from src.services.profile import edits

    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        token = await _mint_token(client)
    async with _bearer_client(token) as agent:
        assert (await agent.patch("/api/profile", json={"edits": [_edit()]})).status_code == 200
    async with authenticated_async_context() as client:
        kept = await client.post("/api/profile/edits/keep", json={"path": LOGI})
        assert kept.status_code == 200, kept.text
        newest = edits.path_history(fixture_user_id, LOGI, 1)[0]
        assert newest["set_by"] == "web"
        assert newest["source"] == "user_said"
        assert edits.is_confirmed(newest["set_by"], newest["source"])
        assert kept.json()["agent_edits"] == []


def test_kit_marks_inferred_answer_unconfirmed():
    from src.services.applications import kit
    from src.services.profile.models import CVData, UserInfo, UserProfile

    profile = UserProfile(cv_data=CVData(), user_info=UserInfo(logistics=dict(NOTICE)))

    def row(source: str, set_by: str = "agent:Claude") -> list[dict[str, Any]]:
        return [{"path": LOGI, "value": NOTICE, "set_by": set_by, "set_at": "2026-10-01T00:00:00Z", "source": source}]

    inferred, _ = kit.compute_answers(profile, row("agent_inferred"), "GB")
    said, _ = kit.compute_answers(profile, row("user_said"), "GB")
    web, _ = kit.compute_answers(profile, row("user_said", "web"), "GB")
    assert inferred["logistics"][0]["confirmed"] is False
    assert "confirmed" not in said["logistics"][0]
    assert "confirmed" not in web["logistics"][0]
    assert any("confirmed:false" in line for line in kit.KIT_INSTRUCTIONS)


@pytest.mark.asyncio
async def test_legacy_agent_rows_read_unconfirmed(authenticated_async_context, fixture_user_id):
    """A row written before migration 0057 has source '' - nobody confirmed it."""
    import json

    from src.core.settings import DB_PATH
    from src.repositories import pgsync

    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        with pgsync.connect(str(DB_PATH)) as conn:
            conn.execute(
                "INSERT INTO profile_edits (user_id, path, value, set_by, set_at) VALUES (?, ?, ?, ?, ?)",
                (fixture_user_id, LOGI, json.dumps(NOTICE), "agent:Claude", "2026-09-01T00:00:00Z"),
            )
        body = (await client.get("/api/profile")).json()
    row = next(r for r in body["agent_edits"] if r["path"] == LOGI)
    assert row["source"] == ""
    assert row["confirmed"] is False
