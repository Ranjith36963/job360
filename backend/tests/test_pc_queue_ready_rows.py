"""The PC queue offers only applications the PC run can take to check_submit's yes; ready rows come first.

Production, 2026-11-19 (read-only funnel audit on efee745): the owner's hourly own-browser run was handed 676, 677
and 679 first, all three ``fix_cv``. Each got ``submit_preview`` ``ask / cv_not_seen`` and the run stopped, never
reaching the 116 PC rows whose CV a strong review cleared. ``own_browser._application_tasks`` queued every
own-browser application whatever its CV: of 288 PC rows, 21 had no CV, 87 an unreviewed CV and 24 a weak review.
The queue label also read "apply from your PC" on the CV-less rows (1064, 1131, 1132).

Real rows through the real routes; value asserts (rule #21). Helpers are copied, never imported.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.api import dependencies as api_deps
from src.services.applications import blockers as blockers_service
from src.services.applications import cv_review, kit
from src.services.profile.models import CVData, UserPreferences, UserProfile


async def _seed(client: AsyncClient, user_id: str) -> None:
    from src.services.profile.storage import save_profile

    save_profile(
        UserProfile(
            cv_data=CVData(raw_text="Python data engineer.", name="Ada Lovelace", skills=["Python"]),
            preferences=UserPreferences(target_job_titles=["Data Engineer"]),
        ),
        user_id,
        source_action="cv_upload",
    )
    edits = [{"path": "assistant_settings.submit_mode", "value": "auto_when_sure"},
             {"path": "assistant_settings.cv_auto_approve", "value": True}]
    resp = await client.patch("/api/profile", json={"edits": edits})
    assert resp.status_code == 200, resp.text


async def _easy_apply(client: AsyncClient, n: int) -> int:
    """A batch-queued application on linkedin.com: the PC queue's from the start (Easy Apply)."""
    resp = await client.post("/api/jobs/bring", json={
        "title": f"Data Engineer {n}", "company": f"Northwind {n}", "location": "London", "country": "GB",
        "remote": False, "queue": True, "apply_url": f"https://www.linkedin.com/jobs/view/{4100000000 + n}",
        "description": "Build the pipelines. " * 8,
    })
    assert resp.status_code == 200 and resp.json()["queued"] is True, resp.text
    return int(resp.json()["application_id"])


async def _cv(client: AsyncClient, app_id: int) -> tuple[int, str]:
    text = f"Ada Lovelace\nPython data engineer, application {app_id}."
    resp = await client.post(f"/api/applications/{app_id}/artifacts", json={"kind": "cv", "text": text})
    assert resp.status_code in (200, 201), resp.text
    return int(resp.json()["artifact_id"]), kit.sha256_text(text)


async def _review(user_id: str, app_id: int, artifact_id: int, sha: str, confidence: str) -> None:
    """A stored ``cv_reviewed`` of exactly that CV (the CV run's verdict), as tests/_cv_review.owners_turn stores one."""
    db = await api_deps.get_db()
    now = datetime.now(timezone.utc).isoformat()
    payload = {"artifact_id": artifact_id, "sha256": sha, "confidence": confidence, "rules": cv_review.RULES}
    await db._db.execute(
        "INSERT INTO application_events (user_id, application_id, event_type, detail, payload, occurred_at, "
        "recorded_at, recorded_by) VALUES (?, ?, ?, '', ?, ?, ?, 'test')",
        (user_id, app_id, cv_review.EVENT_TYPE, json.dumps(payload), now, now),
    )
    await db._db.commit()


async def _agent(client: AsyncClient) -> AsyncClient:
    from src.api.main import app

    token = (await client.post("/api/tokens", json={"name": "agent"})).json()["token"]
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"})


async def _rows(client: AsyncClient, user_id: str) -> dict[str, int]:
    """Queued oldest first: the CV-less row, the weak one, then the ready one, then a ready one with an open ask."""
    no_cv = await _easy_apply(client, 1)
    weak = await _easy_apply(client, 2)
    ready = await _easy_apply(client, 3)
    asked = await _easy_apply(client, 4)
    art, sha = await _cv(client, weak)
    await _review(user_id, weak, art, sha, "weak")
    for app_id in (ready, asked):
        art, sha = await _cv(client, app_id)
        await _review(user_id, app_id, art, sha, "strong")
    ask = await client.post("/api/asks", json={"question": "Notice period?", "application_id": asked})
    assert ask.status_code in (200, 201), ask.text
    return {"no_cv": no_cv, "weak": weak, "ready": ready, "asked": asked}


@pytest.mark.asyncio
async def test_the_pc_queue_lists_only_the_row_check_submit_can_pass_and_counts_the_rest(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        await _seed(client, fixture_user_id)
        ids = await _rows(client, fixture_user_id)
        async with await _agent(client) as agent:
            resp = await agent.get("/api/own-browser/queue", params={"session": "pc-0908"})
            assert resp.status_code == 200, resp.text
            body = resp.json()
    assert [t["task_id"] for t in body["tasks"]] == [f"application:{ids['ready']}"], body["tasks"]
    assert body["count"] == 1
    assert body["applications"] == {
        "ready": 1, "held_back": {"cv_not_strong": 1, "no_cv": 1, "waiting_on_owner": 1},
    }, body["applications"]


@pytest.mark.asyncio
async def test_take_hands_the_ready_row_first_though_the_unready_ones_were_queued_before_it(
    authenticated_async_context, fixture_user_id
):
    """09:08: the run was handed 676 / 677 / 679 (fix_cv) first and stopped; the ready row must come first."""
    async with authenticated_async_context() as client:
        await _seed(client, fixture_user_id)
        ids = await _rows(client, fixture_user_id)
        async with await _agent(client) as agent:
            got = await agent.post("/api/own-browser/take", json={"session": "pc-0908"})
            assert got.status_code == 200, got.text
            body = got.json()
    assert body["status"] == "task" and body["task"]["application_id"] == ids["ready"], body


@pytest.mark.asyncio
async def test_a_cv_the_user_saw_is_ready_without_a_review(authenticated_async_context, fixture_user_id):
    """check_submit's other half of cv_not_seen: the user's own ``cv_seen`` of that exact CV clears it."""
    async with authenticated_async_context() as client:
        await _seed(client, fixture_user_id)
        app_id = await _easy_apply(client, 5)
        art, sha = await _cv(client, app_id)
        seen = await client.post(f"/api/applications/{app_id}/events", json={
            "event_type": "cv_seen", "payload": {"artifact_id": art, "sha256": sha, "where": "web"}})
        assert seen.status_code == 201, seen.text
        body = (await client.get("/api/own-browser/queue")).json()
    assert [t["task_id"] for t in body["tasks"]] == [f"application:{app_id}"], body
    assert body["applications"]["ready"] == 1


@pytest.mark.asyncio
async def test_a_cv_less_pc_row_reads_its_true_next_step_not_apply_from_your_pc(
    authenticated_async_context, fixture_user_id
):
    """1064 / 1131 / 1132 read "queued: apply from your PC" with no CV; the PC queue does not offer them."""
    async with authenticated_async_context() as client:
        await _seed(client, fixture_user_id)
        app_id = await _easy_apply(client, 6)
        fit = await client.put(f"/api/applications/{app_id}/fit",
                               json={"score": 80, "verdict": "my read", "score_source": "full_ad"})
        assert fit.status_code == 200, fit.text
    db = await api_deps.get_db()
    rows: list[dict[str, Any]] = (await blockers_service.blockers_for(db, fixture_user_id, [app_id]))[app_id]
    assert [b for b in rows if b["kind"] == "queued"] == [], rows
    step = [(b["actor"], b["label"]) for b in rows if b["kind"] == "assistant_step"]
    assert step == [("assistant", "write the CV")], rows
