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
    """P0. The pasted ad matches a scraped row the ghost detector already marked
    stale. Before: `get_job_by_id_with_enrichment` filters on staleness_state,
    returned None, and a bare `assert` turned it into a 500. The user is
    reading the ad NOW, so the row is live again: 200, active, in the feed."""
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
        detail = await client.get(f"/api/jobs/{job_id}")
        assert detail.status_code == 200, detail.text
        cur = await db._conn.execute(
            "SELECT status FROM user_feed WHERE user_id = ? AND job_id = ?",
            (fixture_user_id, job_id),
        )
        assert (await cur.fetchone())[0] == "active"


@pytest.mark.asyncio
async def test_pasted_ad_text_is_not_readable_anonymously(authenticated_async_context):
    """P1. Job ids are sequential and the single-job read is public. The
    description of a `user_brought` row is text a person pasted; it must ride
    only for a logged-in user (rule #12/#25: no cross-user leak)."""
    async with authenticated_async_context() as client:
        job_id = await _bring(client)
        mine = await client.get(f"/api/jobs/{job_id}")
        assert mine.status_code == 200 and mine.json()["description"] == _AD["description"]

        client.cookies.clear()
        anon = await client.get(f"/api/jobs/{job_id}")
        if anon.status_code == 200:
            assert anon.json().get("description") in (None, ""), anon.text
        else:
            assert anon.status_code in (401, 403), anon.text


@pytest.mark.asyncio
async def test_brought_job_survives_candidate_selection(authenticated_async_context, fixture_user_id):
    """P1. The next search runs `backfill_feed_from_catalog`, which caps the
    feed and evicts rows outside the selection unless they are protected.
    A brought job has no like/apply row and may score under the store floor,
    so it was evicted. Now it is protected like a liked job.

    Bound: exercises `_load_action_sets` + `apply_candidate_selection` — the
    exact pair backfill uses (rescore.py) — not the full backfill, which needs
    a complete profile."""
    from src.services.feed import FeedService
    from src.services.rescore import _load_action_sets

    async with authenticated_async_context() as client:
        job_id = await _bring(client)
        db = await api_deps.get_db()

        # A second, unrelated feed row is the only thing selection keeps.
        other = await client.post(
            "/api/jobs/bring", json={**_AD, "title": "Data Analyst", "company": "Initech"}
        )
        other_id = int(other.json()["job"]["id"])
        await db._conn.execute("UPDATE jobs SET source = 'adzuna' WHERE id = ?", (other_id,))
        await db._conn.commit()

        protected, rejected = await _load_action_sets(db, fixture_user_id)
        assert job_id in protected and other_id not in protected and not rejected

        evicted = await FeedService(db._db).apply_candidate_selection(
            fixture_user_id, {other_id} | protected
        )
        assert evicted == 0
        cur = await db._conn.execute(
            "SELECT status FROM user_feed WHERE user_id = ? AND job_id = ?",
            (fixture_user_id, job_id),
        )
        assert (await cur.fetchone())[0] == "active"

        # Control: without the protection the same call evicts it.
        await FeedService(db._db).apply_candidate_selection(fixture_user_id, {other_id})
        cur = await db._conn.execute(
            "SELECT status FROM user_feed WHERE user_id = ? AND job_id = ?",
            (fixture_user_id, job_id),
        )
        assert (await cur.fetchone())[0] != "active"


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
