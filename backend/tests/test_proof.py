"""S7a - PROOF OF APPLICATION, the read side (owner decision 2026-10-10).

Pasted confirmation text, the proof level, the "no proof after 7 days" ask. Real doors (HTTP + MCP), VALUES asserted
(rule #21). Helpers are copied, never imported from another test module.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.core import settings

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
AD = {"title": "Data Engineer", "company": "Northwind", "location": "London", "description": "Pipelines. " * 8,
      "apply_url": "https://northwind.example/careers/7"}
EMAIL = {"kind": "email", "message_id": "<m1@northwind.example>", "sender": "hr@northwind.example", "subject": "Hi"}


def _ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


async def _bring(client: AsyncClient, ad: dict[str, Any] = AD) -> int:
    resp = await client.post("/api/jobs/bring", json=ad)
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _ev(client: AsyncClient, app_id: int, event_type: str, payload: dict | None = None, **kw: Any):
    return await client.post(
        f"/api/applications/{app_id}/events", json={"event_type": event_type, "payload": payload or {}, **kw}
    )


async def _bearer(client: AsyncClient) -> AsyncClient:
    from src.api.main import app

    token = (await client.post("/api/tokens", json={"name": "claude-code"})).json()["token"]
    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    )


def _sql(statement: str, params: tuple = ()) -> list[Any]:
    from src.repositories import pgsync

    conn = pgsync.connect(str(settings.DB_PATH))
    try:
        cur = conn.execute(statement, params)
        rows = cur.fetchall() if statement.lstrip().upper().startswith("SELECT") else []
        conn.commit()
        return rows
    finally:
        conn.close()


async def _proof(client: AsyncClient, app_id: int) -> dict[str, Any]:
    """The proof level off the application detail, plus the screenshot rows straight from the table."""
    resp = await client.get(f"/api/applications/{app_id}")
    assert resp.status_code == 200, resp.text
    rows = _sql(
        "SELECT id, created_by, sha256, deleted_at FROM application_proof_screenshots WHERE application_id = ? ORDER BY id",
        (app_id,),
    )
    shots = [{"id": r[0], "created_by": r[1], "sha256": r[2], "deleted_at": r[3]} for r in rows]
    return {"application_id": app_id, "proof": resp.json()["proof"], "screenshots": shots}


def _seed(user_id: str, app_id: int, data: bytes = PNG) -> int:
    """One screenshot row by direct SQL (the upload door comes with the next PR); returns its id."""
    now = datetime.now(timezone.utc).isoformat()
    _sql(
        "INSERT INTO application_proof_screenshots (user_id, application_id, mime, bytes, sha256, size, created_by, "
        "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (user_id, app_id, "image/png", data, "0" * 64, len(data), "web", now),
    )
    return int(_sql("SELECT MAX(id) FROM application_proof_screenshots WHERE application_id = ?", (app_id,))[0][0])


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def logs():
    # On the audit logger itself: once the app lifespan has run it stops propagating to root.
    audit = logging.getLogger("job360.audit")
    handler, level = _Capture(), audit.level
    audit.addHandler(handler)
    audit.setLevel(logging.INFO)
    yield handler
    audit.removeHandler(handler)
    audit.setLevel(level)


@pytest.mark.asyncio
async def test_the_export_never_has_bytes(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        ids = {_seed(fixture_user_id, app_id, d) for d in (PNG, JPEG)}
        exported = (await client.get("/api/auth/users/me/export")).json()
        rows = exported["application_proof_screenshots"]
        assert {r["id"] for r in rows} == ids and all("bytes" not in r for r in rows)
        assert "proof_upload_links" not in exported and "_incomplete_tables" not in exported


def test_both_tables_are_registered_for_erasure_and_only_one_for_export():
    from scripts.observe import PER_USER_TABLES
    from src.repositories.database import JobDatabase

    for tbl in ("application_proof_screenshots", "proof_upload_links"):
        assert tbl in JobDatabase._PER_USER_TABLES and (tbl, "user_id") in PER_USER_TABLES
    assert "application_proof_screenshots" in JobDatabase._EXPORT_TABLES
    assert "proof_upload_links" not in JobDatabase._EXPORT_TABLES
    assert "bytes" not in JobDatabase._EXPORT_COLUMNS["application_proof_screenshots"].split(", ")


# ── pasted text ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_proof_text_is_capped_in_characters_cleaned_and_attributed(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        n = settings.PROOF_TEXT_MAX_CHARS
        too_long = await _ev(client, app_id, "proof_text", {"text": "a" * (n + 1)})
        assert too_long.status_code == 422 and "PROOF_TEXT_MAX_CHARS" in too_long.json()["detail"]
        for bad in ({"text": "   "}, {}, {"text": 5}, {"text": "ok", "extra": 1}, {"text": "ok", "page_host": "a b"}):
            assert (await _ev(client, app_id, "proof_text", bad)).status_code == 422, bad
        assert (await _ev(client, app_id, "proof_text", {"text": "é" * n})).status_code == 201  # chars, not bytes
        ok = await _ev(client, app_id, "proof_text", {"text": " Thanks\x00 for applying\n", "page_host": "https://Jobs.Northwind.example/x?a=1"})
        assert ok.status_code == 201
        stored = [e for e in (await client.get(f"/api/applications/{app_id}")).json()["events"]
                  if e["event_type"] == "proof_text"][-1]
        assert stored["payload"] == {"text": "Thanks for applying", "page_host": "jobs.northwind.example", "by": "web"}
        assert (await _proof(client, app_id))["proof"]["level"] == "text"
        async with await _bearer(client) as agent:  # an assistant may write it too; `by` is server-filled
            assert (await _ev(agent, app_id, "proof_text", {"text": "x", "by": "web"})).status_code == 422
            assert (await _ev(agent, app_id, "proof_text", {"text": "Confirmed"})).status_code == 201


@pytest.mark.asyncio
async def test_proof_screenshot_is_written_by_job360_only(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        resp = await _ev(client, app_id, "proof_screenshot", {"screenshot_id": 1})
        assert resp.status_code == 422 and "written by Job360 itself" in resp.json()["detail"]


# ── the level ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_level_is_the_strongest_proof_and_a_correction_withdraws_text(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        a, b, c, d = [await _bring(client, {**AD, "company": f"Co{i}"}) for i in range(4)]
        _seed(fixture_user_id, b)
        await _ev(client, c, "proof_text", {"text": "Application received"})
        _seed(fixture_user_id, c)  # text beats screenshot
        await _ev(client, d, "applied", {}, source=EMAIL)
        await _ev(client, d, "proof_text", {"text": "also pasted"})  # email beats text
        got = {i: (await _proof(client, i))["proof"] for i in (a, b, c, d)}
        assert [got[i]["level"] for i in (a, b, c, d)] == ["none", "screenshot_only", "text", "email"]
        assert got[c] == {"has_text": True, "has_page_text": True, "has_confirmation": False, "has_email": False,
                          "email_seen_at": None, "screenshots": 1, "level": "text"}
        listed = {r["id"]: r["proof"]["level"] for r in (await client.get("/api/applications")).json()["applications"]}
        assert listed == {a: "none", b: "screenshot_only", c: "text", d: "email"}
        # a correction supersedes a proof_text: the old text no longer counts
        bad = (await _ev(client, a, "proof_text", {"text": "wrong page"})).json()["event_id"]
        assert (await _proof(client, a))["proof"]["level"] == "text"
        await _ev(client, a, "note", {}, detail="mistake", corrects_event_id=bad)
        assert (await _proof(client, a))["proof"]["level"] == "none"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "event_type,detail,source,level",
    [("note", "Submission confirmed on the page", None, "email"), ("note", "SUBMISSION CONFIRMED", None, "email"),
     ("note", "", EMAIL, "none"), ("note", "the portal said submission confirmed", None, "none"),
     ("note", "Submission confirmed", EMAIL, "email"), ("applied", "", EMAIL, "email"),
     ("applied", "I applied", None, "none"), ("lesson", "submission confirmed", None, "none")],
)
async def test_what_counts_as_email_proof(authenticated_async_context, event_type, detail, source, level):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        resp = await _ev(client, app_id, event_type, {}, detail=detail, **({"source": source} if source else {}))
        assert resp.status_code == 201, resp.text
        assert (await _proof(client, app_id))["proof"]["level"] == level


@pytest.mark.asyncio
async def test_receipts_and_mcp_carry_the_proof(authenticated_async_context):
    from src.api.mcp_server import mcp_runtime

    async with authenticated_async_context() as client:
        resp = await client.post("/api/jobs/bring", json=AD)
        job_id, app_id = resp.json()["job"]["id"], resp.json()["application_id"]
        receipt = await client.post(f"/api/receipts/{job_id}", json={})
        assert receipt.status_code == 201 and receipt.json()["application_id"] == app_id
        assert receipt.json()["proof"]["level"] == "none"
        await _ev(client, app_id, "proof_text", {"text": "Confirmed"})
        rid = receipt.json()["id"]
        assert (await client.get(f"/api/receipts/{rid}")).json()["proof"]["level"] == "text"
        assert (await client.get("/api/receipts")).json()["receipts"][0]["proof"]["level"] == "text"
        agent = await _bearer(client)
        token = agent.headers["authorization"].split()[1]
    await agent.aclose()
    import httpx2
    from mcp.client import Client
    from mcp.client.streamable_http import streamable_http_client

    from src.api.main import app

    http = httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://test",
                              headers={"Authorization": f"Bearer {token}"})
    async with mcp_runtime(), Client(streamable_http_client("http://test/api/mcp", http_client=http)) as mcp:
        def load(r):
            assert not r.is_error, r.content[0].text
            return json.loads(r.content[0].text)

        assert load(await mcp.call_tool("get_receipt", {"receipt_id": rid}))["proof"]["level"] == "text"
        listed = load(await mcp.call_tool("list_receipts", {}))["receipts"]
        assert listed[0]["proof"]["has_text"] is True
        assert load(await mcp.call_tool("get_application", {"application_id": app_id}))["proof"]["level"] == "text"
        assert load(await mcp.call_tool("list_applications", {}))["applications"][0]["proof"]["level"] == "text"


@pytest.mark.asyncio
async def test_a_receipt_confirmation_is_text_proof_and_clears_the_missing_list(authenticated_async_context):
    async with authenticated_async_context() as client:
        with_ref, blank = [await _bring(client, {**AD, "company": c}) for c in ("WithRef", "Blank")]
        for app_id, conf in ((with_ref, "REF-4471"), (blank, "  ")):
            made = await client.post(
                f"/api/applications/{app_id}/receipt", json={"confirmation": conf, "applied_at": _ago(9)}
            )
            assert made.status_code == 201, made.text
        assert (await _proof(client, with_ref))["proof"]["level"] == "text"
        assert (await _proof(client, blank))["proof"]["level"] == "none"
        missing = (await client.get("/api/whats-new")).json()["proof_missing"]
        assert [m["application_id"] for m in missing] == [blank]


@pytest.mark.asyncio
async def test_page_text_and_confirmation_are_told_apart_and_email_carries_its_time(authenticated_async_context):
    """S5e: the receipt sheet marks "Thank-you page saved" only from pasted text, never from a confirmation."""
    async with authenticated_async_context() as client:
        ref, mail = [await _bring(client, {**AD, "company": c}) for c in ("Ref", "Mail")]
        made = await client.post(f"/api/applications/{ref}/receipt", json={"confirmation": "REF-9"})
        assert made.status_code == 201, made.text
        p = (await _proof(client, ref))["proof"]
        assert (p["has_text"], p["has_confirmation"], p["has_page_text"]) == (True, True, False)
        await _ev(client, ref, "proof_text", {"text": "Thanks for applying"})
        p = (await _proof(client, ref))["proof"]
        assert (p["has_text"], p["has_confirmation"], p["has_page_text"], p["email_seen_at"]) == (True, True, True, None)
        later, earlier = _ago(1), _ago(3)
        await _ev(client, mail, "applied", {}, source={**EMAIL, "received_at": later})
        await _ev(client, mail, "note", {}, detail="Submission confirmed",
                  source={**EMAIL, "message_id": "<m2@northwind.example>", "received_at": earlier})
        p = (await _proof(client, mail))["proof"]
        assert p["has_email"] is True and p["has_confirmation"] is False and p["has_page_text"] is False
        assert datetime.fromisoformat(p["email_seen_at"]) == datetime.fromisoformat(earlier)


@pytest.mark.asyncio
async def test_only_still_applied_jobs_are_listed_as_missing_proof(authenticated_async_context):
    async with authenticated_async_context() as client:
        still, rejected, interview = [await _bring(client, {**AD, "company": c}) for c in ("Still", "Rej", "Int")]
        for app_id in (still, rejected, interview):
            await _ev(client, app_id, "applied", {}, occurred_at=_ago(8))
        await _ev(client, rejected, "rejected", {})
        await _ev(client, interview, "interview_scheduled", {}, scheduled_at=_ago(-3))
        missing = (await client.get("/api/whats-new")).json()["proof_missing"]
        assert [m["application_id"] for m in missing] == [still]


@pytest.mark.asyncio
async def test_links_in_proof_text_are_removed_before_storing(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        raw = "Thanks! See HTTPS://x.example/apply?token=SECRET123 or www.x.example/t?k=SECRET456 done"
        assert (await _ev(client, app_id, "proof_text", {"text": raw})).status_code == 201
        stored = [e for e in (await client.get(f"/api/applications/{app_id}")).json()["events"]
                  if e["event_type"] == "proof_text"][-1]["payload"]["text"]
        assert stored == "Thanks! See [link removed] or [link removed] done"
        n = settings.PROOF_TEXT_MAX_CHARS  # the cap counts what is kept, not the link
        assert (await _ev(client, app_id, "proof_text", {"text": "https://x.example/" + "a" * n})).status_code == 201


@pytest.mark.asyncio
async def test_proof_text_takes_no_source_and_corrects_only_its_own_kind(authenticated_async_context):
    async with authenticated_async_context() as client:
        a, b = [await _bring(client, {**AD, "company": c}) for c in ("A", "B")]
        assert (await _ev(client, a, "proof_text", {"text": "t"}, source=EMAIL)).status_code == 422
        first = (await _ev(client, a, "proof_text", {"text": "wrong page"})).json()["event_id"]
        note = (await _ev(client, a, "note", {}, detail="n")).json()["event_id"]
        other_app = (await _ev(client, b, "proof_text", {"text": "b"})).json()["event_id"]
        for target in (note, other_app, 10**9):
            resp = await _ev(client, a, "proof_text", {"text": "fix"}, corrects_event_id=target)
            assert resp.status_code == 422, (target, resp.text)
        fixed = await _ev(client, a, "proof_text", {"text": "right page"}, corrects_event_id=first)
        assert fixed.status_code == 201, fixed.text
        assert (await _proof(client, a))["proof"]["level"] == "text"


# ── "no proof after 7 days" ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_proof_after_seven_days_is_listed_once_and_never_asked_twice(authenticated_async_context):
    async with authenticated_async_context() as client:
        young, old, proved = [await _bring(client, {**AD, "company": c}) for c in ("Young", "Old", "Proved")]
        await _ev(client, young, "applied", {}, occurred_at=_ago(6))
        await _ev(client, old, "applied", {}, occurred_at=_ago(8))
        await _ev(client, proved, "applied", {}, occurred_at=_ago(9))
        await _ev(client, proved, "proof_text", {"text": "Confirmed"})

        async def missing():
            return (await client.get("/api/whats-new")).json()["proof_missing"]

        (row,) = await missing()
        assert (row["application_id"], row["company"], row["context"]) == (old, "Old", f"proof_missing:{old}")
        assert row["question"] == "No proof yet for Old after 7 days. Do you have the confirmation email or a screenshot?"
        first = await client.post("/api/asks", json={"application_id": old, "question": row["question"], "context": row["context"]})
        again = await client.post("/api/asks", json={"application_id": old, "question": "different", "context": row["context"]})
        assert first.status_code == again.status_code == 201 and first.json()["id"] == again.json()["id"]
        assert _sql("SELECT COUNT(*) FROM application_asks WHERE application_id = ?", (old,))[0][0] == 1
        assert _sql("SELECT COUNT(*) FROM application_events WHERE application_id = ? AND event_type = 'asked'", (old,))[0][0] == 1
        assert await missing() == []
        await client.post(f"/api/asks/{first.json()['id']}/answer", json={"answer": "no email"})
        assert await missing() == []  # answered: still not asked again
        other = await client.post("/api/asks", json={"application_id": old, "question": "q", "context": "something else"})
        assert other.json()["id"] != first.json()["id"]  # only the proof_missing context is de-duplicated


# ── logs ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_audit_log_never_carries_the_proof_text(authenticated_async_context, logs):
    secret_text = "SECRET-CONFIRMATION-TEXT-7731"
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        await _ev(client, app_id, "proof_text", {"text": secret_text, "page_host": "northwind.example"})
    blob = " ".join(f"{r.getMessage()} {sorted(r.__dict__.items(), key=str)}" for r in logs.records)
    assert secret_text not in blob
    recorded = [r for r in logs.records if getattr(r, "event", "") == "proof_text_recorded"][0]
    assert (recorded.chars, recorded.page_host, recorded.result) == (str(len(secret_text)), "northwind.example", "ok")


@pytest.mark.asyncio
async def test_parallel_no_proof_asks_write_one_row(authenticated_async_context, fixture_user_id):
    from src.repositories.database import JobDatabase
    from src.services.applications import asks

    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        dbs = [JobDatabase(str(settings.DB_PATH)) for _ in range(2)]
        try:
            for db in dbs:
                await db.connect()
            res = await asyncio.gather(
                *(asks.create_ask(db, fixture_user_id, app_id, "Proof?", f"proof_missing:{app_id}", "web") for db in dbs)
            )
        finally:
            for db in dbs:
                await db.close()
        assert res[0]["id"] == res[1]["id"]
        assert _sql("SELECT COUNT(*) FROM application_asks WHERE application_id = ?", (app_id,))[0][0] == 1
