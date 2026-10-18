"""Verify / Edit one answer on a Ready-to-send card (owner decision 2026-10-18).

Real HTTP doors, real rows, VALUE asserts (rule #21): the user's word lands in
memory, the card reads it back, and an edited answer holds the Send.
"""
from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

FORM = "https://careers.example/apply/9"
_AD = {
    "title": "Data Engineer", "company": "Certa", "location": "Remote", "country": "IN", "remote": True,
    "apply_url": "https://certa.example/careers/9", "description": "Build the pipelines.",
}
NAME = {"question": "Name", "answer": "Ada Lovelace", "source": "memory", "key": "contact.legal_last_name"}
GUESS = {"question": "Job requirement to be located in India - do you meet it?", "answer": "Yes", "source": "guessed"}
GENDER = {"question": "Gender (voluntary)", "answer": "Female", "source": "memory", "key": "equality.gender"}
VISA = {"question": "Require sponsorship?", "answer": "No", "source": "memory", "key": "right_to_work.IN.needs_sponsorship"}


def _e(path: str, value: Any) -> dict[str, Any]:
    return {"path": path, "value": value}


async def _bring(client: AsyncClient) -> int:
    resp = await client.post("/api/jobs/bring", json=_AD)
    assert resp.status_code == 200, resp.text
    app_id = int(resp.json()["application_id"])
    cv = {"kind": "cv", "text": "Ada Lovelace\nData engineer."}
    assert (await client.post(f"/api/applications/{app_id}/artifacts", json=cv)).status_code == 201
    return app_id


async def _fill(client: AsyncClient, app_id: int, answers: list[dict[str, Any]]) -> None:
    payload = {"form_url": FORM, "fields_count": len(answers), "answers": answers}
    resp = await client.post(f"/api/applications/{app_id}/events", json={"event_type": "form_filled", "payload": payload})
    assert resp.status_code == 201, resp.text


async def _card(client: AsyncClient, app_id: int) -> dict[str, Any]:
    resp = await client.get("/api/ready-to-send", params={"application_id": app_id})
    assert resp.status_code == 200, resp.text
    return resp.json()["items"][0]


async def _confirm(client: AsyncClient, app_id: int, index: int, fill: int, answer: str | None = None):
    body: dict[str, Any] = {"form_filled_event_id": fill}
    if answer is not None:
        body["answer"] = answer
    return await client.post(f"/api/applications/{app_id}/answers/{index}/confirm", json=body)


def _approved(user_id: str) -> list[dict[str, Any]]:
    from src.services.profile.storage import load_profile

    profile = load_profile(user_id)  # None = nothing was ever written
    return [] if profile is None else [r for r in profile.user_info.answers or [] if r.get("approved") is True]


def _codes(card: dict[str, Any]) -> list[str]:
    """The answer flags only (the job-country "missing" facts are another test's business)."""
    return [f["code"] for f in card["flags"] if f["code"] != "missing"]


@pytest.mark.asyncio
async def test_verify_a_guess_saves_it_as_the_users_answer_and_the_next_fill_reads_verified(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        await _fill(client, app_id, [GUESS])
        card = await _card(client, app_id)
        assert _codes(card) == ["guessed"] and card["answers"][0]["verified"] is False
        resp = await _confirm(client, app_id, 0, card["form_filled_event_id"])
        assert resp.status_code == 200, resp.text
        after = resp.json()
        assert after["answers"][0]["verified"] is True and after["answers"][0]["changed_to"] is None
        assert _codes(after) == [], "a verified guess is no longer a guess"
        assert [(r["question"], r["answer"]) for r in _approved(fixture_user_id)] == [(GUESS["question"], "Yes")]
        await _fill(client, app_id, [GUESS])  # the assistant fills again: still the user's word
        assert (await _card(client, app_id))["answers"][0]["verified"] is True


@pytest.mark.asyncio
async def test_edit_a_guess_holds_send_until_the_assistant_types_the_users_word(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        await _fill(client, app_id, [GUESS])
        fill = (await _card(client, app_id))["form_filled_event_id"]
        resp = await _confirm(client, app_id, 0, fill, "No - I live in London, open to relocating")
        assert resp.status_code == 200, resp.text
        card = resp.json()
        assert card["answers"][0]["changed_to"] == "No - I live in London, open to relocating"
        assert _codes(card) == ["changed"] and card["answers"][0]["verified"] is False
        approve = await client.post(f"/api/applications/{app_id}/send/approve", params={"form_filled_event_id": fill})
        assert approve.status_code == 409 and "changed an answer" in approve.json()["detail"]
        assert [r["answer"] for r in _approved(fixture_user_id)] == ["No - I live in London, open to relocating"]
        await _fill(client, app_id, [{**GUESS, "answer": "No - I live in London, open to relocating", "source": "approved"}])
        card = await _card(client, app_id)
        assert card["answers"][0]["verified"] is True and _codes(card) == []
        ok = await client.post(
            f"/api/applications/{app_id}/send/approve", params={"form_filled_event_id": card["form_filled_event_id"]}
        )
        assert ok.status_code == 201, ok.text


@pytest.mark.asyncio
async def test_sensitive_memory_goes_to_its_field_never_the_approved_list(authenticated_async_context, fixture_user_id):
    from src.services.profile.storage import load_profile

    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        await _fill(client, app_id, [GENDER])
        card = await _card(client, app_id)
        assert _codes(card) == ["not_in_memory"]
        resp = await _confirm(client, app_id, 0, card["form_filled_event_id"])
        assert resp.status_code == 200, resp.text
        assert resp.json()["answers"][0]["verified"] is True and _codes(resp.json()) == []
        profile = load_profile(fixture_user_id)
        assert profile is not None and profile.user_info.equality["gender"] == "Female"
        assert _approved(fixture_user_id) == [], "a sensitive answer never rides in the approved list"
        resp = await _confirm(client, app_id, 0, card["form_filled_event_id"], "Prefer not to say")
        assert resp.json()["answers"][0]["changed_to"] == "Prefer not to say"
        profile = load_profile(fixture_user_id)
        assert profile is not None and profile.user_info.equality["gender"] == "Prefer not to say"


@pytest.mark.asyncio
async def test_verify_memory_an_assistant_saved_makes_it_the_users_own(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        token = (await client.post("/api/tokens", json={"name": "claude-code"})).json()["token"]
        app_id = await _bring(client)
    from src.api.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    ) as agent:
        resp = await agent.patch("/api/profile", json={"edits": [
            {**_e("user_info.right_to_work", {"countries": [{"country": "IN", "needs_sponsorship": False}]}),
             "remember": True},
            _e("user_info.contact", {"legal_last_name": "Lovelace"}),
        ]})
        assert resp.status_code == 200, resp.text
        await _fill(agent, app_id, [VISA, NAME])
        fill = (await _card(agent, app_id))["form_filled_event_id"]
        refused = await _confirm(agent, app_id, 0, fill)
        assert refused.status_code in (401, 403), "an assistant can never vouch for its own answer"
    async with authenticated_async_context() as client:
        card = await _card(client, app_id)
        assert [a["verified"] for a in card["answers"]] == [False, False]
        visa = (await _confirm(client, app_id, 0, fill)).json()["answers"][0]
        assert (visa["verified"], visa["saved_by"]) == (True, "web")
        assert _approved(fixture_user_id) == [], "visa status stays in memory only"
        # Name was typed from the wrong field: verifying it saves the exact wording the user stands behind.
        name = (await _confirm(client, app_id, 1, fill)).json()["answers"][1]
        assert name["verified"] is True
        assert [(r["question"], r["answer"]) for r in _approved(fixture_user_id)] == [("Name", "Ada Lovelace")]


@pytest.mark.asyncio
async def test_refusals_write_nothing(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        await _fill(client, app_id, [GUESS, {"question": "Middle name", "answer": "", "source": "written"}])
        fill = (await _card(client, app_id))["form_filled_event_id"]
        assert (await _confirm(client, app_id, 0, fill + 999)).status_code == 409
        assert (await _confirm(client, app_id, 7, fill)).status_code == 422
        assert (await _confirm(client, app_id, 1, fill)).status_code == 422, "nothing to verify on a blank"
        assert (await _confirm(client, app_id, 0, fill, "   ")).status_code == 422
        assert (await _confirm(client, 999999, 0, fill)).status_code == 404
        assert _approved(fixture_user_id) == []
