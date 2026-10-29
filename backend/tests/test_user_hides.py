"""S11a (owner 2026-10-29): the user hides one CV version or one timeline note from the web.

A hide is an appended correcting event (M3: nothing is updated or deleted), recorded only by the
user's web session; undo is another one. A hidden version is never the current CV (tailored
documents, the kit's pick, the CV to approve), and a receipt that names it still shows it.
Helpers are defined here, never imported (per-test schema isolation).
"""
from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.core import settings
from src.repositories import pgsync

_AD = {
    "title": "Data Engineer", "company": "Larkspur", "location": "London", "country": "GB", "remote": False,
    "apply_url": "https://larkspur.example/careers/7", "description": "Pipelines and Python.",
}


def _rows(app_id: int) -> list[tuple[Any, ...]]:
    with pgsync.connect(str(settings.DB_PATH)) as conn:
        rows = conn.execute(
            "SELECT id, event_type, detail, corrects_event_id, recorded_by FROM application_events "
            "WHERE application_id = ? ORDER BY id", (app_id,),
        ).fetchall()
    return [(r["id"], r["event_type"], r["detail"], r["corrects_event_id"], r["recorded_by"]) for r in rows]


def _agent(token: str) -> AsyncClient:
    from src.api.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"})


async def _bring(client: AsyncClient) -> tuple[int, int]:
    resp = await client.post("/api/jobs/bring", json=_AD)
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"]), int(resp.json()["job"]["id"])


async def _save(client: AsyncClient, app_id: int, text: str) -> dict[str, Any]:
    resp = await client.post(f"/api/applications/{app_id}/artifacts", json={"kind": "cv", "text": text})
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _tailored_cv(client: AsyncClient, job_id: int) -> dict[str, Any]:
    bundle = await client.get(f"/api/tailor/{job_id}")
    assert bundle.status_code == 200, bundle.text
    return {d["doc_kind"]: d for d in bundle.json()["documents"]}["cv"]


@pytest.mark.asyncio
async def test_a_hidden_cv_version_is_never_the_current_one_and_undo_brings_it_back(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id, job_id = await _bring(client)
        v1 = await _save(client, app_id, "the real cv")
        v2 = await _save(client, app_id, "NEGATIVE TEST cv")
        hide = await client.post(f"/api/applications/{app_id}/artifacts/{v2['artifact_id']}/hide")
        assert hide.status_code == 201, hide.text
        again = await client.post(f"/api/applications/{app_id}/artifacts/{v2['artifact_id']}/hide")
        assert again.status_code == 409, again.text

        cv = await _tailored_cv(client, job_id)
        assert (cv["artifact_id"], cv["text"]) == (v1["artifact_id"], "the real cv")
        to_approve = (await client.get("/api/cv-to-approve")).json()["items"]
        assert [r["cv"]["artifact_id"] for r in to_approve] == [v1["artifact_id"]]
        detail = (await client.get(f"/api/applications/{app_id}?with_artifact_text=true")).json()
        listed = {a["id"]: (a["hidden"], a["text"]) for a in detail["artifacts"]}
        assert listed == {v1["artifact_id"]: (False, "the real cv"), v2["artifact_id"]: (True, None)}
        saved = next(e for e in detail["events"] if e["payload"].get("artifact_id") == v2["artifact_id"])
        assert saved["superseded"] is True
        note = next(e for e in detail["events"] if e["corrects_event_id"] == saved["id"])
        assert (note["event_type"], note["detail"], note["recorded_by"]) == ("note", "Hidden by you", "web")

        shown = await client.post(f"/api/applications/{app_id}/artifacts/{v2['artifact_id']}/show")
        assert shown.status_code == 201, shown.text
        assert (await _tailored_cv(client, job_id))["artifact_id"] == v2["artifact_id"]
        detail = (await client.get(f"/api/applications/{app_id}")).json()
        assert {a["id"]: a["hidden"] for a in detail["artifacts"]} == {v1["artifact_id"]: False, v2["artifact_id"]: False}
        assert next(e for e in detail["events"] if e["id"] == saved["id"])["superseded"] is False
        # Append-only: the artifact row and every event are still stored.
        assert [r[1] for r in _rows(app_id)][-2:] == ["note", "note"]


@pytest.mark.asyncio
async def test_an_assistant_may_not_hide_a_version_or_undo_the_users_hide(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id, job_id = await _bring(client)
        v1 = await _save(client, app_id, "cv one")
        v2 = await _save(client, app_id, "cv two")
        saved = [r for r in _rows(app_id) if r[1] == "artifact_saved"][-1]
        token = (await client.post("/api/tokens", json={"name": "agent"})).json()["token"]
        async with _agent(token) as agent:
            route = await agent.post(f"/api/applications/{app_id}/artifacts/{v2['artifact_id']}/hide")
            assert route.status_code == 403, route.text
            note = await agent.post(f"/api/applications/{app_id}/events",
                                    json={"event_type": "note", "detail": "hide it", "corrects_event_id": saved[0]})
            assert note.status_code == 403, note.text
            assert note.json()["detail"] == "only the user hides a document version, with its Hide button on the web"
            assert (await _tailored_cv(agent, job_id))["artifact_id"] == v2["artifact_id"]
        assert (await client.post(f"/api/applications/{app_id}/artifacts/{v2['artifact_id']}/hide")).status_code == 201
        hide_id = _rows(app_id)[-1][0]
        async with _agent(token) as agent:
            undo = await agent.post(f"/api/applications/{app_id}/events",
                                    json={"event_type": "note", "detail": "show it", "corrects_event_id": hide_id})
            assert undo.status_code == 403, undo.text
            assert (await _tailored_cv(agent, job_id))["artifact_id"] == v1["artifact_id"]


@pytest.mark.asyncio
async def test_a_receipt_that_names_a_hidden_version_still_shows_it(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id, _ = await _bring(client)
        v1 = await _save(client, app_id, "sent cv")
        await _save(client, app_id, "later cv")
        receipt = await client.post(f"/api/applications/{app_id}/receipt",
                                    json={"channel": "company_site", "cv_artifact_id": v1["artifact_id"]})
        assert receipt.status_code == 201, receipt.text
        assert (await client.post(f"/api/applications/{app_id}/artifacts/{v1['artifact_id']}/hide")).status_code == 201
        detail = (await client.get(f"/api/applications/{app_id}")).json()
        assert [r["cv_artifact_id"] for r in detail["receipts"]] == [v1["artifact_id"]]
        row = (await client.get(f"/api/applications/{app_id}/artifacts/{v1['artifact_id']}")).json()
        assert row["text"] == "sent cv"


@pytest.mark.asyncio
async def test_the_user_hides_a_timeline_note_and_shows_it_again(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id, _ = await _bring(client)
        token = (await client.post("/api/tokens", json={"name": "agent"})).json()["token"]
        async with _agent(token) as agent:
            note = (await agent.post(f"/api/applications/{app_id}/events",
                                     json={"event_type": "note", "detail": "Doctor self-review."})).json()
        nid = note["event_id"]
        hidden = await client.post(f"/api/applications/{app_id}/events/{nid}/hide")
        assert hidden.status_code == 201, hidden.text
        events = {e["id"]: e for e in (await client.get(f"/api/applications/{app_id}")).json()["events"]}
        assert events[nid]["process"] is True
        async with _agent(token) as agent:
            refused = await agent.post(f"/api/applications/{app_id}/events",
                                       json={"event_type": "note", "detail": "undo",
                                             "corrects_event_id": hidden.json()["event_id"]})
            assert refused.status_code == 403, refused.text
            assert (await agent.post(f"/api/applications/{app_id}/events/{nid}/show")).status_code == 403
        shown = await client.post(f"/api/applications/{app_id}/events/{nid}/show")
        assert shown.status_code == 201, shown.text
        events = {e["id"]: e for e in (await client.get(f"/api/applications/{app_id}")).json()["events"]}
        assert (events[nid]["process"], events[nid]["superseded"]) == (False, False)
        assert events[shown.json()["event_id"]]["detail"] == "Shown again by you"
        assert (await client.post(f"/api/applications/{app_id}/events/{nid}/show")).status_code == 409
        again = await client.post(f"/api/applications/{app_id}/events/{nid}/hide")
        assert again.status_code == 201, again.text
        brought = next(e for e in events.values() if e["event_type"] == "brought")
        assert (await client.post(f"/api/applications/{app_id}/events/{brought['id']}/hide")).status_code == 422
