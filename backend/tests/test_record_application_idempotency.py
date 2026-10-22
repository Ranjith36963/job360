"""record_application is atomic and idempotent (gap roadmap slice 10, migration 0070).

The receipt, its `applied` event and the `request_id` row commit in ONE
transaction, so a network retry can no longer write a second permanent receipt
(receipts are append-only, M3). `applied_at` is parsed like `occurred_at`.
Rule #21: every assertion checks a real value.
"""
from __future__ import annotations

from typing import Any

import pytest

from src.services import idempotency
from src.services.applications import spine
from tests.test_mcp_server import _error_text, _mcp_client, _mint_token, _payload

AD = {"title": "Data Engineer", "company": "Northwind", "location": "London", "description": "Pipelines. " * 8,
      "apply_url": "https://northwind.example/careers/7"}


def _sql(statement: str, params: tuple = ()) -> list[Any]:
    from src.core import settings
    from src.repositories import pgsync

    conn = pgsync.connect(str(settings.DB_PATH))
    try:
        return conn.execute(statement, params).fetchall()
    finally:
        conn.close()


def _counts(app_id: int) -> tuple[int, int, int]:
    """(receipts, applied events, idempotency rows) for this application's user."""
    receipts = _sql("SELECT COUNT(*) FROM application_receipts WHERE application_id = ?", (app_id,))[0][0]
    applied = _sql(
        "SELECT COUNT(*) FROM application_events WHERE application_id = ? AND event_type = 'applied'", (app_id,)
    )[0][0]
    keys = _sql(
        "SELECT COUNT(*) FROM idempotency_keys k JOIN applications a ON a.user_id = k.user_id WHERE a.id = ?",
        (app_id,),
    )[0][0]
    return int(receipts), int(applied), int(keys)


async def _bring(client) -> int:
    resp = await client.post("/api/jobs/bring", json=AD)
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


def test_body_hash_is_canonical_and_keys_are_checked():
    assert idempotency.body_sha256({"a": 1, "b": [1, 2]}) == idempotency.body_sha256({"b": [1, 2], "a": 1})
    assert idempotency.body_sha256({"a": 1}) != idempotency.body_sha256({"a": 2})
    assert idempotency.check_key("x" * 128) == "x" * 128
    for bad in ("", "x" * 129, "line\nbreak", "tab\there"):
        with pytest.raises(idempotency.IdempotencyError) as exc:
            idempotency.check_key(bad)
        assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_same_request_id_twice_writes_one_receipt(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        body = {"channel": "company_site", "confirmation": "REF-4471", "request_id": "submit-7-a"}
        first = await client.post(f"/api/applications/{app_id}/receipt", json=body)
        second = await client.post(f"/api/applications/{app_id}/receipt", json=body)
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    one, two = first.json(), second.json()
    assert one["already_existed"] is False
    assert two["already_existed"] is True
    assert two["receipt_id"] == one["receipt_id"]
    assert two["event_id"] == one["event_id"]
    assert two["confirmation"] == "REF-4471"
    assert _counts(app_id) == (1, 1, 1)


@pytest.mark.asyncio
async def test_same_request_id_with_a_different_body_is_409(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        ok = await client.post(f"/api/applications/{app_id}/receipt", json={"note": "a", "request_id": "k1"})
        clash = await client.post(f"/api/applications/{app_id}/receipt", json={"note": "b", "request_id": "k1"})
    assert ok.status_code == 201, ok.text
    assert clash.status_code == 409, clash.text
    assert clash.json()["detail"].startswith("idempotency_key_reused:")
    assert _counts(app_id) == (1, 1, 1)


@pytest.mark.asyncio
async def test_a_failure_between_receipt_and_event_leaves_no_receipt(authenticated_async_context, monkeypatch):
    async def _boom(*_a: Any, **_k: Any) -> dict[str, Any]:
        raise RuntimeError("injected after the receipt INSERT")

    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        monkeypatch.setattr(spine, "append_event", _boom)
        with pytest.raises(RuntimeError, match="injected"):
            await client.post(f"/api/applications/{app_id}/receipt", json={"request_id": "r-atomic"})
        assert _counts(app_id) == (0, 0, 0)
        monkeypatch.undo()
        retry = await client.post(f"/api/applications/{app_id}/receipt", json={"request_id": "r-atomic"})
    assert retry.status_code == 201, retry.text
    assert retry.json()["already_existed"] is False
    assert _counts(app_id) == (1, 1, 1)


@pytest.mark.asyncio
async def test_a_racing_retry_returns_the_winner(authenticated_async_context, monkeypatch):
    """Both retries passed the lookup: the UNIQUE key rolls the loser back whole."""
    real_lookup = idempotency.lookup
    calls = {"n": 0}

    async def _first_miss(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        return None if calls["n"] == 1 else await real_lookup(*args, **kwargs)

    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        won = (await client.post(f"/api/applications/{app_id}/receipt", json={"request_id": "race"})).json()
        monkeypatch.setattr(idempotency, "lookup", _first_miss)
        lost = await client.post(f"/api/applications/{app_id}/receipt", json={"request_id": "race"})
    assert lost.status_code == 201, lost.text
    assert lost.json()["already_existed"] is True
    assert lost.json()["receipt_id"] == won["receipt_id"]
    assert calls["n"] == 2
    assert _counts(app_id) == (1, 1, 1)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad, says",
    [("yesterday", "applied_at must be ISO-8601"), ("2026-10-01T10:00:00", "applied_at must include a timezone")],
)
async def test_bad_applied_at_is_422_naming_applied_at(authenticated_async_context, bad, says):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        resp = await client.post(f"/api/applications/{app_id}/receipt", json={"applied_at": bad})
        good = await client.post(
            f"/api/applications/{app_id}/receipt", json={"applied_at": "2026-10-01T10:00:00Z"}
        )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"].startswith(says)
    assert good.status_code == 201, good.text
    assert good.json()["sent_at"] == "2026-10-01T10:00:00+00:00"
    assert _counts(app_id) == (1, 1, 0)


@pytest.mark.asyncio
async def test_bad_request_id_is_422(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        resp = await client.post(f"/api/applications/{app_id}/receipt", json={"request_id": "x" * 129})
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"] == "request_id must be 1-128 printable characters"
    assert _counts(app_id) == (0, 0, 0)


@pytest.mark.asyncio
async def test_mcp_tool_and_route_share_the_key(authenticated_async_context):
    from src.api.mcp_server import mcp_runtime

    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        route = (
            await client.post(f"/api/applications/{app_id}/receipt", json={"note": "sent", "request_id": "both"})
        ).json()
    token = await _mint_token(authenticated_async_context)
    async with mcp_runtime():
        async with _mcp_client(token) as mcp:
            again = _payload(await mcp.call_tool(
                "record_application", {"application_id": app_id, "note": "sent", "request_id": "both"}
            ))
            clash = _error_text(await mcp.call_tool(
                "record_application", {"application_id": app_id, "note": "other", "request_id": "both"}
            ))
            bad_date = _error_text(await mcp.call_tool(
                "record_application", {"application_id": app_id, "applied_at": "last week"}
            ))
    assert route["already_existed"] is False
    assert again["already_existed"] is True
    assert again["receipt_id"] == route["receipt_id"]
    assert "409: idempotency_key_reused: this request_id was already used" in clash
    assert "422: applied_at must be ISO-8601" in bad_date
    assert _counts(app_id) == (1, 1, 1)
