"""The receipt names the exact FILE that was sent (gap roadmap slice 29).

Prod receipt 19 claimed a cover letter for a form with no letter field, and no receipt
could say which file left Job360: the CV was guessed (the newest saved). Now the
browser's upload and the kit's file link each write a server-only ``document_uploaded``
event with the sha256 of the exact bytes served, and ``record_application`` names those
uploads (``files``) when the caller names no version. Real rows, real values (rule #21).
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import logging
from types import SimpleNamespace
from typing import Any

import pytest
from httpx import AsyncClient

from tests.test_application_kit import _anon_client, _kit, _seed_profile, _token_of
from tests.test_mcp_server import _mcp_client, _mint_token, _payload

_AD = {
    "title": "Data Engineer", "company": "Northwind", "location": "London", "country": "GB",
    "apply_url": "https://northwind.example/careers/7", "description": "Pipelines. " * 8,
}
CV_V1 = "Ada Lovelace\nSenior data engineer\nPython, dbt, Snowflake."
CV_V2 = "Ada Lovelace\nStaff data engineer\nPython, dbt, Snowflake, Airflow."
LETTER = "Dear Northwind, I would love to join the data team."
FORM_HOST = "boards.greenhouse.io"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def _bring(client: AsyncClient) -> int:
    resp = await client.post("/api/jobs/bring", json=_AD)
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _save(client: AsyncClient, app_id: int, kind: str, text: str) -> int:
    resp = await client.post(f"/api/applications/{app_id}/artifacts", json={"kind": kind, "text": text})
    assert resp.status_code == 201, resp.text
    return int(resp.json()["artifact_id"])


async def _receipt(client: AsyncClient, app_id: int, **body: Any) -> dict[str, Any]:
    resp = await client.post(f"/api/applications/{app_id}/receipt", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _uploads(user_id: str, app_id: int) -> list[dict[str, Any]]:
    from src.api.dependencies import get_request_db

    async with contextlib.asynccontextmanager(get_request_db)() as db:
        cur = await db._db.execute(
            "SELECT id, payload, recorded_by FROM application_events WHERE user_id = ? AND application_id = ? "
            "AND event_type = 'document_uploaded' ORDER BY id",
            (user_id, app_id),
        )
        return [
            {"id": int(r["id"]), "recorded_by": r["recorded_by"], **json.loads(r["payload"])}
            for r in await cur.fetchall()
        ]


def _user(user_id: str):
    from src.api.auth_deps import CurrentUser

    return CurrentUser(id=user_id, email="ada@example.com", auth_via="token", actor_name="claude")


@pytest.fixture
def form_page(monkeypatch):
    """The caller's cloud browser sits on a Greenhouse form (no Chromium needed)."""
    from src.api.routes import browser as browser_route

    class _Pool:
        async def get(self, user_id: str, *, create: bool, load_state: Any = None) -> Any:
            assert create is False
            return SimpleNamespace(page=SimpleNamespace(url=f"https://{FORM_HOST}/northwind/jobs/7#app"))

    monkeypatch.setattr(browser_route.browser_agent, "get_pool", lambda: _Pool())


async def _browser_upload(user_id: str, app_id: int, kind: str) -> bytes:
    from src.api.routes import browser as browser_route

    _gate, files = browser_route._gate_and_files(_user(user_id), app_id, "")  # noqa: SLF001
    name, data = await files(kind)
    assert name.endswith("CV.pdf" if kind == "cv" else "_letter.pdf") and data[:5] == b"%PDF-"
    return data


# ── Writing it ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_browser_upload_records_the_sha_of_the_exact_bytes(authenticated_async_context, form_page):
    from src.services.tailoring.cv_render import RENDERER_VERSION

    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        cv_id = await _save(client, app_id, "cv", CV_V1)
    user_id = authenticated_async_context.fixture_user_id
    data = await _browser_upload(user_id, app_id, "cv")
    events = await _uploads(user_id, app_id)
    assert len(events) == 1
    ev = events[0]
    assert ev["sha256"] == _sha(data)
    assert (ev["kind"], ev["artifact_id"], ev["version"], ev["fmt"]) == ("cv", cv_id, 1, "pdf")
    assert ev["renderer_version"] == RENDERER_VERSION and ev["host"] == FORM_HOST
    assert ev["recorded_by"] == "token:claude"
    # A hash only: no bytes, no text, no file name.
    assert set(ev) == {"id", "recorded_by", "kind", "artifact_id", "version", "sha256", "fmt", "renderer_version", "host"}
    assert CV_V1 not in json.dumps(ev)


@pytest.mark.asyncio
async def test_a_failed_write_never_stops_the_upload(authenticated_async_context, form_page, monkeypatch):
    from src.services.applications import spine
    from src.utils.logger import get_audit_logger

    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        cv_id = await _save(client, app_id, "cv", CV_V1)
    user_id = authenticated_async_context.fixture_user_id

    async def _boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("db down")

    records: list[logging.LogRecord] = []
    handler = logging.Handler()
    handler.emit = records.append  # type: ignore[method-assign]
    get_audit_logger().addHandler(handler)
    monkeypatch.setattr(spine, "append_event", _boom)
    try:
        data = await _browser_upload(user_id, app_id, "cv")
    finally:
        get_audit_logger().removeHandler(handler)
    assert data[:5] == b"%PDF-"
    failed = [r for r in records if r.getMessage() == "document_uploaded_failed"]
    assert len(failed) == 1 and failed[0].artifact_id == cv_id and failed[0].application_id == app_id
    assert not hasattr(failed[0], "sha256") and not hasattr(failed[0], "text")
    monkeypatch.undo()
    assert await _uploads(user_id, app_id) == []


@pytest.mark.asyncio
async def test_each_file_link_serve_records_one_with_the_served_bytes(authenticated_async_context, fixture_user_id):
    from src.services.tailoring.cv_render import RENDERER_VERSION

    _seed_profile(fixture_user_id)
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        cv_id = await _save(client, app_id, "cv", CV_V1)
        kit = (await _kit(client, app_id)).json()
    token = _token_of(kit)
    served: list[bytes] = []
    async with _anon_client() as anon:
        for _ in range(2):
            resp = await anon.get(f"/api/files/{token}")
            assert resp.status_code == 200, resp.text
            served.append(resp.content)
    events = await _uploads(fixture_user_id, app_id)
    assert [e["sha256"] for e in events] == [_sha(b) for b in served]
    assert all(e["artifact_id"] == cv_id and e["kind"] == "cv" and e["host"] is None for e in events)
    assert all(e["fmt"] == "pdf" and e["renderer_version"] == RENDERER_VERSION for e in events)
    assert all(e["recorded_by"] == "web" for e in events), "the link's creator (the web kit call here)"
    assert token not in json.dumps(events)


@pytest.mark.asyncio
async def test_an_assistant_cannot_forge_one(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        cv_id = await _save(client, app_id, "cv", CV_V1)
        resp = await client.post(f"/api/applications/{app_id}/events", json={
            "event_type": "document_uploaded",
            "payload": {"kind": "cv", "artifact_id": cv_id, "sha256": "0" * 64, "fmt": "pdf",
                        "renderer_version": "x", "host": FORM_HOST},
        })
    assert resp.status_code == 422 and "written by Job360 itself" in resp.json()["detail"]
    assert await _uploads(authenticated_async_context.fixture_user_id, app_id) == []
    token = await _mint_token(authenticated_async_context)
    from src.api.mcp_server import mcp_runtime

    async with mcp_runtime():
        async with _mcp_client(token) as mcp:
            result = await mcp.call_tool("record_event", {
                "application_id": app_id, "event_type": "document_uploaded",
                "payload": {"kind": "cv", "artifact_id": cv_id, "sha256": "0" * 64},
            })
    assert result.is_error and "written by Job360 itself" in result.content[0].text


# ── The receipt reads it ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_ids_names_the_uploaded_files_and_freezes_them(authenticated_async_context, form_page):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        cv1 = await _save(client, app_id, "cv", CV_V1)
        letter = await _save(client, app_id, "cover_letter", LETTER)
        user_id = authenticated_async_context.fixture_user_id
        cv_bytes = await _browser_upload(user_id, app_id, "cv")
        letter_bytes = await _browser_upload(user_id, app_id, "cover_letter")
        await _save(client, app_id, "cv", CV_V2)  # newer, never uploaded: the old code froze this one
        made = await _receipt(client, app_id)
        full = (await client.get(f"/api/receipts/{made['receipt_id']}")).json()
        # A second receipt later, with no new upload: the first submission's files are not reused.
        again = await _receipt(client, app_id)
    up = {e["kind"]: e for e in await _uploads(user_id, app_id)}
    expect = [
        {"kind": "cv", "artifact_id": cv1, "sha256": _sha(cv_bytes), "fmt": "pdf", "event_id": up["cv"]["id"]},
        {"kind": "cover_letter", "artifact_id": letter, "sha256": _sha(letter_bytes), "fmt": "pdf",
         "event_id": up["cover_letter"]["id"]},
    ]
    assert made["cv_artifact_id"] == cv1 and made["cv_version_no"] == 1
    assert made["cover_letter_artifact_id"] == letter and made["cover_letter_sent"] is True
    assert made["files"] == expect and full["files"] == expect
    assert not any("cv_artifact_id not given" in w or "cover letter" in w for w in made["warnings"])
    assert full["cv_text"] == CV_V1 and full["cover_letter_text"] == LETTER
    assert again["files"] == [] and again["cover_letter_sent"] is False
    assert any(w.startswith("cv_artifact_id not given: froze the newest CV") for w in again["warnings"])

    from tests.test_receipt_freezes_answers import _sql

    stored = _sql("SELECT files FROM application_receipts WHERE id = ?", (made["receipt_id"],))[0][0]
    assert json.loads(stored) == expect
    token = await _mint_token(authenticated_async_context)
    from src.api.mcp_server import mcp_runtime

    async with mcp_runtime():
        async with _mcp_client(token) as mcp:
            got = _payload(await mcp.call_tool("get_receipt", {"receipt_id": made["receipt_id"]}))
    assert got["files"] == expect


@pytest.mark.asyncio
async def test_a_named_id_that_was_not_uploaded_warns_mismatch(authenticated_async_context, form_page):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        cv1 = await _save(client, app_id, "cv", CV_V1)
        letter1 = await _save(client, app_id, "cover_letter", LETTER)
        user_id = authenticated_async_context.fixture_user_id
        await _browser_upload(user_id, app_id, "cv")
        await _browser_upload(user_id, app_id, "cover_letter")
        cv2 = await _save(client, app_id, "cv", CV_V2)
        letter2 = await _save(client, app_id, "cover_letter", LETTER + " Again.")
        made = await _receipt(client, app_id, cv_artifact_id=cv2, cover_letter_artifact_id=letter2)
    assert made["cv_artifact_id"] == cv2 and made["cover_letter_artifact_id"] == letter2, "the named ids are kept"
    cv_warn = [w for w in made["warnings"] if w.startswith("cv_mismatch")]
    cl_warn = [w for w in made["warnings"] if w.startswith("cover_letter_mismatch")]
    assert len(cv_warn) == 1 and f"artifact {cv1}" in cv_warn[0]
    assert len(cl_warn) == 1 and f"artifact {letter1}" in cl_warn[0]
    assert made["files"] == []


@pytest.mark.asyncio
async def test_a_named_id_that_matches_the_upload_is_frozen_without_warning(authenticated_async_context, form_page):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        cv1 = await _save(client, app_id, "cv", CV_V1)
        await _browser_upload(authenticated_async_context.fixture_user_id, app_id, "cv")
        made = await _receipt(client, app_id, cv_artifact_id=cv1)
    assert [f["artifact_id"] for f in made["files"]] == [cv1]
    assert not any("mismatch" in w for w in made["warnings"])


@pytest.mark.asyncio
async def test_cv_uploaded_and_no_letter_uploaded_freezes_no_letter(
    authenticated_async_context, fixture_user_id, form_page
):
    """Prod receipt 19: a letter was claimed for a form that had no letter field."""
    _seed_profile(fixture_user_id)
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        cv1 = await _save(client, app_id, "cv", CV_V1)
        letter = await _save(client, app_id, "cover_letter", LETTER)
        await _browser_upload(fixture_user_id, app_id, "cv")
        # The assistant even downloaded the letter through the kit link: a download is not an upload.
        kit = (await _kit(client, app_id)).json()
        async with _anon_client() as anon:
            assert (await anon.get(f"/api/files/{_token_of(kit, 'cover_letter')}")).status_code == 200
        made = await _receipt(client, app_id)
        full = (await client.get(f"/api/receipts/{made['receipt_id']}")).json()
    assert made["cover_letter_sent"] is False and made["cover_letter_artifact_id"] is None
    assert full["cover_letter_text"] is None and full["cover_letter_sent"] is False
    assert [(f["kind"], f["artifact_id"]) for f in made["files"]] == [("cv", cv1)]
    assert any(f"a cover letter was downloaded (artifact {letter})" in w for w in made["warnings"])


@pytest.mark.asyncio
async def test_no_uploads_at_all_keeps_the_old_behaviour(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        await _save(client, app_id, "cv", CV_V1)
        cv2 = await _save(client, app_id, "cv", CV_V2)
        await _save(client, app_id, "cover_letter", LETTER)
        made = await _receipt(client, app_id)
    assert made["cv_artifact_id"] == cv2 and made["files"] == []
    assert made["cover_letter_sent"] is False
    assert any(w.startswith("cv_artifact_id not given: froze the newest CV") for w in made["warnings"])
    assert any(w.startswith("no cover_letter_artifact_id given: the receipt says") for w in made["warnings"])


@pytest.mark.asyncio
async def test_a_request_id_replay_returns_the_first_receipt_unchanged(authenticated_async_context, form_page):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        cv1 = await _save(client, app_id, "cv", CV_V1)
        user_id = authenticated_async_context.fixture_user_id
        await _browser_upload(user_id, app_id, "cv")
        first = await _receipt(client, app_id, request_id="submit-29-a")
        # A new version is uploaded after: the replay must not pick it up.
        await _save(client, app_id, "cv", CV_V2)
        await _browser_upload(user_id, app_id, "cv")
        again = await _receipt(client, app_id, request_id="submit-29-a")
    assert first["already_existed"] is False and again["already_existed"] is True
    assert {k: v for k, v in again.items() if k != "already_existed"} == {
        k: v for k, v in first.items() if k != "already_existed"
    }
    assert again["cv_artifact_id"] == cv1 and [f["artifact_id"] for f in again["files"]] == [cv1]
    from tests.test_receipt_freezes_answers import _sql

    assert _sql("SELECT COUNT(*) FROM application_receipts WHERE application_id = ?", (app_id,))[0][0] == 1
