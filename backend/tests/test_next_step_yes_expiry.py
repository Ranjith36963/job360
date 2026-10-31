"""next_step says ``submit_now`` only while the user's yes still holds (the submit gate's rule);
an expired or CV-stale yes reads ``confirm_submit`` "Your yes ran out" on both the detail and the list."""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient

from src.core import settings
from src.repositories import pgsync

_AD = {
    "title": "AI Engineer", "company": "QuantCo", "location": "London",
    "apply_url": "https://jobs.lever.co/quantco-/1", "description": "RAG, Python, LLM APIs.",
}
CV = "Ada Lovelace\nPython."
RAN_OUT = {"code": "confirm_submit", "label": "Your yes ran out — say yes again"}


async def _approved(client: AsyncClient) -> int:
    app_id = int((await client.post("/api/jobs/bring", json=_AD)).json()["application_id"])
    await client.put(f"/api/applications/{app_id}/fit", json={"score": 68, "verdict": "good", "score_source": "full_ad"})
    await client.post(f"/api/applications/{app_id}/artifacts", json={"kind": "cv", "text": CV})
    r = await client.post(
        f"/api/applications/{app_id}/events",
        json={"event_type": "form_filled", "payload": {"form_url": "https://jobs.lever.co/x", "fields_count": 2}},
    )
    assert r.status_code == 201, r.text
    cv = next(a for a in (await client.get(f"/api/applications/{app_id}")).json()["artifacts"] if a["kind"] == "cv")
    r = await client.post(
        f"/api/applications/{app_id}/events",
        json={"event_type": "submit_approved", "payload": {
            "artifact_id": cv["id"], "sha256": hashlib.sha256(CV.encode()).hexdigest(), "where": "web"}},
    )
    assert r.status_code == 201, r.text
    return app_id


async def _both(client: AsyncClient, app_id: int) -> tuple[dict, dict]:
    detail = (await client.get(f"/api/applications/{app_id}")).json()["next_step"]
    rows = (await client.get("/api/applications")).json()["applications"]
    return detail, next(a for a in rows if a["id"] == app_id)["next_step"]


@pytest.mark.asyncio
async def test_fresh_yes_is_submit_now(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _approved(client)
        detail, listed = await _both(client, app_id)
        assert detail == listed
        assert detail["code"] == "submit_now"


@pytest.mark.asyncio
async def test_expired_yes_asks_again_on_detail_and_list(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _approved(client)
        old = (datetime.now(timezone.utc) - timedelta(minutes=settings.SUBMIT_APPROVAL_TTL_MINUTES + 5)).isoformat()
        conn = pgsync.connect(str(settings.DB_PATH))
        conn.execute(
            "UPDATE application_events SET recorded_at = ? WHERE application_id = ? AND event_type = 'submit_approved'",
            (old, app_id),
        )
        conn.commit()
        conn.close()
        detail, listed = await _both(client, app_id)
        assert detail == listed == RAN_OUT


@pytest.mark.asyncio
async def test_cv_edited_after_the_yes_asks_again(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _approved(client)
        await client.post(f"/api/applications/{app_id}/artifacts", json={"kind": "cv", "text": CV + "\nRust."})
        detail, listed = await _both(client, app_id)
        assert detail == listed == RAN_OUT
