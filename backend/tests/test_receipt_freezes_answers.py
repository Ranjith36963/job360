"""record_application freezes the form's answers when none are passed (gap roadmap slice 11).

Prod receipt 12 read "0 of 11 fields · 11 skipped" for a form the assistant filled with
11 answers: the receipt never looked at the form events already recorded. Now the newest
form_filled / form_swept is frozen (``answers_from``), an empty channel reads the form's
host, and a sensitive answer the user did not agree to remember is stored as its sha256
only (G023) - in the event AND the receipt, both append-only (M3). Real rows, real values
(rule #21).
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from httpx import AsyncClient

from tests.test_mcp_server import _mcp_client, _mint_token, _payload

_AD = {
    "title": "Data Engineer", "company": "Northwind", "location": "London", "country": "GB",
    "apply_url": "https://northwind.example/careers/7", "description": "Pipelines. " * 8,
}
_FORM = "https://boards.greenhouse.io/northwind/jobs/7"
_DOT = "·"
WHY = {"question": "Why this role?", "answer": "I like the product.", "source": "profile"}
NOTICE = {"question": "Which team interests you most?", "answer": "Platform", "source": "profile"}
VISA = {"question": "Will you require visa sponsorship?", "answer": "Yes - Skilled Worker 7731", "source": "profile"}
PAY = {"question": "What are your salary expectations?", "answer": "GBP 61,500", "source": "profile"}


def _sql(statement: str, params: tuple = ()) -> list[Any]:
    from src.core import settings
    from src.repositories import pgsync

    conn = pgsync.connect(str(settings.DB_PATH))
    try:
        return conn.execute(statement, params).fetchall()
    finally:
        conn.close()


async def _bring(client: AsyncClient, ad: dict[str, Any] = _AD) -> int:
    resp = await client.post("/api/jobs/bring", json=ad)
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _event(client: AsyncClient, app_id: int, event_type: str, payload: dict[str, Any]) -> int:
    resp = await client.post(f"/api/applications/{app_id}/events", json={"event_type": event_type, "payload": payload})
    assert resp.status_code == 201, resp.text
    return int(resp.json()["event_id"])


async def _fill(client: AsyncClient, app_id: int, answers: list[dict[str, Any]], form: str = _FORM) -> int:
    return await _event(client, app_id, "form_filled", {"form_url": form, "fields_count": len(answers), "answers": answers})


async def _receipt(client: AsyncClient, app_id: int, **body: Any) -> dict[str, Any]:
    resp = await client.post(f"/api/applications/{app_id}/receipt", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _read(client: AsyncClient, receipt_id: int) -> dict[str, Any]:
    resp = await client.get(f"/api/receipts/{receipt_id}")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _qa(answers: list[dict[str, Any]]) -> list[tuple[str, str]]:
    return [(a["question"], a["answer"]) for a in answers]


@pytest.mark.asyncio
async def test_no_answers_freezes_the_newest_form_filled(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        await _fill(client, app_id, [WHY])  # an earlier attempt: never the source
        fill = await _fill(client, app_id, [WHY, NOTICE])
        made = await _receipt(client, app_id)
        full = await _read(client, made["receipt_id"])
    assert made["answers_from"] == f"form_filled#{fill}"
    assert full["answers_from"] == f"form_filled#{fill}"
    assert _qa(full["answers"]) == [(WHY["question"], WHY["answer"]), (NOTICE["question"], NOTICE["answer"])]
    assert full["counts_line"] == f"2 of 2 fields {_DOT} 0 skipped {_DOT} 0 guessed"
    stored = _sql("SELECT answers, answers_from, answers_count FROM application_receipts WHERE id = ?",
                  (made["receipt_id"],))[0]
    assert [a["answer"] for a in json.loads(stored[0])] == ["I like the product.", NOTICE["answer"]]
    assert (stored[1], stored[2]) == (f"form_filled#{fill}", 2)
    token = await _mint_token(authenticated_async_context)
    from src.api.mcp_server import mcp_runtime

    async with mcp_runtime():
        async with _mcp_client(token) as mcp:
            got = _payload(await mcp.call_tool("get_receipt", {"receipt_id": made["receipt_id"]}))
    assert got["answers_from"] == f"form_filled#{fill}" and _qa(got["answers"]) == _qa(full["answers"])


@pytest.mark.asyncio
async def test_a_newer_form_swept_wins_over_an_older_form_filled(authenticated_async_context):
    fixed = {**NOTICE, "answer": "Data"}
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        await _fill(client, app_id, [WHY, NOTICE])
        sweep = await _event(client, app_id, "form_swept", {"fields_checked": 2, "fixed": 1, "answers": [WHY, fixed]})
        made = await _receipt(client, app_id)
        full = await _read(client, made["receipt_id"])
    assert made["answers_from"] == f"form_swept#{sweep}"
    assert _qa(full["answers"]) == [(WHY["question"], WHY["answer"]), (NOTICE["question"], "Data")]


@pytest.mark.asyncio
async def test_explicit_answers_are_used_as_given(authenticated_async_context):
    given = [{"question": "Portfolio", "answer": "https://ada.example", "source": "profile"}]
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        await _fill(client, app_id, [WHY, NOTICE])
        made = await _receipt(client, app_id, answers=given, channel="referral")
        full = await _read(client, made["receipt_id"])
    assert made["answers_from"] == "" and full["answers_from"] == ""
    assert _qa(full["answers"]) == [("Portfolio", "https://ada.example")]
    assert _sql("SELECT answers_count FROM application_receipts WHERE id = ?", (made["receipt_id"],))[0][0] is None


@pytest.mark.asyncio
async def test_channel_defaults_to_the_form_host_and_an_explicit_channel_wins(authenticated_async_context):
    async with authenticated_async_context() as client:
        site = await _bring(client)
        await _fill(client, site, [WHY])
        linkedin = await _bring(client, {**_AD, "title": "Data Engineer (LinkedIn)", "apply_url": "https://northwind.example/careers/8"})
        await _fill(client, linkedin, [WHY], form="https://www.linkedin.com/jobs/view/8/apply")
        given = await _bring(client, {**_AD, "title": "Data Engineer (Leeds)", "apply_url": "https://northwind.example/careers/9"})
        await _fill(client, given, [WHY])
        on_site = await _receipt(client, site)
        on_linkedin = await _receipt(client, linkedin)
        explicit = await _receipt(client, given, channel="referral")
    assert on_site["channel"] == "company_site"
    assert "channel not given: stored 'company_site' from the form's host boards.greenhouse.io" in on_site["warnings"]
    assert on_linkedin["channel"] == "linkedin_easy_apply"
    assert explicit["channel"] == "referral"
    assert not any(w.startswith("channel not given") for w in explicit["warnings"])


@pytest.mark.asyncio
async def test_a_count_with_no_answers_warns_and_the_counts_line_keeps_the_count(
    authenticated_async_context, fixture_user_id
):
    """A form event that says 11 answers were typed but lists none (an older payload shape): the
    receipt warns ``answers_missing`` and still reads 11, never "0 of 11"."""
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        payload = {"host": "boards.greenhouse.io", "fields_count": 11, "answers_count": 11, "answers": [], "by": "agent"}
        at = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        event_id = _sql(
            "INSERT INTO application_events (user_id, application_id, event_type, detail, payload, occurred_at, "
            "recorded_at, recorded_by) VALUES (?, ?, 'form_filled', '', ?, ?, ?, 'agent') RETURNING id",
            (fixture_user_id, app_id, json.dumps(payload), at, at),
        )[0][0]
        made = await _receipt(client, app_id)
        full = await _read(client, made["receipt_id"])
    assert [w for w in made["warnings"] if w.startswith("answers_missing")] == [
        f"answers_missing: form_filled#{event_id} says 11 answers were typed but lists none; "
        "the receipt keeps the count only"
    ]
    assert made["answers_from"] == f"form_filled#{event_id}"
    assert full["answers"] == []
    assert full["counts_line"] == f"11 of 11 fields {_DOT} 0 skipped {_DOT} 0 guessed"


@pytest.mark.asyncio
async def test_a_sensitive_answer_not_remembered_is_hashed_in_the_event_and_the_receipt(authenticated_async_context):
    """Visa and salary said for THIS form only: the event and the receipt keep the question and
    the sha256, never the value. A remembered sensitive answer (the user's own save) stays as typed."""
    gender = {"question": "Gender", "answer": "Female", "source": "memory", "key": "equality.gender"}
    async with authenticated_async_context() as client:
        resp = await client.patch("/api/profile", json={"edits": [{"path": "user_info.equality", "value": {"gender": "Female"}}]})
        assert resp.status_code == 200, resp.text
        app_id = await _bring(client)
        fill = await _fill(client, app_id, [WHY, VISA, PAY, gender])
        frozen = await _receipt(client, app_id)
        given_app = await _bring(client, {**_AD, "title": "Data Engineer (York)", "apply_url": "https://northwind.example/careers/10"})
        given = await _receipt(client, given_app, answers=[VISA, WHY])
        full = await _read(client, frozen["receipt_id"])
    event_payload = _sql("SELECT payload FROM application_events WHERE id = ?", (fill,))[0][0]
    rows = [r[0] for r in _sql(
        "SELECT answers FROM application_receipts WHERE id IN (?, ?)", (frozen["receipt_id"], given["receipt_id"])
    )]
    for raw in (event_payload, *rows):
        assert "Skilled Worker 7731" not in raw and "61,500" not in raw
    sha = {a["question"]: hashlib.sha256(a["answer"].encode("utf-8")).hexdigest() for a in (VISA, PAY)}
    for stored in (json.loads(event_payload)["answers"], json.loads(rows[0])):
        by_q = {a["question"]: a for a in stored}
        for q in (VISA["question"], PAY["question"]):
            assert by_q[q] == {"question": q, "source": "profile", "value_sha256": sha[q], "redacted": True}
        assert by_q["Why this role?"]["answer"] == "I like the product."
        assert by_q["Gender"]["answer"] == "Female", "remembered with the user's OK: kept as typed"
    assert [a["redacted"] for a in json.loads(rows[1]) if a["question"] == VISA["question"]] == [True]
    shown = {a["question"]: a for a in full["answers"]}
    assert (shown[VISA["question"]]["answer"], shown[VISA["question"]]["redacted"]) == ("", True)
    assert shown[VISA["question"]]["value_sha256"] == sha[VISA["question"]]
    assert full["counts_line"] == f"4 of 4 fields {_DOT} 0 skipped {_DOT} 0 guessed"


@pytest.mark.asyncio
async def test_a_request_id_replay_returns_the_first_receipt(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        fill = await _fill(client, app_id, [WHY])
        first = await _receipt(client, app_id, request_id="submit-11")
        await _fill(client, app_id, [NOTICE])  # a later fill must not change the replay
        again = await _receipt(client, app_id, request_id="submit-11")
    assert first["already_existed"] is False and again["already_existed"] is True
    assert again["receipt_id"] == first["receipt_id"]
    assert again["answers_from"] == first["answers_from"] == f"form_filled#{fill}"
    assert _sql("SELECT COUNT(*) FROM application_receipts WHERE application_id = ?", (app_id,))[0][0] == 1
