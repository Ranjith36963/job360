"""Receipt counts line, "via <host>", sent CV/letter versions (weak spot #2, wave 2).

Computed at read time from the receipt row plus the newest ``form_filled`` event at or
before it; nothing stored twice, receipts stay append-only. Rule #21: values asserted.
Reference: tsenta home-curl.txt:224-227 ("6 of 6 fields · 0 skipped · 0 generic answers"),
:200-201 ("via Workday"), :216-217 (the sent résumé file shown with its version).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from tests.test_mcp_server import _mcp_client, _mint_token, _payload

_AD = {
    "title": "Data Engineer", "company": "Mactores", "location": "Remote",
    "apply_url": "https://other.example.com/jobs/1", "description": "Spark, Airflow, Python.",
}
_FORM = "https://boards.greenhouse.io/x/1"
_DOT = "·"


def _ans(i: int, source: str = "profile") -> dict[str, str]:
    return {"question": f"Q{i}", "answer": f"A{i}", "source": source}


async def _bring(client: AsyncClient) -> int:
    resp = await client.post("/api/jobs/bring", json=_AD)
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _form_filled(client: AsyncClient, app_id: int, **payload: Any) -> None:
    resp = await client.post(
        f"/api/applications/{app_id}/events",
        json={"event_type": "form_filled", "payload": {"form_url": _FORM, **payload}},
    )
    assert resp.status_code == 201, resp.text


async def _receipt(client: AsyncClient, app_id: int, **body: Any) -> dict[str, Any]:
    resp = await client.post(f"/api/applications/{app_id}/receipt", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_counts_line_exact_values(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        answers = [_ans(1), _ans(2), _ans(3), _ans(4, "guessed")]
        await _form_filled(client, app_id, fields_count=6, answers=answers)
        made = await _receipt(
            client, app_id, answers=answers, fields_filled={"Full name": "A", "Email": "a@b.co"}
        )
        full = (await client.get(f"/api/receipts/{made['receipt_id']}")).json()
    assert full["counts_line"] == f"6 of 6 fields {_DOT} 0 skipped {_DOT} 1 guessed"
    assert (full["fields_total"], full["fields_skipped"], full["guessed_count"]) == (6, 0, 1)


@pytest.mark.asyncio
async def test_skipped_counts_when_fields_skipped_listed(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        await _form_filled(client, app_id, fields_count=8, fields_skipped=["a", "b", "c"])
        made = await _receipt(client, app_id, fields_filled={f: "x" for f in "vwxyz"})
        page = (await client.get(f"/api/applications/{app_id}")).json()["receipts"]
    assert page[0]["id"] == made["receipt_id"]
    assert page[0]["counts_line"] == f"5 of 8 fields {_DOT} 3 skipped {_DOT} 0 guessed"
    assert page[0]["fields_skipped"] == 3


@pytest.mark.asyncio
async def test_skipped_defaults_to_the_gap_when_not_listed(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        await _form_filled(client, app_id, fields_count=5)
        made = await _receipt(client, app_id, fields_filled={"Full name": "A", "Email": "e"})
        full = (await client.get(f"/api/receipts/{made['receipt_id']}")).json()
    assert full["counts_line"] == f"2 of 5 fields {_DOT} 3 skipped {_DOT} 0 guessed"


@pytest.mark.asyncio
async def test_no_form_filled_event_means_no_counts_line(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        made = await _receipt(client, app_id, answers=[_ans(1, "guessed")])
        full = (await client.get(f"/api/receipts/{made['receipt_id']}")).json()
    assert full["counts_line"] == "" and full["fields_total"] is None
    assert full["fields_skipped"] == 0 and full["guessed_count"] == 1


@pytest.mark.asyncio
async def test_a_later_form_filled_does_not_change_an_earlier_receipt(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        await _form_filled(client, app_id, fields_count=3)
        made = await _receipt(client, app_id, fields_filled={"a": "1", "b": "2", "c": "3"})
        await _form_filled(client, app_id, fields_count=9)
        full = (await client.get(f"/api/receipts/{made['receipt_id']}")).json()
    assert full["counts_line"] == f"3 of 3 fields {_DOT} 0 skipped {_DOT} 0 guessed"


@pytest.mark.asyncio
async def test_form_host_is_the_filled_form_not_the_job_link(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        await _form_filled(client, app_id, fields_count=1)
        made = await _receipt(client, app_id)
        full = (await client.get(f"/api/receipts/{made['receipt_id']}")).json()
    assert full["form_host"] == "boards.greenhouse.io"


@pytest.mark.asyncio
async def test_form_host_falls_back_to_job_link_host(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        made = await _receipt(client, app_id)
        full = (await client.get(f"/api/receipts/{made['receipt_id']}")).json()
    assert full["form_host"] == "other.example.com (job link)"


@pytest.mark.asyncio
async def test_versions_match_frozen_artifacts_and_no_letter_is_none(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        saved = []
        for kind, text in (("cv", "CV one"), ("cv", "CV two"), ("cover_letter", "Dear team")):
            r = await client.post(f"/api/applications/{app_id}/artifacts", json={"kind": kind, "text": text})
            assert r.status_code == 201, r.text
            saved.append(r.json())
        made = await _receipt(
            client, app_id, cv_artifact_id=saved[0]["artifact_id"], cover_letter_artifact_id=saved[2]["artifact_id"]
        )
        page = (await client.get(f"/api/applications/{app_id}")).json()["receipts"][0]
        bare = await _receipt(client, app_id)
        bare_page = (await client.get(f"/api/applications/{app_id}")).json()["receipts"][0]
    assert made["cover_letter_version_no"] == 1
    assert (page["cv_version_no"], page["cover_letter_version_no"], page["cover_letter_sent"]) == (1, 1, True)
    assert bare["cover_letter_version_no"] is None
    assert bare_page["id"] == bare["receipt_id"]
    assert (bare_page["cv_version_no"], bare_page["cover_letter_version_no"]) == (2, None)
    assert bare_page["cover_letter_sent"] is False


@pytest.mark.asyncio
async def test_other_user_receipt_returns_404_and_no_counts(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        await _form_filled(client, app_id, fields_count=2)
        made = await _receipt(client, app_id)
    from src.api.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as other:
        creds = {"email": "counts-other@example.com", "password": "an0therS3cret!"}
        assert (await other.post("/api/auth/register", json=creds)).status_code in (200, 201)
        assert (await other.post("/api/auth/login", json=creds)).status_code == 200
        resp = await other.get(f"/api/receipts/{made['receipt_id']}")
        listed = (await other.get("/api/receipts")).json()
    assert resp.status_code == 404 and "counts_line" not in resp.json()
    assert listed["receipts"] == []


@pytest.mark.asyncio
async def test_mcp_receipt_tools_carry_same_counts_as_route(authenticated_async_context):
    from src.api.mcp_server import mcp_runtime

    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        await _form_filled(client, app_id, fields_count=2, answers=[_ans(1, "guessed")])
        made = await _receipt(client, app_id, answers=[_ans(1, "guessed")], fields_filled={"Email": "e"})
        route = (await client.get(f"/api/receipts/{made['receipt_id']}")).json()
    token = await _mint_token(authenticated_async_context)
    async with mcp_runtime():
        async with _mcp_client(token) as mcp:
            got = _payload(await mcp.call_tool("get_receipt", {"receipt_id": made["receipt_id"]}))
            listed = _payload(await mcp.call_tool("list_receipts", {}))["receipts"][0]
    assert route["counts_line"] == f"2 of 2 fields {_DOT} 0 skipped {_DOT} 1 guessed"
    for key in ("counts_line", "fields_total", "fields_skipped", "guessed_count", "form_host"):
        assert got[key] == route[key], key
    assert listed["counts_line"] == route["counts_line"] and listed["form_host"] == "boards.greenhouse.io"


@pytest.mark.asyncio
async def test_form_filled_accepts_fields_skipped_and_rejects_non_list(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        url = f"/api/applications/{app_id}/events"
        base = {"form_url": _FORM, "fields_count": 1}
        bad = await client.post(
            url, json={"event_type": "form_filled", "payload": {**base, "fields_skipped": "a"}}
        )
        bad_item = await client.post(
            url, json={"event_type": "form_filled", "payload": {**base, "fields_skipped": [3]}}
        )
        await _form_filled(client, app_id, fields_count=1, fields_skipped=["Phone"])
    assert bad.status_code == 422 and "fields_skipped" in bad.text
    assert bad_item.status_code == 422


def test_recipe_cap_and_skipped_is_taught():
    apply_md = Path(__file__).resolve().parent.parent / "src" / "recipes" / "apply.md"
    text = apply_md.read_text(encoding="utf-8")
    assert len(text.splitlines()) <= 70
    assert "fields_skipped" in text
