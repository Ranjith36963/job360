"""Gmail check 2026-10-31: ``whats_due`` hands an inbox run everything ONE Gmail search needs.

Every audited hourly run (2026-10-30/31) read ``list_applications`` instead: 100 rows passed the tool-result
limit, runs searched a guessed window (``after:2026/10/29``) and re-parked the same two unmatched mails each
hour. The inbox entry now carries ``search``. Helpers are defined here, never imported.
"""
from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

_ADS = (
    {"title": "Data Engineer", "company": "Northwind Ltd", "location": "Remote",
     "apply_url": "https://jobs.lever.co/northwind/1", "description": "Pipelines. Python, dbt."},
    {"title": "Platform Engineer", "company": "Contoso", "location": "London",
     "apply_url": "https://contoso.example/jobs/9", "description": "Platform. Kubernetes, Go."},
    {"title": "ML Engineer", "company": "Closedco", "location": "Remote",
     "apply_url": "https://closedco.example/jobs/3", "description": "Models. Python."},
)


async def _bring(client: AsyncClient, ad: dict[str, Any]) -> int:
    resp = await client.post("/api/jobs/bring", json=ad)
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


def _set_cursor(application_id: int, when: str) -> None:
    from src.core import settings
    from src.repositories import pgsync

    conn = pgsync.connect(str(settings.DB_PATH))
    try:
        conn.execute("UPDATE applications SET inbox_checked_at = ? WHERE id = ?", (when, application_id))
        conn.commit()
    finally:
        conn.close()


async def _inbox_on(client: AsyncClient) -> None:
    resp = await client.patch("/api/profile", json={"edits": [
        {"path": "preferences.daily_check", "value": "auto"}, {"path": "preferences.check_every", "value": "1h"},
    ]})
    assert resp.status_code == 200, resp.text


async def _inbox_entry(client: AsyncClient) -> dict[str, Any]:
    resp = await client.get("/api/schedule/due", params={"duties": ["inbox"]})
    assert resp.status_code == 200, resp.text
    due = [d for d in resp.json()["due"] if d["duty"] == "inbox"]
    assert len(due) == 1, resp.json()
    return due[0]


@pytest.mark.asyncio
async def test_inbox_due_carries_one_search_for_every_open_application(authenticated_async_context):
    async with authenticated_async_context() as client:
        await _inbox_on(client)
        north, contoso, closed = [await _bring(client, ad) for ad in _ADS]
        gone = await client.post(f"/api/applications/{closed}/events", json={"event_type": "rejected"})
        assert gone.status_code in (200, 201), gone.text
        _set_cursor(north, "2026-10-30T09:00:00+00:00")  # mark_inbox_checked would also stamp inbox done
        parked = await client.post("/api/applications/record-events", json={"unmatched": [{
            "message_id": "m-parked", "sender": "talent@globex.example", "subject": "Your application",
            "received_at": "2026-10-30T10:00:00+00:00"}]})
        assert parked.status_code == 200, parked.text

        search = (await _inbox_entry(client))["search"]
        assert search["application_ids"] == [north, contoso]  # the rejected one is not searched
        assert search["companies"] == ["Contoso", "Northwind Ltd"]
        assert "lever.co" in search["email_senders"]
        assert search["skip_message_ids"] == ["m-parked"]
        # the never-checked application starts at its created_at (today), the checked one at its cursor
        assert search["after"].startswith("2026-10-30T09:00:00") and search["gmail_after"] == "2026/10/30"


@pytest.mark.asyncio
async def test_inbox_search_is_empty_with_no_open_application(authenticated_async_context):
    async with authenticated_async_context() as client:
        await _inbox_on(client)
        search = (await _inbox_entry(client))["search"]
        assert search == {"after": "", "gmail_after": "", "application_ids": [], "companies": [],
                          "email_senders": [], "skip_message_ids": []}
