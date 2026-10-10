"""S7 - PROOF OF APPLICATION (owner decision 2026-10-10).

Screenshots (web upload + single-use link), pasted confirmation text, the proof
level, the "no proof after 7 days" ask. Real doors (HTTP + MCP), VALUES asserted
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
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 32
MAX = settings.PROOF_SCREENSHOT_MAX_BYTES
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


async def _link(client: AsyncClient, app_id: int) -> str:
    resp = await client.post(f"/api/applications/{app_id}/proof/link")
    assert resp.status_code == 201, resp.text
    return "/api/proof/" + resp.json()["url"].rsplit("/", 1)[1]


def _post(client: AsyncClient, path: str, data: bytes, name: str = "shot.png"):
    return client.post(path, files={"file": (name, data, "image/png")})


def _anon() -> AsyncClient:
    from src.api.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


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
    resp = await client.get(f"/api/applications/{app_id}/proof")
    assert resp.status_code == 200, resp.text
    return resp.json()


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


# ── the link: upload, single use, expiry, ownership ──────────────────────────


@pytest.mark.asyncio
async def test_link_upload_stores_the_image_and_leaves_a_trail(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        minted = await client.post(f"/api/applications/{app_id}/proof/link")
        body = minted.json()
        assert minted.headers["cache-control"] == "no-store"
        assert (body["application_id"], body["single_use"], body["max_bytes"]) == (app_id, True, MAX)
        assert body["accepts"] == ["image/png", "image/jpeg", "image/webp"]
        assert body["url"].startswith(settings.SITE_BASE_URL + "/api/proof/")
        ttl = datetime.fromisoformat(body["expires_at"]) - datetime.now(timezone.utc)
        assert timedelta(minutes=settings.PROOF_LINK_TTL_MINUTES - 1) < ttl <= timedelta(minutes=5)
        path = "/api/proof/" + body["url"].rsplit("/", 1)[1]
        async with _anon() as anon:
            up = await _post(anon, path, PNG)
        assert up.status_code == 201, up.text
        assert up.headers["cache-control"] == "no-store" and up.headers["referrer-policy"] == "no-referrer"
        assert (up.json()["application_id"], up.json()["mime"], up.json()["size"]) == (app_id, "image/png", len(PNG))
        state = await _proof(client, app_id)
        assert state["proof"] == {"has_text": False, "has_email": False, "screenshots": 1, "level": "screenshot_only"}
        shot = state["screenshots"][0]
        assert shot["id"] == up.json()["screenshot_id"] and shot["created_by"] == "web" and "bytes" not in shot
        assert len(shot["sha256"]) == 64
        image = await client.get(f"/api/applications/{app_id}/proof/screenshots/{shot['id']}")
        assert image.content == PNG and image.headers["content-type"] == "image/png"
        assert image.headers["x-content-type-options"] == "nosniff"
        detail = (await client.get(f"/api/applications/{app_id}")).json()
        trail = [e for e in detail["events"] if e["event_type"] == "proof_screenshot"]
        assert [(e["payload"]["via"], e["payload"]["size"], e["payload"]["mime"]) for e in trail] == [
            ("link", len(PNG), "image/png")
        ]
        assert detail["proof"]["screenshots"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data,name,status",
    [(b"", "e.png", 422), (b"not an image at all" * 5, "fake.png", 415), (b"GIF89a" + b"\0" * 40, "a.gif", 415),
     (PNG + b"\0" * MAX, "big.png", 413), (JPEG, "a.jpg", 201), (WEBP, "a.webp", 201), (PNG, "named.exe", 201)],
)
async def test_file_checks_use_the_bytes_not_the_name_and_a_refusal_keeps_the_link(
    authenticated_async_context, data, name, status
):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        path = await _link(client, app_id)
        async with _anon() as anon:
            assert (await _post(anon, path, data, name)).status_code == status
            if status != 201:  # the link was not burned: a good file still goes through
                assert (await _post(anon, path, PNG)).status_code == 201


@pytest.mark.asyncio
async def test_a_link_works_once_and_dies_after_five_minutes(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        path, expired = await _link(client, app_id), await _link(client, app_id)
        _sql("UPDATE proof_upload_links SET expires_at = ? WHERE token_hash = ?",
             (_ago(1), __import__("hashlib").sha256(expired.rsplit("/", 1)[1].encode()).hexdigest()))
        async with _anon() as anon:
            assert (await _post(anon, path, PNG)).status_code == 201
            again = await _post(anon, path, PNG)
            assert again.status_code == 410 and "ask your assistant" in again.json()["detail"]
            assert (await _post(anon, expired, PNG)).status_code == 410
            assert (await _post(anon, "/api/proof/" + "x" * 43, PNG)).status_code == 404
            assert (await _post(anon, "/api/proof/short", PNG)).status_code == 404
            assert (await anon.post(path)).status_code in (404, 410, 422)  # no file at all: never 401/403
            assert (await anon.post("/api/proof/" + "y" * 43)).status_code == 404  # no body needed to refuse
        assert len((await _proof(client, app_id))["screenshots"]) == 1


@pytest.mark.asyncio
async def test_nobody_can_mint_for_or_store_into_another_users_application(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        mine, other = await _bring(client), await _bring(client, {**AD, "company": "Southwind"})
        path = await _link(client, mine)
        async with _anon() as anon:
            up = await _post(anon, path, PNG)
        assert up.json()["application_id"] == mine  # the token names ONE application
        assert (await _proof(client, other))["screenshots"] == []
        assert (await client.post("/api/applications/987654321/proof/link")).status_code == 404
        assert (await client.get("/api/applications/987654321/proof")).status_code == 404
        assert (await _post(client, "/api/applications/987654321/proof/screenshots", PNG)).status_code == 404
        sid = up.json()["screenshot_id"]
        assert (await client.get(f"/api/applications/{other}/proof/screenshots/{sid}")).status_code == 404
        assert (await client.delete(f"/api/applications/{other}/proof/screenshots/{sid}")).status_code == 404
        assert _sql("SELECT user_id FROM application_proof_screenshots WHERE id = ?", (sid,))[0][0] == fixture_user_id


@pytest.mark.asyncio
async def test_the_mint_is_capped_per_hour(authenticated_async_context, monkeypatch):
    monkeypatch.setattr(settings, "PROOF_LINKS_MAX_PER_HOUR", 2)
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        codes = [(await client.post(f"/api/applications/{app_id}/proof/link")).status_code for _ in range(3)]
        assert codes == [201, 201, 429]


# ── the count limit, delete, export ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_three_live_screenshots_at_most_and_delete_frees_a_slot(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        url = f"/api/applications/{app_id}/proof/screenshots"
        ids = []
        for _ in range(settings.PROOF_SCREENSHOTS_MAX_LIVE):
            ok = await _post(client, url, PNG)
            assert ok.status_code == 201 and ok.json()["created_by"] == "web"
            ids.append(ok.json()["id"])
        refused = await _post(client, url, PNG)
        assert refused.status_code == 409 and "PROOF_SCREENSHOTS_MAX_LIVE" in refused.json()["detail"]
        path = await _link(client, app_id)  # the link route refuses too, and the refusal keeps the link
        async with _anon() as anon:
            assert (await _post(anon, path, PNG)).status_code == 409
            assert (await client.delete(f"{url}/{ids[0]}")).status_code == 200
            assert (await _post(anon, path, PNG)).status_code == 201
        assert (await _post(client, url, PNG)).status_code == 409


@pytest.mark.asyncio
async def test_delete_erases_the_bytes_keeps_the_row_and_the_export_never_has_bytes(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        url = f"/api/applications/{app_id}/proof/screenshots"
        keep, gone = (await _post(client, url, PNG)).json()["id"], (await _post(client, url, JPEG)).json()["id"]
        await _link(client, app_id)
        deleted = await client.delete(f"{url}/{gone}")
        today = datetime.now(timezone.utc).date().isoformat()
        assert deleted.json()["delete_note"] == f"Deleted by you, {today}" and deleted.json()["deleted_at"]
        assert _sql("SELECT bytes FROM application_proof_screenshots WHERE id = ?", (gone,))[0][0] is None
        assert (await client.delete(f"{url}/{gone}")).status_code == 404
        assert (await client.get(f"{url}/{gone}")).status_code == 410
        notes = _sql(
            "SELECT detail FROM application_events WHERE application_id = ? AND event_type = 'note'", (app_id,)
        )
        assert [r[0] for r in notes] == ["Screenshot deleted by you"]
        state = await _proof(client, app_id)
        assert state["proof"]["screenshots"] == 1
        assert [(s["id"], s["deleted_at"] is None) for s in state["screenshots"]] == [(keep, True), (gone, False)]
        exported = (await client.get("/api/auth/users/me/export")).json()
        rows = exported["application_proof_screenshots"]
        assert {r["id"] for r in rows} == {keep, gone} and all("bytes" not in r for r in rows)
        assert "proof_upload_links" not in exported and "_incomplete_tables" not in exported
        assert [r["delete_note"] for r in rows if r["id"] == gone] == [f"Deleted by you, {today}"]


def test_both_tables_are_registered_for_erasure_and_only_one_for_export():
    from scripts.observe import PER_USER_TABLES
    from src.repositories.database import JobDatabase

    for tbl in ("application_proof_screenshots", "proof_upload_links"):
        assert tbl in JobDatabase._PER_USER_TABLES and (tbl, "user_id") in PER_USER_TABLES
    assert "application_proof_screenshots" in JobDatabase._EXPORT_TABLES
    assert "proof_upload_links" not in JobDatabase._EXPORT_TABLES
    assert "bytes" not in JobDatabase._EXPORT_COLUMNS["application_proof_screenshots"].split(", ")


@pytest.mark.asyncio
async def test_image_routes_are_session_only_but_the_link_and_the_read_take_a_token(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        sid = (await _post(client, f"/api/applications/{app_id}/proof/screenshots", PNG)).json()["id"]
        url = f"/api/applications/{app_id}/proof/screenshots"
        async with await _bearer(client) as agent:
            assert (await _post(agent, url, PNG)).status_code == 403
            assert (await agent.get(f"{url}/{sid}")).status_code == 403
            assert (await agent.delete(f"{url}/{sid}")).status_code == 403
            assert (await agent.post(f"/api/applications/{app_id}/proof/link")).status_code == 201
            assert (await agent.get(f"/api/applications/{app_id}/proof")).json()["proof"]["screenshots"] == 1


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
async def test_the_level_is_the_strongest_proof_and_a_correction_withdraws_text(authenticated_async_context):
    async with authenticated_async_context() as client:
        a, b, c, d = [await _bring(client, {**AD, "company": f"Co{i}"}) for i in range(4)]
        await _post(client, f"/api/applications/{b}/proof/screenshots", PNG)
        await _ev(client, c, "proof_text", {"text": "Application received"})
        await _post(client, f"/api/applications/{c}/proof/screenshots", PNG)  # text beats screenshot
        await _ev(client, d, "applied", {}, source=EMAIL)
        await _ev(client, d, "proof_text", {"text": "also pasted"})  # email beats text
        got = {i: (await _proof(client, i))["proof"] for i in (a, b, c, d)}
        assert [got[i]["level"] for i in (a, b, c, d)] == ["none", "screenshot_only", "text", "email"]
        assert got[c] == {"has_text": True, "has_email": False, "screenshots": 1, "level": "text"}
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
        link = load(await mcp.call_tool("get_proof_upload_link", {"application_id": app_id}))
        assert link["single_use"] and "/api/proof/" in link["url"]
        denied = await mcp.call_tool("get_proof_upload_link", {"application_id": 987654321})
        assert denied.is_error and "404" in denied.content[0].text


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


# ── masking and logs ─────────────────────────────────────────────────────────


def test_the_upload_token_is_masked_in_paths_and_the_proof_routes_are_not():
    from src.utils.logger import redact_path

    assert redact_path("/api/proof/abc123") == "/api/proof/[redacted]"
    assert redact_path("http://test/api/proof/abc123?x=1") == "http://test/api/proof/[redacted]"
    for path in ("/api/applications/5/proof/link", "/api/applications/5/proof", "/api/applications/5/proof/screenshots/2"):
        assert redact_path(path) == path


@pytest.mark.asyncio
async def test_the_audit_log_never_carries_tokens_text_or_bytes(authenticated_async_context, logs):
    secret_text = "SECRET-CONFIRMATION-TEXT-7731"
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        path = await _link(client, app_id)
        token = path.rsplit("/", 1)[1]
        async with _anon() as anon:
            await _post(anon, path, PNG)
            await _post(anon, path, PNG)  # 410
            await _post(anon, "/api/proof/" + "z" * 43, PNG)  # 404
        await _ev(client, app_id, "proof_text", {"text": secret_text, "page_host": "northwind.example"})
        sid = (await _proof(client, app_id))["screenshots"][0]["id"]
        await client.delete(f"/api/applications/{app_id}/proof/screenshots/{sid}")
    names = {getattr(r, "event", None) for r in logs.records}
    assert {"proof_link_created", "proof_upload", "proof_link_used", "proof_text_recorded",
            "proof_screenshot_deleted", "proof_upload_refused"} <= names
    from hashlib import sha256

    blob = " ".join(f"{r.getMessage()} {sorted(r.__dict__.items(), key=str)}" for r in logs.records)
    for forbidden in (token, sha256(token.encode()).hexdigest(), secret_text, "PNG", repr(PNG)):
        assert forbidden not in blob, forbidden
    recorded = [r for r in logs.records if getattr(r, "event", "") == "proof_text_recorded"][0]
    assert (recorded.chars, recorded.page_host, recorded.result) == (str(len(secret_text)), "northwind.example", "ok")


# ── review fixes: the body is read before a pooled DB connection is borrowed; the count and ask are race-proof ──


def _spy(monkeypatch, read_ok=True):
    """Wrap the route's short connection and ``_read_file``: ``log`` gets "open"/"read" (+ how many connections were
    open at the read) so a test can see the ORDER and that no connection is held during the body read."""
    from contextlib import asynccontextmanager

    from src.api.routes import proof as route

    log: list[Any] = []
    state = {"open": 0}
    real_short, real_read = route._short_db, route._read_file

    @asynccontextmanager
    async def short():
        async with real_short() as db:
            state["open"] += 1
            log.append("open")
            try:
                yield db
            finally:
                state["open"] -= 1

    async def read(request):
        log.append(("read", state["open"]))
        return await real_read(request)

    monkeypatch.setattr(route, "_short_db", short)
    monkeypatch.setattr(route, "_read_file", read)
    return log


def _spy_db(monkeypatch):
    """Override ``get_request_db`` with a spy; returns the list of times a handle was borrowed."""
    from src.api.dependencies import get_request_db
    from src.api.main import app

    entered: list[str] = []

    async def fake():
        entered.append("db")
        yield object()

    monkeypatch.setitem(app.dependency_overrides, get_request_db, fake)
    return entered


MULTIPART = {"content-type": "multipart/form-data; boundary=x"}


@pytest.mark.asyncio
async def test_an_unknown_token_is_404_without_reading_the_body(authenticated_async_context, monkeypatch):
    async with authenticated_async_context():
        log = _spy(monkeypatch)
        over_cap = {**MULTIPART, "content-length": str(MAX + 10**6)}
        async with _anon() as anon:
            for token in ("bad-shape", "u" * 43):
                assert (await anon.post(f"/api/proof/{token}", content=b"x", headers=over_cap)).status_code == 404

            async def boom():
                raise AssertionError("the body stream was consumed")
                yield b""

            assert (await anon.post("/api/proof/" + "v" * 43, content=boom(), headers=MULTIPART)).status_code == 404
        assert not [e for e in log if e != "open"], log  # never read


@pytest.mark.asyncio
async def test_a_used_or_expired_link_is_410_without_reading_the_body(authenticated_async_context, monkeypatch):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        used, expired = await _link(client, app_id), await _link(client, app_id)
        _sql("UPDATE proof_upload_links SET expires_at = ? WHERE token_hash = ?",
             (_ago(1), __import__("hashlib").sha256(expired.rsplit("/", 1)[1].encode()).hexdigest()))
        async with _anon() as anon:
            assert (await _post(anon, used, PNG)).status_code == 201
            log = _spy(monkeypatch)
            over_cap = {**MULTIPART, "content-length": str(MAX + 10**6)}
            for path in (used, expired):
                gone = await anon.post(path, content=b"x", headers=over_cap)
                assert gone.status_code == 410 and gone.headers["cache-control"] == "no-store"
        assert not [e for e in log if e != "open"], log


@pytest.mark.asyncio
async def test_a_failed_web_body_read_never_borrows_a_db_connection(authenticated_async_context, monkeypatch):
    async with authenticated_async_context() as client:
        entered = _spy_db(monkeypatch)
        big = {**MULTIPART, "content-length": str(MAX + 10**6)}
        path = "/api/applications/1/proof/screenshots"
        assert (await client.post(path, content=b"x", headers=big)).status_code == 413
        assert (await client.post(path, content=b"not multipart")).status_code == 422
        assert entered == [], "the body must be read BEFORE get_request_db is resolved"


@pytest.mark.asyncio
async def test_no_pooled_connection_is_held_while_the_body_is_read(authenticated_async_context, monkeypatch):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        path = await _link(client, app_id)
        log = _spy(monkeypatch)
        async with _anon() as anon:
            big = {**MULTIPART, "content-length": str(MAX + 10**6)}
            assert (await anon.post(path, content=b"x", headers=big)).status_code == 413
            assert (await anon.post(path, content=b"not multipart")).status_code == 422
            assert (await _post(anon, path, PNG)).status_code == 201  # the link survived both failed reads
        assert ("read", 0) in log and ("read", 1) not in log, log  # every read saw zero open connections


@pytest.mark.asyncio
async def test_a_slow_upload_times_out_with_408_holding_no_connection(authenticated_async_context, monkeypatch):
    from src.api.routes import proof as proof_route

    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        path = await _link(client, app_id)
        log = _spy(monkeypatch)

        async def never(request):
            log.append(("read", 0))  # _spy's wrapper is replaced; the short connection is closed by now
            await asyncio.Event().wait()

        monkeypatch.setattr(proof_route, "_read_file", never)
        monkeypatch.setattr(settings, "PROOF_UPLOAD_READ_SECONDS", 0.05)
        async with _anon() as anon:
            slow = await _post(anon, path, PNG)
        assert slow.status_code == 408 and slow.json()["detail"] == "upload took too long"
        assert slow.headers["cache-control"] == "no-store"
        assert log == ["open", ("read", 0)]


@pytest.mark.asyncio
async def test_a_slow_web_upload_times_out_with_408_before_any_db_borrow(authenticated_async_context, monkeypatch):
    from src.api.routes import proof as proof_route

    async def never(_request):
        await asyncio.Event().wait()

    monkeypatch.setattr(proof_route, "_read_file", never)
    monkeypatch.setattr(settings, "PROOF_UPLOAD_READ_SECONDS", 0.05)
    async with authenticated_async_context() as client:
        entered = _spy_db(monkeypatch)
        resp = await _post(client, "/api/applications/1/proof/screenshots", PNG)
        assert resp.status_code == 408 and resp.json()["detail"] == "upload took too long"
        assert entered == []


@pytest.mark.asyncio
async def test_parallel_screenshots_cannot_exceed_the_live_cap(authenticated_async_context, fixture_user_id):
    from src.repositories.database import JobDatabase
    from src.services.applications import proof

    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        for _ in range(settings.PROOF_SCREENSHOTS_MAX_LIVE - 1):
            assert (await _post(client, f"/api/applications/{app_id}/proof/screenshots", PNG)).status_code == 201
        dbs = [JobDatabase(str(settings.DB_PATH)) for _ in range(2)]  # one connection each
        try:
            for db in dbs:
                await db.connect()
            now = datetime.now(timezone.utc)
            res = await asyncio.gather(
                *(proof.store_screenshot(db, fixture_user_id, app_id, PNG, "web", "web", now) for db in dbs),
                return_exceptions=True,
            )
        finally:
            for db in dbs:
                await db.close()
        refused = [r for r in res if isinstance(r, proof.SpineError)]
        assert len(refused) == 1 and refused[0].status_code == 409, res
        live = _sql(
            "SELECT COUNT(*) FROM application_proof_screenshots WHERE application_id = ? AND deleted_at IS NULL",
            (app_id,),
        )
        assert live[0][0] == settings.PROOF_SCREENSHOTS_MAX_LIVE


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
