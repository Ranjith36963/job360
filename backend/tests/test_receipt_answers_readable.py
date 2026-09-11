"""The receipt's answers and filled fields must be READABLE, not just stored.

Found 2026-09-11: ``record_application`` stored ``answers`` and
``fields_filled`` (migration 0037) but ``_list_receipts_for_application``
never selected them, so neither ``get_application`` (web + MCP) nor
``export_history`` ever returned them. Rule 21 — value-presence, not
schema-presence: every assertion below checks the real value round-trips.
"""
from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

_AD = {
    "title": "Data Engineer",
    "company": "Contoso",
    "location": "Manchester",
    "apply_url": "https://contoso.example/jobs/12",
    "description": "Pipelines, Airflow, dbt, Postgres.",
}

_ANSWERS = [
    {"question": "Why Contoso?", "answer": "Because the data team ships weekly."},
    {"question": "Notice period?", "answer": "Four weeks."},
]
_FIELDS: dict[str, Any] = {"salary_expectation": 65000, "phone": "+44 7700 900123", "remote_ok": True}


async def _bring(client: AsyncClient) -> int:
    resp = await client.post("/api/jobs/bring", json=_AD)
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _receipt_with_everything(client: AsyncClient, app_id: int) -> int:
    resp = await client.post(
        f"/api/applications/{app_id}/receipt",
        json={"channel": "company site", "confirmation": "REF-9", "answers": _ANSWERS, "fields_filled": _FIELDS},
    )
    assert resp.status_code == 201, resp.text
    return int(resp.json()["receipt_id"]) if "receipt_id" in resp.json() else int(resp.json()["id"])


@pytest.mark.asyncio
async def test_get_application_returns_the_answers_and_fields(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        await _receipt_with_everything(client, app_id)
        detail = await client.get(f"/api/applications/{app_id}")
    assert detail.status_code == 200, detail.text
    receipt = detail.json()["receipts"][0]
    assert receipt["answers"] == _ANSWERS
    assert receipt["fields_filled"] == _FIELDS


@pytest.mark.asyncio
async def test_export_history_returns_the_answers_and_fields(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        await _receipt_with_everything(client, app_id)
        export = await client.get("/api/applications/export")
    assert export.status_code == 200, export.text
    apps = [a for a in export.json()["applications"] if a["id"] == app_id]
    assert len(apps) == 1
    receipt = apps[0]["receipts"][0]
    assert receipt["answers"] == _ANSWERS
    assert receipt["fields_filled"] == _FIELDS


@pytest.mark.asyncio
async def test_a_receipt_with_nothing_extra_reads_as_empty_not_null(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        resp = await client.post(f"/api/applications/{app_id}/receipt", json={"channel": "email"})
        assert resp.status_code == 201, resp.text
        detail = await client.get(f"/api/applications/{app_id}")
    receipt = detail.json()["receipts"][0]
    assert receipt["answers"] == []
    assert receipt["fields_filled"] == {}


@pytest.mark.asyncio
async def test_a_legacy_row_with_bad_json_reads_as_empty(authenticated_async_context):
    """A pre-0037 row backfilled with junk must not 500 the whole page."""
    from src.core import settings
    from src.repositories import pgsync

    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        resp = await client.post(f"/api/applications/{app_id}/receipt", json={"channel": "email"})
        assert resp.status_code == 201, resp.text
        conn = pgsync.connect(str(settings.DB_PATH))
        conn.execute(
            "UPDATE application_receipts SET answers = ?, fields_filled = ? WHERE application_id = ?",
            ("not json", "[1,2]", app_id),
        )
        conn.commit()
        conn.close()
        detail = await client.get(f"/api/applications/{app_id}")
    assert detail.status_code == 200, detail.text
    receipt = detail.json()["receipts"][0]
    assert receipt["answers"] == []
    assert receipt["fields_filled"] == {}
