"""2026-11-18 (23:41 bulk apply run): batch was marked done at 23:41:49, before the run opened its one
workable row (1087, CV strong, opened 23:42). mark_done(batch) checked for work left only on outcome
partial/failed: the default outcome "done" closed the batch with a row that needed nothing from the owner.
"done" now runs the same check ("done" means nothing is left)."""
from __future__ import annotations

import pytest
from httpx import AsyncClient

from src.services.profile.models import CVData, UserPreferences, UserProfile


def _seed(user_id: str) -> None:
    from src.services.profile.storage import save_profile

    save_profile(UserProfile(cv_data=CVData(raw_text="Python.", name="Ada"),
                             preferences=UserPreferences(target_job_titles=["Data Engineer"])),
                 user_id, source_action="cv_upload")


async def _ready_row(client: AsyncClient) -> int:
    resp = await client.post("/api/jobs/bring", json={
        "title": "Junior Engineer", "company": "Whitespace", "location": "London", "queue": True,
        "apply_url": "https://whitespace.example/careers/1", "description": "Build the pipelines. " * 8,
    })
    assert resp.status_code == 200, resp.text
    app_id = int(resp.json()["application_id"])
    saved = await client.post(f"/api/applications/{app_id}/artifacts", json={"kind": "cv", "text": "Ada, engineer."})
    assert saved.status_code in (200, 201), saved.text
    return app_id


@pytest.mark.asyncio
async def test_done_with_a_row_left_is_refused_like_partial(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        app_id = await _ready_row(client)
        done = await client.post("/api/schedule/done", json={"duty": "batch", "outcome": "done"})
        assert done.status_code == 409, done.text
        assert f"next: application {app_id}" in done.json()["detail"]


@pytest.mark.asyncio
async def test_done_with_no_work_left_is_accepted(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        done = await client.post("/api/schedule/done", json={"duty": "batch", "outcome": "done"})
        assert done.status_code == 200, done.text
        assert done.json()["outcome"] == "done"
