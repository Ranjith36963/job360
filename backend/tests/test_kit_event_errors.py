"""Agent ergonomics (2026-10-14): one answer shape, and every refusal at once.

An agent over MCP needed four calls to get ``form_filled`` right (one error per
call) and met two answer shapes (``record_event`` wanted ``source``, a receipt
refused it). These tests pin: one shared source list, round-trip of ``source`` /
``key`` on receipts, and ALL problems + one valid example in one 422.
"""
from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from src.services.applications import blocked as blocked_service
from src.services.applications import event_examples, kit, proof
from src.services.applications.spine import SpineError

_AD = {
    "title": "Data Engineer", "company": "Contoso", "location": "Manchester",
    "apply_url": "https://contoso.example/jobs/12", "description": "Pipelines, Airflow, dbt, Postgres.",
}


async def _bring(client: AsyncClient) -> int:
    resp = await client.post("/api/jobs/bring", json=_AD)
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _receipt(client: AsyncClient, app_id: int, answers: list[dict[str, Any]]):
    return await client.post(f"/api/applications/{app_id}/receipt", json={"channel": "email", "answers": answers})


@pytest.mark.asyncio
async def test_receipt_accepts_and_round_trips_answer_source(authenticated_async_context):
    answers = [
        {"question": "Right to work?", "answer": "Yes", "source": "profile", "key": "work_authorization"},
        {"question": "Why us?", "answer": "The data team.", "source": "written"},
        {"question": "Notice period?", "answer": "Four weeks."},  # the old shape still works
    ]
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        resp = await _receipt(client, app_id, answers)
        assert resp.status_code == 201, resp.text
        detail = await client.get(f"/api/applications/{app_id}")
    assert detail.json()["receipts"][0]["answers"] == answers


@pytest.mark.asyncio
async def test_receipt_rejects_unknown_answer_source(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        resp = await _receipt(client, app_id, [{"question": "Q", "answer": "A", "source": "vibes"}])
    assert resp.status_code == 422
    assert "memory, profile, approved, written, guessed" in resp.text


@pytest.mark.asyncio
async def test_form_filled_and_receipt_share_answer_sources(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        for source in kit.ANSWER_SOURCES:
            answer = {"question": f"Q {source}", "answer": "A", "source": source}
            assert (await _receipt(client, app_id, [answer])).status_code == 201, source
            event = await client.post(
                f"/api/applications/{app_id}/events",
                json={"event_type": "form_filled", "payload": {
                    "form_url": "https://jobs.example.com/apply", "fields_count": 1, "answers": [answer]}},
            )
            assert event.status_code == 201, (source, event.text)
        bad = {"question": "Q", "answer": "A", "source": "nope"}
        assert (await _receipt(client, app_id, [bad])).status_code == 422
        refused = await client.post(
            f"/api/applications/{app_id}/events",
            json={"event_type": "form_filled", "payload": {
                "form_url": "https://jobs.example.com/apply", "fields_count": 1, "answers": [bad]}},
        )
        assert refused.status_code == 422


@pytest.mark.asyncio
async def test_form_filled_reports_all_errors_at_once(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        resp = await client.post(
            f"/api/applications/{app_id}/events",
            json={"event_type": "form_filled", "payload": {
                "answers": [{"question": "Q", "answer": "A", "source": "vibes"}, {"question": "only a question"}],
            }},
        )
    assert resp.status_code == 422
    text = resp.json()["detail"]
    assert "fields_count" in text and "form_url" in text  # both missing keys, one message
    assert "answers[0]: answer source must be one of" in text
    assert "answers[1]: each answer needs question, answer and source" in text
    assert "Valid example payload:" in text


@pytest.mark.asyncio
async def test_kit_event_error_includes_valid_example(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        for event_type in ("form_swept", "hold_released", "site_account", "autofill_set", "submit_declined",
                           "cv_seen", "blocked", "unblocked", "proof_text"):
            resp = await client.post(
                f"/api/applications/{app_id}/events", json={"event_type": event_type, "payload": {"bogus": 1}}
            )
            assert resp.status_code == 422, (event_type, resp.text)
            assert "Valid example payload:" in resp.json()["detail"], event_type


def test_every_example_payload_is_valid():
    """An example the validator refuses is not an example."""
    for event_type, example in event_examples.EVENT_EXAMPLES.items():
        if event_type in ("blocked", "unblocked"):
            try:
                blocked_service.check_payload(_U_PROOF, event_type, example, "")
            except SpineError as exc:  # pragma: no cover
                raise AssertionError(f"{event_type}: {exc.detail}") from None
        elif event_type == "proof_text":
            proof.check_proof_text(_U_PROOF, example)
        else:
            assert kit.payload_problems(event_type, example) == [], event_type


class _UProof:
    id = "u"
    auth_via = "session"
    client_name = ""


_U_PROOF: Any = _UProof()
