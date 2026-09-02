"""Application receipts — "what did I send X?" must be answerable forever.

The contract (migration 0034): a receipt copies the job and the documents at
the moment of "I applied", and NOTHING later rewrites it — not re-tailoring
(which DELETE+INSERTs tailored_documents), not the catalog re-describing the
job, not a second application. Append-only is pinned by reading the source.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.api import dependencies as api_deps

_AD = {
    "title": "Data Engineer",
    "company": "Northwind",
    "location": "Remote",
    "apply_url": "https://northwind.example/careers/7",
    "description": "Build the pipelines. Python, dbt, Snowflake. Fully remote.",
}


async def _bring(client) -> int:
    resp = await client.post("/api/jobs/bring", json=_AD)
    assert resp.status_code == 200, resp.text
    return int(resp.json()["job"]["id"])


@pytest.mark.asyncio
async def test_receipt_freezes_job_and_marks_applied(authenticated_async_context):
    async with authenticated_async_context() as client:
        job_id = await _bring(client)
        resp = await client.post(f"/api/receipts/{job_id}", json={"channel": "company site", "note": "via referral"})
        assert resp.status_code == 201, resp.text
        r = resp.json()
        assert r["job_id"] == job_id
        assert r["job_title"] == _AD["title"]
        assert r["job_company"] == _AD["company"]
        assert r["job_description"] == _AD["description"]
        assert r["job_apply_url"] == _AD["apply_url"]
        assert r["cv_text"] is None and r["cv_origin"] is None       # applied without a tailored CV
        assert r["channel"] == "company site" and r["note"] == "via referral"
        assert r["sent_at"]

        # Both existing "applied" surfaces agree.
        detail = await client.get(f"/api/jobs/{job_id}")
        assert detail.json()["action"] == "applied"
        pipeline = await client.get("/api/pipeline")
        assert any(a["job_id"] == job_id and a["stage"] == "applied" for a in pipeline.json()["applications"])


@pytest.mark.asyncio
async def test_receipt_keeps_the_cv_actually_sent(authenticated_async_context, fixture_user_id):
    """Polished edit wins over the AI draft; a later regenerate does not touch it."""
    async with authenticated_async_context() as client:
        job_id = await _bring(client)
        db = await api_deps.get_db()
        await db.upsert_tailored_doc(fixture_user_id, job_id, "cv", "DRAFT v1", model="test")
        await db.save_tailored_polished(fixture_user_id, job_id, "cv", "POLISHED v1 — what I sent")
        await db.upsert_tailored_doc(fixture_user_id, job_id, "cover_letter", "Dear Northwind", model="test")

        resp = await client.post(f"/api/receipts/{job_id}", json={})
        assert resp.status_code == 201, resp.text
        receipt_id = resp.json()["id"]
        assert resp.json()["cv_text"] == "POLISHED v1 — what I sent"
        assert resp.json()["cv_origin"] == "polished"
        assert resp.json()["cover_letter_text"] == "Dear Northwind"
        assert resp.json()["cover_letter_origin"] == "ai_draft"

        # Re-tailor: tailored_documents is DELETE+INSERT, the old text is gone there…
        await db.upsert_tailored_doc(fixture_user_id, job_id, "cv", "DRAFT v2", model="test")
        assert (await db.get_tailored_doc(fixture_user_id, job_id, "cv"))["polished"] is None
        # …and the catalog re-describes the job…
        await db._conn.execute("UPDATE jobs SET description = 'rewritten' WHERE id = ?", (job_id,))
        await db._conn.commit()

        # …but the receipt is exactly what it was.
        got = await client.get(f"/api/receipts/{receipt_id}")
        assert got.status_code == 200
        assert got.json()["cv_text"] == "POLISHED v1 — what I sent"
        assert got.json()["job_description"] == _AD["description"]


@pytest.mark.asyncio
async def test_two_applications_two_receipts(authenticated_async_context):
    async with authenticated_async_context() as client:
        job_id = await _bring(client)
        a = await client.post(f"/api/receipts/{job_id}", json={"note": "first"})
        b = await client.post(f"/api/receipts/{job_id}", json={"note": "six months later"})
        assert a.status_code == 201 and b.status_code == 201
        assert a.json()["id"] != b.json()["id"]

        listed = await client.get("/api/receipts", params={"job_id": job_id})
        assert listed.status_code == 200
        body = listed.json()
        assert body["total"] == 2
        assert [r["note"] for r in body["receipts"]] == ["six months later", "first"]  # newest first
        assert all(r["has_cv"] is False for r in body["receipts"])


@pytest.mark.asyncio
async def test_receipts_are_scoped_to_owner(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        job_id = await _bring(client)
        created = await client.post(f"/api/receipts/{job_id}", json={})
        receipt_id = created.json()["id"]
        db = await api_deps.get_db()
        assert await db.get_receipt(fixture_user_id, receipt_id) is not None
        assert await db.get_receipt("someone-else", receipt_id) is None   # rule #12
        assert await db.list_receipts("someone-else") == []
        missing = await client.get("/api/receipts/999999")
        assert missing.status_code == 404


@pytest.mark.asyncio
async def test_receipt_for_unknown_job_is_404(authenticated_async_context):
    async with authenticated_async_context() as client:
        resp = await client.post("/api/receipts/999999", json={})
        assert resp.status_code == 404


def test_receipts_are_append_only():
    """No code path may UPDATE or DELETE a receipt, and the API exposes no
    PATCH/PUT/DELETE on /receipts. The migration's down file is the one
    sanctioned drop (schema rollback, not a user operation)."""
    src = Path(__file__).resolve().parent.parent / "src"
    offenders = []
    for py in src.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        if re.search(r"(UPDATE|DELETE\s+FROM)\s+application_receipts", text, re.IGNORECASE):
            offenders.append(str(py))
    assert offenders == [], f"receipts must be append-only: {offenders}"

    from src.api.main import app

    for route in app.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", set()) or set()
        if path.startswith("/api/receipts"):
            assert not (methods & {"PATCH", "PUT", "DELETE"}), f"{path} exposes {methods}"
