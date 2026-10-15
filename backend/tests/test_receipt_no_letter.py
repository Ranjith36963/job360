"""A receipt never guesses a cover letter (weak spot #2, 2026-10-15).

`record_application` used to freeze the NEWEST letter when no id was named, so a
form with no letter field still produced a receipt claiming one was sent. Now only
a NAMED letter is frozen; otherwise the receipt records none (NULL) and says so.
The CV keeps its newest-when-unnamed behaviour, with a warning naming the version.
Rule #21: every assertion checks a real value.
"""
from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from tests.test_mcp_server import _mcp_client, _mint_token, _payload

_AD = {
    "title": "Data Engineer", "company": "Mactores", "location": "Remote",
    "apply_url": "https://mactores.example/jobs/1", "description": "Spark, Airflow, Python.",
}
_NO_LETTER_WARNING = (
    "no cover_letter_artifact_id given: the receipt says no cover letter was sent. "
    "If one was sent, record a correction note; receipts cannot be edited."
)


async def _setup(client: AsyncClient) -> tuple[int, dict[str, Any], dict[str, Any], dict[str, Any]]:
    resp = await client.post("/api/jobs/bring", json=_AD)
    assert resp.status_code == 200, resp.text
    app_id = int(resp.json()["application_id"])
    saved: list[dict[str, Any]] = []
    for kind, text in (("cv", "CV one"), ("cv", "CV two"), ("cover_letter", "Dear Mactores, hello")):
        r = await client.post(f"/api/applications/{app_id}/artifacts", json={"kind": kind, "text": text})
        assert r.status_code == 201, r.text
        saved.append(r.json())
    return app_id, saved[0], saved[1], saved[2]


@pytest.mark.asyncio
async def test_no_letter_id_records_no_letter_even_when_a_letter_exists(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id, _cv1, _cv2, _letter = await _setup(client)
        resp = await client.post(f"/api/applications/{app_id}/receipt", json={"channel": "company_site"})
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["cover_letter_artifact_id"] is None
        assert body["cover_letter_sent"] is False
        assert _NO_LETTER_WARNING in body["warnings"]
        rid = body["receipt_id"]

        full = (await client.get(f"/api/receipts/{rid}")).json()
        assert full["cover_letter_text"] is None
        assert full["cover_letter_sent"] is False
        assert full["cover_letter_version_no"] is None
        listed = (await client.get("/api/receipts")).json()["receipts"]
        assert [(r["id"], r["cover_letter_sent"]) for r in listed] == [(rid, False)]
        page = (await client.get(f"/api/applications/{app_id}")).json()["receipts"]
        assert [(r["id"], r["cover_letter_artifact_id"], r["cover_letter_sent"]) for r in page] == [
            (rid, None, False)
        ]


@pytest.mark.asyncio
async def test_named_letter_is_frozen_and_marked_sent(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id, _cv1, _cv2, letter = await _setup(client)
        resp = await client.post(
            f"/api/applications/{app_id}/receipt",
            json={"cover_letter_artifact_id": letter["artifact_id"]},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["cover_letter_artifact_id"] == letter["artifact_id"]
        assert body["cover_letter_sent"] is True
        assert _NO_LETTER_WARNING not in body["warnings"]
        rid = body["receipt_id"]

        full = (await client.get(f"/api/receipts/{rid}")).json()
        assert full["cover_letter_text"] == "Dear Mactores, hello"
        assert full["cover_letter_sent"] is True
        assert full["cover_letter_version_no"] == 1
        listed = (await client.get("/api/receipts")).json()["receipts"]
        assert [(r["id"], r["cover_letter_sent"]) for r in listed] == [(rid, True)]
        page = (await client.get(f"/api/applications/{app_id}")).json()["receipts"]
        assert page[0]["cover_letter_sent"] is True


@pytest.mark.asyncio
async def test_unnamed_cv_freezes_newest_and_warning_names_it(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id, _cv1, cv2, _letter = await _setup(client)
        resp = await client.post(f"/api/applications/{app_id}/receipt", json={})
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["cv_artifact_id"] == cv2["artifact_id"]
        assert body["cv_version_no"] == 2
        expected = f"cv_artifact_id not given: froze the newest CV (artifact {cv2['artifact_id']}, v2)"
        assert expected in body["warnings"]
        full = (await client.get(f"/api/receipts/{body['receipt_id']}")).json()
        assert full["cv_text"] == "CV two"


@pytest.mark.asyncio
async def test_named_cv_gets_no_cv_warning(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id, cv1, _cv2, _letter = await _setup(client)
        resp = await client.post(
            f"/api/applications/{app_id}/receipt", json={"cv_artifact_id": cv1["artifact_id"]}
        )
        assert resp.status_code == 201, resp.text
        assert [w for w in resp.json()["warnings"] if w.startswith("cv_artifact_id")] == []


@pytest.mark.asyncio
async def test_mcp_tool_matches_the_route(authenticated_async_context):
    from src.api.mcp_server import mcp_runtime

    async with authenticated_async_context() as client:
        app_id, _cv1, cv2, letter = await _setup(client)
        route = (await client.post(f"/api/applications/{app_id}/receipt", json={})).json()

    token = await _mint_token(authenticated_async_context)
    async with mcp_runtime():
        async with _mcp_client(token) as mcp:
            bare = _payload(await mcp.call_tool("record_application", {"application_id": app_id}))
            named = _payload(
                await mcp.call_tool(
                    "record_application",
                    {"application_id": app_id, "cover_letter_artifact_id": letter["artifact_id"]},
                )
            )
            got = _payload(await mcp.call_tool("get_receipt", {"receipt_id": bare["receipt_id"]}))
            listed = _payload(await mcp.call_tool("list_receipts", {}))

    for key in ("cover_letter_artifact_id", "cover_letter_sent", "cv_artifact_id", "warnings"):
        assert bare[key] == route[key], key
    assert bare["cover_letter_sent"] is False and bare["cover_letter_artifact_id"] is None
    assert named["cover_letter_sent"] is True and named["cover_letter_artifact_id"] == letter["artifact_id"]
    assert bare["cv_artifact_id"] == cv2["artifact_id"]
    assert got["cover_letter_sent"] is False and got["has_cover_letter"] is False
    sent = {r["id"]: r["cover_letter_sent"] for r in listed["receipts"]}
    assert sent == {route["receipt_id"]: False, bare["receipt_id"]: False, named["receipt_id"]: True}
