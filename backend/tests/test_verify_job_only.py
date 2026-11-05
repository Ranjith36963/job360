"""Verify / Edit on the Ready card approves ONE answer for THIS job only (owner decision 2026-11-02, "A").

Reuse across jobs happens only through the "remember" press (services/profile/ask_once.py). Real HTTP
doors, real rows, VALUE asserts (rule #21).
"""
from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

VISA = {"key": "needs_visa", "question": "Will you need visa sponsorship to work in this role?"}  # one country
FORM = "https://careers.example/apply/1"


def _ad(company: str, country: str) -> dict[str, Any]:
    return {"title": "Data Engineer", "company": company, "location": "", "country": country,
            "apply_url": f"https://{company.lower()}.example/careers/1", "description": "Build the pipelines."}


async def _bring(client: AsyncClient, company: str, country: str = "GB") -> int:
    resp = await client.post("/api/jobs/bring", json=_ad(company, country))
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _fill_guess(client: AsyncClient, app_id: int, answer: str = "No") -> None:
    payload = {"form_url": FORM, "fields_count": 1,
               "answers": [{"question": VISA["question"], "answer": answer, "source": "guessed", "key": VISA["key"]}]}
    resp = await client.post(f"/api/applications/{app_id}/events", json={"event_type": "form_filled", "payload": payload})
    assert resp.status_code == 201, resp.text


async def _card(client: AsyncClient, app_id: int) -> dict[str, Any]:
    resp = await client.get("/api/ready-to-send", params={"application_id": app_id})
    assert resp.status_code == 200, resp.text
    return resp.json()["items"][0]


def _codes(card: dict[str, Any]) -> list[str]:
    return [f["code"] for f in card["flags"] if f["code"] != "missing"]


async def _verify(client: AsyncClient, app_id: int, answer: str | None = None) -> dict[str, Any]:
    fill = (await _card(client, app_id))["form_filled_event_id"]
    body: dict[str, Any] = {"form_filled_event_id": fill, **({} if answer is None else {"answer": answer})}
    resp = await client.post(f"/api/applications/{app_id}/answers/0/confirm", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _gate_guessed(user_id: str, app_id: int) -> bool:
    from src.api import dependencies as api_deps
    from src.services.applications import kit, spine

    db = await api_deps.get_db()
    row = await spine.get_owned_application(db, user_id, app_id)
    return (await kit.gate_facts(db, user_id, row, email_verified=True)).guessed_unverified


async def _missing(client: AsyncClient, app_id: int) -> list[str]:
    resp = await client.post(f"/api/applications/{app_id}/kit/form-check",
                             json={"questions": [{"question": VISA["question"]}]})
    assert resp.status_code == 200, resp.text
    return [m["question"] for m in resp.json()["missing"]]


def _approved(user_id: str) -> list[dict[str, Any]]:
    from src.services.profile.storage import load_profile

    profile = load_profile(user_id)
    return [] if profile is None else [r for r in profile.user_info.answers or [] if r.get("approved") is True]


@pytest.mark.asyncio
async def test_verify_clears_this_jobs_guess_but_the_same_question_on_another_job_is_still_a_guess(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        job_a, job_b = await _bring(client, "Acme"), await _bring(client, "Initech")
        await _fill_guess(client, job_a)
        assert await _gate_guessed(fixture_user_id, job_a) is True and "guessed" in _codes(await _card(client, job_a))

        after = await _verify(client, job_a)
        assert after["answers"][0]["verified"] is True and "guessed" not in _codes(after)
        assert await _gate_guessed(fixture_user_id, job_a) is False, "Verify clears A's answer_guessed"
        await _fill_guess(client, job_a)  # the assistant fills A again: still this job's approved word
        assert await _gate_guessed(fixture_user_id, job_a) is False
        assert _approved(fixture_user_id) == [], "nothing is saved for reuse"

        # Job B: same question, same country - the Verify on A is not B's.
        await _fill_guess(client, job_b)
        card_b = await _card(client, job_b)
        assert card_b["answers"][0]["verified"] is False and "guessed" in _codes(card_b)
        assert await _gate_guessed(fixture_user_id, job_b) is True
        assert await _missing(client, job_b) == [VISA["question"]], "kit / answer_match do not fill B from A's Verify"


@pytest.mark.asyncio
async def test_edit_is_this_jobs_word_only(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        job_a, job_b = await _bring(client, "Acme"), await _bring(client, "Initech")
        await _fill_guess(client, job_a)
        edited = await _verify(client, job_a, "Yes - I hold a work visa")
        assert (edited["answers"][0]["changed_to"], edited["answers"][0]["verified"]) == ("Yes - I hold a work visa", True)
        assert await _gate_guessed(fixture_user_id, job_a) is False
        await _fill_guess(client, job_b)
        card_b = await _card(client, job_b)
        assert card_b["answers"][0]["changed_to"] is None and "guessed" in _codes(card_b)
        assert _approved(fixture_user_id) == []


@pytest.mark.asyncio
async def test_remember_is_still_what_makes_the_next_job_fill_itself(authenticated_async_context, fixture_user_id):
    """Memory first (owner 2026-11-05, plan S2): the "remember" press saves no sponsorship answer: the user sets
    it on the GB page in Job360, so the next job still waits on Memory."""
    async with authenticated_async_context() as client:
        job_a, job_b = await _bring(client, "Acme"), await _bring(client, "Initech")
        qs = [{**VISA, "answer": "No", "source": "guess"}]
        resp = await client.post("/api/asks", json={"application_id": job_a, "question": "Please check these",
                                                    "kind": "question", "questions": qs})
        assert resp.status_code == 201, resp.text
        await _fill_guess(client, job_a)
        await _verify(client, job_a)
        assert await _missing(client, job_b) == [VISA["question"]], "Verify alone reuses nothing"
        resp = await client.post(f"/api/asks/{resp.json()['id']}/answers/remember", json={"answers": {VISA["key"]: "No"}})
        assert resp.status_code == 200 and resp.json()["saved"] == [], resp.text
        assert "your GB page" in resp.json()["not_saved"][VISA["key"]]
        assert _approved(fixture_user_id) == []
        assert await _missing(client, job_b) == [VISA["question"]], "Memory, never a saved answer"


CERTIFY = {"key": "certify.truth", "question": "I certify that everything in this application is true"}


@pytest.mark.asyncio
async def test_verify_clears_a_certify_row_on_this_job_though_it_is_never_reused(
    authenticated_async_context, fixture_user_id
):
    """consent.* / certify.* answers are never REUSED from memory, but the user's own Verify on THIS job is
    their word for it: the row is verified and the gate's answer_guessed clears (independent check, #1470)."""
    async with authenticated_async_context() as client:
        app_id = await _bring(client, "Acme")
        payload = {"form_url": FORM, "fields_count": 1,
                   "answers": [{**CERTIFY, "answer": "Yes", "source": "guessed"}]}
        resp = await client.post(f"/api/applications/{app_id}/events", json={"event_type": "form_filled", "payload": payload})
        assert resp.status_code == 201, resp.text
        assert await _gate_guessed(fixture_user_id, app_id) is True
        after = await _verify(client, app_id)
        assert after["answers"][0]["verified"] is True and "guessed" not in _codes(after)
        assert await _gate_guessed(fixture_user_id, app_id) is False
        assert _approved(fixture_user_id) == [], "nothing is saved for reuse"


@pytest.mark.asyncio
async def test_this_jobs_verify_wins_over_a_saved_answer_that_says_otherwise(
    authenticated_async_context, fixture_user_id
):
    """A remembered "Yes" and the user's Verify of "No" on this job are not two answers that disagree: the
    job's own Verify is the user's word for this job, so the row verifies with "No"."""
    async with authenticated_async_context() as client:
        saved = {"path": "user_info.answers", "value": [{"question": VISA["question"], "answer": "Yes", "approved": True}]}
        resp = await client.patch("/api/profile", json={"edits": [saved]})
        assert resp.status_code == 200, resp.text
        app_id = await _bring(client, "Acme")
        await _fill_guess(client, app_id, answer="No")
        after = await _verify(client, app_id)
        assert after["answers"][0]["verified"] is True and "guessed" not in _codes(after)
        assert await _gate_guessed(fixture_user_id, app_id) is False


@pytest.mark.asyncio
async def test_a_verify_of_a_wordless_question_never_covers_another_wordless_one(
    authenticated_async_context, fixture_user_id
):
    """A punctuation-only question ("???", an unlabelled box) has no wording to match by: Verify of one never
    verifies a different wordless row the user never saw (independent check, #1470 c3)."""
    async with authenticated_async_context() as client:
        app_id = await _bring(client, "Acme")

        async def fill(question: str, answer: str) -> None:
            payload = {"form_url": FORM, "fields_count": 1,
                       "answers": [{"question": question, "answer": answer, "source": "guessed"}]}
            resp = await client.post(f"/api/applications/{app_id}/events",
                                     json={"event_type": "form_filled", "payload": payload})
            assert resp.status_code == 201, resp.text

        await fill("???", "Yes")
        assert (await _verify(client, app_id))["answers"][0]["verified"] is True
        await fill("!!!", "No")
        card = await _card(client, app_id)
        assert card["answers"][0]["verified"] is False and "guessed" in _codes(card)
        assert await _gate_guessed(fixture_user_id, app_id) is True
