"""A receipt's `confirmation` is the application ID / portal reference ONLY.

A label or prose ("confirmation id", "Application ID") used to be stored as the
confirmation and counted as proof. Now it is kept in the note with a warning
(the receipt is never refused), and the proof read ignores old bad rows.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from src.services.applications.proof import looks_like_confirmation_id

AD = {"title": "Data Engineer", "company": "Northwind", "location": "London", "description": "Pipelines. " * 8,
      "apply_url": "https://northwind.example/careers/7"}


def _ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _sql(statement: str, params: tuple = ()) -> list[Any]:
    from src.core import settings
    from src.repositories import pgsync

    conn = pgsync.connect(str(settings.DB_PATH))
    try:
        cur = conn.execute(statement, params)
        rows = cur.fetchall() if statement.lstrip().upper().startswith("SELECT") else []
        conn.commit()
        return rows
    finally:
        conn.close()


async def _bring(client, company: str = "Northwind") -> int:
    resp = await client.post("/api/jobs/bring", json={**AD, "company": company})
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


@pytest.mark.parametrize(
    "value, ok",
    [
        ("APP-12345", True),
        ("R0123456", True),
        ("3f2b8c1e-9d4a-4e6b-8a57-0c1d2e3f4a5b", True),
        ("  REF 4471  ", True),
        ("confirmation id", False),
        ("Application ID", False),
        ("Confirmation number", False),
        ("", False),
        ("12", False),
        ("x" * 70 + "1", False),
        ("Thanks, your application 12345 was received by the team and we will be in touch soon", False),
        ("see https://x/1", False),
        ("APP-1\nAPP-2", False),
        ("a 1 b c", False),
    ],
)
def test_looks_like_confirmation_id(value, ok):
    assert looks_like_confirmation_id(value) is ok


@pytest.mark.asyncio
async def test_a_label_is_kept_in_the_note_with_a_warning_and_the_receipt_is_still_made(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        resp = await client.post(
            f"/api/applications/{app_id}/receipt",
            json={"confirmation": "confirmation id", "note": "sent via portal", "channel": "company_site"},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["confirmation"] == ""
        assert any("does not look like an application ID" in w for w in body["warnings"])
        row = _sql("SELECT confirmation, note FROM application_receipts WHERE id = ?", (body["receipt_id"],))[0]
        assert row[0] == ""
        assert "sent via portal" in row[1] and "Confirmation text (not an ID): confirmation id" in row[1]


@pytest.mark.asyncio
async def test_a_real_id_is_stored_as_given_without_a_warning(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        resp = await client.post(f"/api/applications/{app_id}/receipt", json={"confirmation": "APP-12345"})
        assert resp.status_code == 201, resp.text
        assert resp.json()["confirmation"] == "APP-12345"
        assert not any("application ID" in w for w in resp.json()["warnings"])


@pytest.mark.asyncio
async def test_an_old_bad_row_no_longer_counts_as_proof_and_is_listed_as_missing(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client, "OldBad")
        made = await client.post(
            f"/api/applications/{app_id}/receipt", json={"confirmation": "REF-4471", "applied_at": _ago(8)}
        )
        assert made.status_code == 201, made.text
        assert (await client.get(f"/api/applications/{app_id}/proof")).json()["proof"]["level"] == "text"
        # An old row from before the validator: written directly (tests may; src may not).
        _sql("UPDATE application_receipts SET confirmation = ? WHERE application_id = ?", ("Confirmation number", app_id))
        assert (await client.get(f"/api/applications/{app_id}/proof")).json()["proof"]["level"] == "none"
        missing = (await client.get("/api/whats-new")).json()["proof_missing"]
        assert [m["application_id"] for m in missing] == [app_id]
