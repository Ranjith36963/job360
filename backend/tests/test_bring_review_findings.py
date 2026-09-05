"""Bring-a-job / receipts — the four Important findings from the review pass.

Playbook rule: every bug found becomes a test before it becomes a fix. The
original test files stayed frozen; these were ADDED after the bugs review
(2026-09-02) reproduced each one on real Postgres.
"""
from __future__ import annotations

import pytest

from src.api import dependencies as api_deps

_AD = {
    "title": "Platform Engineer",
    "company": "Globex Corp",
    "location": "Manchester, UK",
    "apply_url": "https://globex.example/jobs/9",
    "description": "Run the Kubernetes platform. Python, Terraform, Postgres. Hybrid, Manchester.",
}


async def _bring(client) -> int:
    resp = await client.post("/api/jobs/bring", json=_AD)
    assert resp.status_code == 200, resp.text
    return int(resp.json()["job"]["id"])


@pytest.mark.asyncio
async def test_bring_reactivates_a_stale_catalog_row(authenticated_async_context, fixture_user_id):
    """P0. The pasted ad matches a legacy scraped row a long-dead ghost sweep
    marked stale. The user is reading the ad NOW, so the row is live again:
    200, `staleness_state='active'`, and the application page is reachable.
    (`POST /pipeline/{job_id}` still refuses a `confirmed_expired` row, which
    is the read that keeps `update_last_seen` load-bearing after slice 5.)"""
    async with authenticated_async_context() as client:
        job_id = await _bring(client)
        db = await api_deps.get_db()
        await db._conn.execute(
            "UPDATE jobs SET source = 'indeed', staleness_state = 'likely_stale', "
            "consecutive_misses = 4 WHERE id = ?",
            (job_id,),
        )
        await db._conn.commit()

        again = await client.post("/api/jobs/bring", json=_AD)
        assert again.status_code == 200, again.text
        assert again.json()["existing"] is True
        assert again.json()["job"]["id"] == job_id

        cur = await db._conn.execute(
            "SELECT staleness_state, consecutive_misses FROM jobs WHERE id = ?", (job_id,)
        )
        state, misses = await cur.fetchone()
        assert state == "active" and misses == 0

        # The page the user is sent to must not be empty.
        detail = await client.get(f"/api/applications/job/{job_id}")
        assert detail.status_code == 200, detail.text


@pytest.mark.asyncio
async def test_pasted_ad_text_is_not_readable_anonymously(authenticated_async_context):
    """P1. Job ids are sequential, and the read used to be PUBLIC. The
    description of a `user_brought` row is text a person pasted; it must ride
    only for the user who brought it (rule #12/#25: no cross-user leak).

    Slice 5 (#483) closed this at the route, not the field: the public
    `GET /api/jobs/{id}` is deleted and its replacement,
    `GET /api/applications/job/{id}`, is `Depends(require_user)` and scoped by
    the caller's own application."""
    async with authenticated_async_context() as client:
        job_id = await _bring(client)
        mine = await client.get(f"/api/applications/job/{job_id}")
        assert mine.status_code == 200 and mine.json()["description"] == _AD["description"]

        client.cookies.clear()
        anon = await client.get(f"/api/applications/job/{job_id}")
        assert anon.status_code in (401, 403), anon.text


@pytest.mark.asyncio
async def test_receipt_list_is_bounded_and_light(authenticated_async_context, fixture_user_id):
    """P2. The list read selected every body column with no LIMIT. Now:
    summary columns only, LIMIT/OFFSET honoured, `total` is the real count."""
    async with authenticated_async_context() as client:
        job_id = await _bring(client)
        for i in range(3):
            r = await client.post(f"/api/receipts/{job_id}", json={"channel": f"c{i}"})
            assert r.status_code == 201, r.text

        page = await client.get("/api/receipts", params={"limit": 2})
        assert page.status_code == 200, page.text
        body = page.json()
        assert body["total"] == 3 and len(body["receipts"]) == 2
        assert body["receipts"][0]["channel"] == "c2"          # newest first
        assert "cv_text" not in body["receipts"][0]
        assert "job_description" not in body["receipts"][0]

        rest = await client.get("/api/receipts", params={"limit": 2, "offset": 2})
        assert [r["channel"] for r in rest.json()["receipts"]] == ["c0"]
        assert rest.json()["total"] == 3

        too_big = await client.get("/api/receipts", params={"limit": 10_000})
        assert too_big.status_code == 422

    db = await api_deps.get_db()
    rows = await db.list_receipts(fixture_user_id, limit=5)
    assert rows and "cv_text" not in rows[0] and rows[0]["has_cv"] is False
