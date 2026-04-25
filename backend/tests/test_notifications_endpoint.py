"""Step-1.5 S3-D — paginated notification ledger endpoint."""

from __future__ import annotations

import pytest

from src.api import dependencies as api_deps


async def _insert_ledger_row(db, **overrides) -> None:
    payload = dict(
        user_id="user-1",
        job_id=1,
        channel="email",
        status="sent",
        sent_at="2026-04-25T12:00:00+00:00",
        error_message=None,
        retry_count=0,
        created_at="2026-04-25T12:00:00+00:00",
    )
    payload.update(overrides)
    cols = ", ".join(payload.keys())
    placeholders = ", ".join(["?"] * len(payload))
    await db._conn.execute(
        f"INSERT INTO notification_ledger ({cols}) VALUES ({placeholders})",  # noqa: S608 — test helper
        tuple(payload.values()),
    )
    await db._conn.commit()


@pytest.mark.asyncio
async def test_notifications_empty_for_new_user(authenticated_async_context):
    """Fresh user with no ledger rows must get 200 + empty list."""
    async with authenticated_async_context() as client:
        resp = await client.get("/api/notifications")
    assert resp.status_code == 200
    body = resp.json()
    assert body["notifications"] == []
    assert body["total"] == 0
    assert body["limit"] == 50
    assert body["offset"] == 0


@pytest.mark.asyncio
async def test_notifications_returns_paginated_rows(authenticated_async_context, fixture_user_id):
    """A row inserted under the fixture-user id must surface in the response."""
    db = await api_deps.get_db()
    await _insert_ledger_row(db, user_id=fixture_user_id, job_id=42)
    async with authenticated_async_context() as client:
        resp = await client.get("/api/notifications")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert len(body["notifications"]) == 1
    entry = body["notifications"][0]
    assert entry["job_id"] == 42
    assert entry["channel"] == "email"
    assert entry["status"] == "sent"


@pytest.mark.asyncio
async def test_notifications_scoped_per_user(authenticated_async_context, fixture_user_id):
    """A row for ANOTHER user must NOT surface (tenant isolation)."""
    db = await api_deps.get_db()
    await _insert_ledger_row(db, user_id="other-user", job_id=99)
    async with authenticated_async_context() as client:
        resp = await client.get("/api/notifications")
    assert resp.status_code == 200
    body = resp.json()
    # Even when other-user has rows, the fixture-user sees zero.
    assert all(e["job_id"] != 99 for e in body["notifications"])


@pytest.mark.asyncio
async def test_notifications_filter_by_channel(authenticated_async_context, fixture_user_id):
    """?channel=slack must filter out non-slack rows."""
    db = await api_deps.get_db()
    await _insert_ledger_row(db, user_id=fixture_user_id, job_id=1, channel="email")
    await _insert_ledger_row(db, user_id=fixture_user_id, job_id=2, channel="slack")
    async with authenticated_async_context() as client:
        resp = await client.get("/api/notifications?channel=slack")
    assert resp.status_code == 200
    body = resp.json()
    assert all(e["channel"] == "slack" for e in body["notifications"])
    assert body["total"] == 1


@pytest.mark.asyncio
async def test_notifications_filter_by_status(authenticated_async_context, fixture_user_id):
    """?status=failed must filter to failed-status rows only."""
    db = await api_deps.get_db()
    await _insert_ledger_row(db, user_id=fixture_user_id, job_id=1, status="sent")
    await _insert_ledger_row(db, user_id=fixture_user_id, job_id=2, status="failed")
    async with authenticated_async_context() as client:
        resp = await client.get("/api/notifications?status=failed")
    assert resp.status_code == 200
    body = resp.json()
    assert all(e["status"] == "failed" for e in body["notifications"])
    assert body["total"] == 1


@pytest.mark.asyncio
async def test_jobs_response_dedup_group_ids_field_present(authenticated_async_context):
    """S3-F field-presence: JobResponse.dedup_group_ids exists and defaults
    to None until the dedup-group writer batch lands. The frontend Step 2
    type contract relies on this field shape being stable — populating
    it later is additive and won't break Step 2's wired consumer."""
    db = await api_deps.get_db()
    from datetime import datetime, timezone

    now = datetime(2026, 4, 25, tzinfo=timezone.utc).isoformat()
    cur = await db._conn.execute(
        "INSERT INTO jobs (title, company, apply_url, source, date_found, "
        "normalized_company, normalized_title, first_seen) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "Engineer",
            "Acme",
            "https://example.test/x",
            "test",
            now,
            "acme",
            "engineer",
            now,
        ),
    )
    await db._conn.commit()
    job_id = cur.lastrowid
    async with authenticated_async_context() as client:
        resp = await client.get(f"/api/jobs/{job_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert "dedup_group_ids" in body
    assert body["dedup_group_ids"] is None
