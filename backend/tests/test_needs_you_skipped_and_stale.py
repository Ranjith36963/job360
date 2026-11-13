"""Needs you F5 + F10 (production audit 2026-11-13).

* F5: a job the user's own rule drops (below the score line, ``below_line`` skip: the rule ``open_queue`` uses)
  raises no blocker on the user or the user's PC: no ``approve_cv``, no ``apply_by_hand``, no queued-on-your-PC,
  and no batch place. ``needs_you_count`` and ``schedule._waiting_on_you`` agree on it.
* F10: an open job with a CV, no fill and not queued is the ASSISTANT's ("queue or drop this job"), never the
  user's; the your-PC queued blocker reads "apply from your PC".

Real Postgres, real HTTP doors to seed, the services called directly to read. Helpers are copied, never imported
from another test module (schema isolation).
"""
from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from src.api import dependencies as api_deps
from src.services.applications import blockers, kit, needs_you_count, spine
from src.services.profile import schedule

OWNER_SIDE = ("you", "your_pc")


async def _seed(client: AsyncClient, user_id: str, **values: Any) -> None:
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
    edits = [{"path": f"assistant_settings.{k}", "value": v} for k, v in values.items()]
    resp = await client.patch("/api/profile", json={"edits": edits})
    assert resp.status_code == 200, resp.text


async def _settings(client: AsyncClient, **values: Any) -> None:
    edits = [{"path": f"assistant_settings.{k}", "value": v} for k, v in values.items()]
    resp = await client.patch("/api/profile", json={"edits": edits})
    assert resp.status_code == 200, resp.text


async def _bring(client: AsyncClient, n: int, *, queue: bool, url: str = "") -> int:
    resp = await client.post("/api/jobs/bring", json={
        "title": f"Data Engineer {n}", "company": f"Northwind {n}", "location": "Remote", "queue": queue,
        "apply_url": url or f"https://northwind.example/careers/{n}", "description": "Build the pipelines. " * 8,
    })
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _fit(client: AsyncClient, app_id: int, score: int) -> None:
    resp = await client.put(
        f"/api/applications/{app_id}/fit", json={"score": score, "verdict": "my read", "score_source": "full_ad"}
    )
    assert resp.status_code == 200, resp.text


async def _cv(client: AsyncClient, app_id: int, *, seen: bool = False) -> None:
    text = f"Ada Lovelace, CV for job {app_id}"
    resp = await client.post(f"/api/applications/{app_id}/artifacts", json={"kind": "cv", "text": text})
    assert resp.status_code == 201, resp.text
    art_id = int(resp.json()["artifact_id"])
    if seen:  # the user's own click: yes to this exact CV
        ok = await client.post(f"/api/applications/{app_id}/events", json={"event_type": "cv_seen", "payload": {
            "artifact_id": art_id, "sha256": kit.sha256_text(text), "where": "web",
        }})
        assert ok.status_code == 201, ok.text


async def _blockers(user_id: str, ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    db = await api_deps.get_db()
    return await blockers.blockers_for(db, user_id, ids)  # type: ignore[return-value]


async def _open_queue(db: Any, user_id: str) -> tuple[list[int], dict[int, str]]:
    from src.services.profile.storage import load_profile

    return await spine.open_queue(db, user_id, load_profile(user_id))


def _owner_side(rows: list[dict[str, Any]]) -> list[tuple[str, str]]:
    return [(b["kind"], b["label"]) for b in rows if b["actor"] in OWNER_SIDE]


async def _dropped_job(client: AsyncClient, user_id: str, *, queue: bool, n: int = 1) -> int:
    """A job brought (queued or not) while the user's rule was ``ask``, fit 35 under the line 70, given a CV,
    then the user's rule turns to skip: the open queue leaves it out (S4)."""
    await _seed(client, user_id, apply_min_score=70, below_line="ask")
    app_id = await _bring(client, n, queue=queue)
    await _fit(client, app_id, 35)
    await _cv(client, app_id)
    await _settings(client, below_line="skip")
    return app_id


# ── F5: a job the user's rule drops waits on nobody on the owner's side ───────


@pytest.mark.asyncio
async def test_a_queued_job_below_the_line_with_skip_raises_no_owner_blocker(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        app_id = await _dropped_job(client, fixture_user_id, queue=True)
    db = await api_deps.get_db()
    assert app_id not in (await _open_queue(db, fixture_user_id))[1], "open_queue drops it (S4)"
    rows = (await _blockers(fixture_user_id, [app_id]))[app_id]
    assert _owner_side(rows) == [], rows
    assert "queued" not in [b["kind"] for b in rows], "not in the batch: no batch place either"
    assert not [b for b in rows if b["kind"] in ("approve_cv", "review_cv", "fix_cv")], "no CV look for a dropped job"


@pytest.mark.asyncio
async def test_an_unqueued_job_below_the_line_with_skip_is_not_apply_by_hand(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        app_id = await _dropped_job(client, fixture_user_id, queue=False)
    rows = (await _blockers(fixture_user_id, [app_id]))[app_id]
    assert "apply_by_hand" not in [b["kind"] for b in rows]
    assert _owner_side(rows) == [], rows


@pytest.mark.asyncio
async def test_below_the_line_with_ask_still_works_the_job(authenticated_async_context, fixture_user_id):
    """Control: only ``skip`` drops. Below the line with ``ask`` stays in the batch, as open_queue keeps it."""
    async with authenticated_async_context() as client:
        await _seed(client, fixture_user_id, apply_min_score=70, below_line="ask")
        app_id = await _bring(client, 2, queue=True)
        await _fit(client, app_id, 35)
        await _cv(client, app_id, seen=True)
    rows = (await _blockers(fixture_user_id, [app_id]))[app_id]
    assert [(b["kind"], b["actor"]) for b in rows if b["kind"] == "queued"] == [("queued", "assistant")], rows


@pytest.mark.asyncio
async def test_needs_you_count_and_waiting_on_you_agree_on_a_dropped_job(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        dropped = await _dropped_job(client, fixture_user_id, queue=True)
    db = await api_deps.get_db()
    queue_ids, _ = await _open_queue(db, fixture_user_id)
    waiting = await schedule._waiting_on_you(db, fixture_user_id, queue_ids)
    count = await needs_you_count.needs_you_count(db, fixture_user_id)
    assert dropped not in count["job_ids"], count
    assert count["jobs"] == waiting == 0, (count, waiting)


# ── F10: a stale open job is the assistant's; the your-PC label is its own ────


@pytest.mark.asyncio
async def test_an_open_job_with_a_cv_never_queued_is_the_assistants_queue_or_drop(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        await _seed(client, fixture_user_id, apply_min_score=70, below_line="skip")
        app_id = await _bring(client, 3, queue=False)
        await _fit(client, app_id, 90)
        await _cv(client, app_id, seen=True)
    rows = (await _blockers(fixture_user_id, [app_id]))[app_id]
    assert "apply_by_hand" not in [b["kind"] for b in rows]
    assert _owner_side(rows) == [], rows
    step = [b for b in rows if b["kind"] == "assistant_step" and b["label"] == "queue or drop this job"]
    assert len(step) == 1 and step[0]["actor"] == "assistant", rows
    assert step[0]["ref"]["reason"] == "not_queued", step
    db = await api_deps.get_db()
    count = await needs_you_count.needs_you_count(db, fixture_user_id)
    assert app_id not in count["job_ids"] and count["by_actor"]["assistant"] == 1, count


@pytest.mark.asyncio
async def test_a_job_queued_on_the_users_pc_reads_apply_from_your_pc(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id = await _bring(client, 4, queue=True, url="https://www.linkedin.com/jobs/view/4242424242")
    rows = (await _blockers(fixture_user_id, [app_id]))[app_id]
    queued = [b for b in rows if b["kind"] == "queued"]
    assert [(b["actor"], b["label"]) for b in queued] == [("your_pc", "apply from your PC")], rows
