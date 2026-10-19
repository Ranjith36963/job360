"""The receipt page reads what was recorded when the user applied.

`GET /api/receipts/{id}` (the web receipt page) and the MCP `get_receipt` tool
return the answers, filled fields, confirmation, the CV / cover-letter VERSION
numbers the receipt names, the application id and who recorded it — read off
the SAME `application_receipts` row (one table, no join). Stored facts only:
a receipt that recorded none of them reads empty / None, never a default
(rule #29). Rule #21 — every assertion checks a real value, not a key.
"""
from __future__ import annotations

import json
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

_AD = {
    "title": "Platform Engineer",
    "company": "Fabrikam",
    "location": "Leeds",
    "apply_url": "https://fabrikam.example/jobs/7",
    "description": "Kubernetes, Terraform, Python, on-call rota.",
}
_ANSWERS = [
    {"question": "Why Fabrikam?", "answer": "The platform team owns its own SLOs."},
    {"question": "Right to work?", "answer": "Needs sponsorship."},
]
_FIELDS: dict[str, Any] = {"phone": "+44 7700 900456", "salary_expectation": 70000, "relocate": False}
_NEW_FIELDS = (
    "application_id", "answers", "fields_filled", "confirmation",
    "cv_version_no", "cover_letter_version_no", "recorded_by",
)


async def _bring(client: AsyncClient) -> int:
    resp = await client.post("/api/jobs/bring", json=_AD)
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _save(client: AsyncClient, app_id: int, kind: str, text: str) -> dict[str, Any]:
    resp = await client.post(f"/api/applications/{app_id}/artifacts", json={"kind": kind, "text": text})
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _full_receipt(client: AsyncClient) -> tuple[int, int]:
    """Two CV versions + one cover letter; the receipt names CV v1 (NOT the
    newest) so the version lookup is proven to follow the stored id."""
    app_id = await _bring(client)
    cv1 = await _save(client, app_id, "cv", "CV version one: Kubernetes, Terraform.")
    cv2 = await _save(client, app_id, "cv", "CV version two: Kubernetes, Terraform, Go.")
    cl1 = await _save(client, app_id, "cover_letter", "Dear Fabrikam, ...")
    assert (cv1["version_no"], cv2["version_no"], cl1["version_no"]) == (1, 2, 1)
    resp = await client.post(
        f"/api/applications/{app_id}/receipt",
        json={
            "channel": "company site", "confirmation": "FAB-2026-0042", "answers": _ANSWERS,
            "fields_filled": _FIELDS, "cv_artifact_id": cv1["artifact_id"],
            "cover_letter_artifact_id": cl1["artifact_id"],
        },
    )
    assert resp.status_code == 201, resp.text
    return app_id, int(resp.json()["receipt_id"])


async def _log_in_other_user(other: AsyncClient) -> None:
    creds = {"email": "receipt-other@example.com", "password": "an0therS3cret!"}
    reg = await other.post("/api/auth/register", json=creds)
    assert reg.status_code in (200, 201), reg.text
    login = await other.post("/api/auth/login", json=creds)
    assert login.status_code == 200, login.text


@pytest.mark.asyncio
async def test_receipt_returns_what_was_recorded_and_the_versions_sent(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id, receipt_id = await _full_receipt(client)
        resp = await client.get(f"/api/receipts/{receipt_id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["application_id"] == app_id
    assert body["answers"] == _ANSWERS
    assert body["fields_filled"] == _FIELDS
    assert body["confirmation"] == "FAB-2026-0042"
    assert body["cv_version_no"] == 1          # the named version, not the newest (2)
    assert body["cover_letter_version_no"] == 1
    assert body["recorded_by"] == "web"        # authorship.actor_for for a session
    # The frozen text is the named version's text — the old fields are intact.
    assert body["cv_text"] == "CV version one: Kubernetes, Terraform."
    assert body["channel"] == "company_site"


@pytest.mark.asyncio
async def test_receipt_that_recorded_nothing_reads_empty_not_defaulted(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        made = await client.post(f"/api/applications/{app_id}/receipt", json={})
        assert made.status_code == 201, made.text
        resp = await client.get(f"/api/receipts/{made.json()['receipt_id']}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["answers"] == []
    assert body["fields_filled"] == {}
    assert body["confirmation"] is None
    assert body["cv_version_no"] is None
    assert body["cover_letter_version_no"] is None
    assert body["application_id"] == app_id
    assert body["recorded_by"] == "web"


@pytest.mark.asyncio
async def test_legacy_i_applied_receipt_reads_empty_details(authenticated_async_context):
    """The web "I applied" button (`POST /receipts/{job_id}`) records no
    answers / confirmation / artifact ids — they stay empty. `recorded_by`
    is the column's stored value ('web', migration 0037), not invented here."""
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        app = await client.get(f"/api/applications/{app_id}")
        job_id = int(app.json()["job_id"])
        made = await client.post(f"/api/receipts/{job_id}", json={"note": "applied by hand"})
        assert made.status_code == 201, made.text
        created = made.json()
        resp = await client.get(f"/api/receipts/{created['id']}")
    body = resp.json()
    assert body["note"] == "applied by hand"
    assert body["answers"] == [] and body["fields_filled"] == {}
    assert body["confirmation"] is None
    assert body["cv_version_no"] is None and body["cover_letter_version_no"] is None
    assert body["application_id"] == app_id
    assert body["recorded_by"] == "web"
    assert created["application_id"] == app_id   # the POST answer carries the same facts


@pytest.mark.asyncio
async def test_another_users_receipt_is_404_and_leaks_nothing(authenticated_async_context):
    async with authenticated_async_context() as client:
        _, receipt_id = await _full_receipt(client)
    from src.api.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as other:
        await _log_in_other_user(other)
        resp = await other.get(f"/api/receipts/{receipt_id}")
    assert resp.status_code == 404
    text = resp.text
    assert "FAB-2026-0042" not in text and "Fabrikam" not in text
    assert not any(field in resp.json() for field in _NEW_FIELDS)


@pytest.mark.asyncio
async def test_mcp_get_receipt_returns_the_same_details(authenticated_async_context):
    pytest.importorskip("mcp")
    import httpx2
    from mcp.client import Client
    from mcp.client.streamable_http import streamable_http_client

    from src.api.main import app
    from src.api.mcp_server import mcp_runtime

    async with authenticated_async_context() as client:
        minted = await client.post("/api/tokens", json={"name": "agent"})
        assert minted.status_code == 201, minted.text
        token = minted.json()["token"]
        app_id, receipt_id = await _full_receipt(client)
        web = (await client.get(f"/api/receipts/{receipt_id}")).json()

    http = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )
    async with mcp_runtime():
        async with Client(streamable_http_client("http://test/api/mcp", http_client=http)) as mcp:
            result = await mcp.call_tool("get_receipt", {"receipt_id": receipt_id})
    assert not result.is_error, result.content[0].text
    tool = json.loads(result.content[0].text)
    assert tool["answers"] == _ANSWERS
    assert tool["fields_filled"] == _FIELDS
    assert tool["confirmation"] == "FAB-2026-0042"
    assert tool["cv_version_no"] == 1 and tool["cover_letter_version_no"] == 1
    assert tool["application_id"] == app_id
    assert tool["recorded_by"] == "web"
    assert {f: tool[f] for f in _NEW_FIELDS} == {f: web[f] for f in _NEW_FIELDS}
