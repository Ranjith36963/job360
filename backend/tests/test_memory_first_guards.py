"""Memory first S5 - mark_done(batch) only from a run that claimed the batch.

Prod: in 4 runs an Apply routine called ``mark_done(batch)`` after a plain ``whats_due()`` (which claims
nothing) or with no claim at all, closing a batch it never owned and pushing the next one back. Now an
assistant's ``mark_done`` batch is refused 409 unless ``whats_due(duties=["batch"])`` claimed it less than
``DUTY_CLAIM_MINUTES`` ago, and nothing is recorded. The website never claims, so its own mark is not
refused. Helpers are defined here, never imported from another test module.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.core import settings
from src.services.profile import schedule
from src.services.profile.models import CVData, UserPreferences, UserProfile

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)
REFUSAL = 'You did not claim the batch: call whats_due(duties=["batch"]) first. Nothing was recorded.'


async def _setup(client: AsyncClient, user_id: str) -> AsyncClient:
    """A due batch (one queued job, the assistant may apply on its own) and an agent token client."""
    from src.api.main import app
    from src.services.profile.storage import save_profile

    save_profile(UserProfile(cv_data=CVData(raw_text="Python.", name="Ada"),
                             preferences=UserPreferences(target_job_titles=["Data Engineer"])),
                 user_id, source_action="cv_upload")
    job = await client.post("/api/jobs/bring", json={
        "title": "Data Engineer", "company": "Northwind", "location": "Remote", "queue": True,
        "apply_url": "https://northwind.example/careers/7", "description": "Build the pipelines. " * 8,
    })
    assert job.status_code == 200, job.text
    on = await client.patch("/api/profile", json={
        "edits": [{"path": "assistant_settings.submit_mode", "value": "auto_when_sure"}]})
    assert on.status_code == 200, on.text
    token = (await client.post("/api/tokens", json={"name": "apply-routine"})).json()["token"]
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"})


def _batch_rows() -> set[str]:
    """Every batch slot the user has: the stamp, the claim and the tick (a mark writes the tick)."""
    from src.repositories import pgsync

    with pgsync.connect(str(settings.DB_PATH)) as conn:
        rows = conn.execute("SELECT duty FROM assistant_duty_runs").fetchall()
    return {str(r[0]) for r in rows} & {"batch", "tick", schedule.RUNNING + "batch"}


async def _close(agent: AsyncClient, **body: Any):
    return await agent.post("/api/schedule/done", json={"duty": "batch", **body})


@pytest.mark.asyncio
async def test_a_batch_never_claimed_is_refused_and_nothing_is_recorded(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        async with await _setup(client, fixture_user_id) as agent:
            for body in ({}, {"outcome": "partial", "reason": "rate_limited"}, {"outcome": "failed", "reason": "error"}):
                refused = await _close(agent, **body)
                assert refused.status_code == 409, refused.text
                assert refused.json()["detail"] == REFUSAL
            assert _batch_rows() == set()
            look = (await agent.get("/api/schedule/due")).json()
            assert look["last_done_at"]["batch"] is None and look["last_tick_at"] is None


@pytest.mark.asyncio
async def test_a_plain_whats_due_claims_nothing_so_the_close_is_refused(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        async with await _setup(client, fixture_user_id) as agent:
            plain = (await agent.get("/api/schedule/due")).json()
            assert [d["duty"] for d in plain["due"]] == ["batch"] and plain["running"] == {}
            refused = await _close(agent)
            assert (refused.status_code, refused.json()["detail"]) == (409, REFUSAL)
            assert _batch_rows() == set()


@pytest.mark.asyncio
async def test_the_run_that_claimed_the_batch_closes_it(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        async with await _setup(client, fixture_user_id) as agent:
            mine = (await agent.get("/api/schedule/due", params={"duties": ["batch"]})).json()
            assert [d["duty"] for d in mine["due"]] == ["batch"]
            done = await _close(agent)
            assert done.status_code == 200, done.text
            assert (done.json()["duty"], done.json()["outcome"]) == ("batch", "done")
            assert _batch_rows() == {"batch", "tick"}  # stamped, and the claim is freed
            # the claim was used up: a second close from the same run is refused
            assert (await _close(agent)).status_code == 409


@pytest.mark.asyncio
async def test_an_expired_claim_is_refused(authenticated_async_context, fixture_user_id, monkeypatch):
    async with authenticated_async_context() as client:
        async with await _setup(client, fixture_user_id) as agent:
            mine = (await agent.get("/api/schedule/due", params={"duties": ["batch"]})).json()
            assert [d["duty"] for d in mine["due"]] == ["batch"]
            monkeypatch.setattr(settings, "DUTY_CLAIM_MINUTES", 0)  # every claim is past its window
            refused = await _close(agent)
            assert (refused.status_code, refused.json()["detail"]) == (409, REFUSAL)
            assert _batch_rows() == {schedule.RUNNING + "batch"}  # the old claim only; no stamp, no tick


@pytest.mark.asyncio
async def test_a_claim_lives_for_the_hold_window_on_the_fixture_clock(migrated_db_path):
    from src.repositories import pg as _pg
    from src.repositories.database import JobDatabase

    async with _pg.connect(migrated_db_path) as conn:
        await conn.execute("INSERT INTO users (id, email, password_hash, created_at) VALUES (?, ?, ?, ?)",
                           ("u-s5", "s5@example.com", "!", NOW.isoformat()))
        await conn.commit()
    db = JobDatabase(migrated_db_path)
    await db.init_db()
    try:
        window = timedelta(minutes=settings.DUTY_CLAIM_MINUTES)
        assert await schedule.claim_live(db, "u-s5", "batch", NOW) is False  # never claimed
        assert await schedule.claim(db, "u-s5", ["batch"], NOW) == {"batch"}
        assert await schedule.claim_live(db, "u-s5", "batch", NOW + window - timedelta(seconds=1)) is True
        assert await schedule.claim_live(db, "u-s5", "batch", NOW + window) is False
        assert await schedule.claim_live(db, "u-s5", "hunt", NOW) is False  # a claim is per duty
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_the_website_and_other_duties_are_unchanged(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        async with await _setup(client, fixture_user_id) as agent:
            assert (await agent.post("/api/schedule/done", json={"duty": "inbox"})).status_code == 200
            web = await _close(client)  # the website never claims, so its own mark is not refused
            assert web.status_code == 200, web.text


@pytest.mark.asyncio
async def test_the_mcp_mark_done_holds_the_same_gate(authenticated_async_context, fixture_user_id):
    from src.api import mcp_server as m
    from src.api.auth_deps import CurrentUser

    tools = m.build_server()._tool_manager
    async with authenticated_async_context() as client:
        async with await _setup(client, fixture_user_id):
            token = m._current_user.set(CurrentUser(
                id=fixture_user_id, email="e2e@example.com", email_verified=True, auth_via="token", actor_name="cc"))
            try:
                with pytest.raises(Exception) as refused:  # noqa: B017 - the tool error wraps the 409
                    await tools.get_tool("mark_done").fn(duty="batch")
                assert "did not claim the batch" in str(refused.value)
                assert _batch_rows() == set()
                due = await tools.get_tool("whats_due").fn(duties=["batch"])
                assert [d["duty"] for d in due["due"]] == ["batch"]
                assert (await tools.get_tool("mark_done").fn(duty="batch"))["duty"] == "batch"
            finally:
                m._current_user.reset(token)
