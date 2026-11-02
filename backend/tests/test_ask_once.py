"""Ask once (owner answers 2026-11-02, questions 2 and 3).

Only the owner's "remember" press saves an answer for reuse; the saved answer is bound to the job's
country, so the next job in that country needs no owner and another country still asks. Real HTTP
doors and VALUE asserts (rule #21).
"""
from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

Q = {"key": "notice_weeks", "question": "What is your notice period?"}  # global: true in every country
VISA = {"key": "needs_visa", "question": "Will you need visa sponsorship to work in this role?"}  # one country


def _ad(company: str, country: str) -> dict[str, Any]:
    return {"title": "Data Engineer", "company": company, "location": "", "country": country,
            "apply_url": f"https://{company.lower()}.example/careers/1", "description": "Build the pipelines."}


async def _bring(client: AsyncClient, company: str, country: str) -> int:
    resp = await client.post("/api/jobs/bring", json=_ad(company, country))
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _ask(client: AsyncClient, app_id: int, *rows: dict[str, str]) -> int:
    qs = [{**r, "answer": "1 month", "source": "guess"} for r in rows]
    resp = await client.post("/api/asks", json={"application_id": app_id, "question": "Please check these",
                                                "kind": "question", "questions": qs})
    assert resp.status_code == 201, resp.text
    return int(resp.json()["id"])


async def _form_check_missing(client: AsyncClient, app_id: int, q: dict[str, str] = Q) -> list[str]:
    resp = await client.post(f"/api/applications/{app_id}/kit/form-check",
                             json={"questions": [{"question": q["question"]}]})
    assert resp.status_code == 200, resp.text
    return [m["question"] for m in resp.json()["missing"]]


async def _gate_guessed(user_id: str, app_id: int) -> bool:
    from src.api import dependencies as api_deps
    from src.services.applications import kit, spine

    db = await api_deps.get_db()
    row = await spine.get_owned_application(db, user_id, app_id)
    return (await kit.gate_facts(db, user_id, row, email_verified=True)).guessed_unverified


async def _fill_guess(client: AsyncClient, app_id: int, answer: str, q: dict[str, str] = Q) -> None:
    payload = {"form_url": "https://careers.example/apply/1", "fields_count": 1,
               "answers": [{"question": q["question"], "answer": answer, "source": "guessed", "key": q["key"]}]}
    resp = await client.post(f"/api/applications/{app_id}/events", json={"event_type": "form_filled", "payload": payload})
    assert resp.status_code == 201, resp.text


def _approved(user_id: str) -> list[dict[str, Any]]:
    from src.services.profile.storage import load_profile

    profile = load_profile(user_id)
    return [] if profile is None else [r for r in profile.user_info.answers or [] if r.get("approved") is True]


@pytest.mark.asyncio
async def test_a_right_to_work_answer_remembered_once_fills_that_country_only(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        first = await _bring(client, "Acme", "GB")
        ask_id = await _ask(client, first, VISA)
        # Answering alone remembers nothing (owner answer 2: only the press saves for reuse).
        resp = await client.post(f"/api/asks/{ask_id}/answer", json={"answers": {VISA["key"]: "No"}})
        assert resp.status_code == 200, resp.text
        assert _approved(fixture_user_id) == []
        resp = await client.post(f"/api/asks/{ask_id}/answers/remember", json={"answers": {VISA["key"]: "No"}})
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"saved": [VISA["key"]], "skipped": [], "country": "GB"}
        assert [(r["topic"], r["answer"], r.get("country")) for r in _approved(fixture_user_id)] == [
            (VISA["key"], "No", "GB")]

        same = await _bring(client, "Globex", "GB")
        assert await _form_check_missing(client, same, VISA) == [], "the saved answer fills the next UK form"
        await _fill_guess(client, same, "No", VISA)
        assert await _gate_guessed(fixture_user_id, same) is False, "no answer_guessed: the owner's word"

        other = await _bring(client, "Initech", "US")
        assert await _form_check_missing(client, other, VISA) == [VISA["question"]], "a US form still asks"
        await _fill_guess(client, other, "No", VISA)
        assert await _gate_guessed(fixture_user_id, other) is True, "a UK answer never crosses to the US"

        # The US answer is its own record: one per question AND country.
        us_ask = await _ask(client, other, VISA)
        resp = await client.post(f"/api/asks/{us_ask}/answers/remember", json={"answers": {VISA["key"]: "Yes"}})
        assert resp.status_code == 200 and resp.json()["country"] == "US", resp.text
        assert {r["country"]: r["answer"] for r in _approved(fixture_user_id)} == {"GB": "No", "US": "Yes"}


@pytest.mark.asyncio
async def test_a_global_answer_remembered_once_fills_every_country(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        first = await _bring(client, "Acme", "GB")
        ask_id = await _ask(client, first, Q)
        resp = await client.post(f"/api/asks/{ask_id}/answers/remember", json={"answers": {Q["key"]: "4 weeks"}})
        assert resp.status_code == 200 and resp.json()["saved"] == [Q["key"]], resp.text
        assert [(r["topic"], r["answer"], r.get("country")) for r in _approved(fixture_user_id)] == [
            (Q["key"], "4 weeks", None)], "a notice period is the same in every country: saved global"
        other = await _bring(client, "Initech", "US")
        assert await _form_check_missing(client, other) == [], "the US form is filled without asking"
        await _fill_guess(client, other, "4 weeks")
        assert await _gate_guessed(fixture_user_id, other) is False


@pytest.mark.asyncio
async def test_remember_skips_per_job_rows_and_an_assistant_cannot_press_it(
    authenticated_async_context, fixture_user_id
):
    from src.api.main import app

    async with authenticated_async_context() as client:
        app_id = await _bring(client, "Acme", "GB")
        heard = {"key": "how_heard", "question": "How did you hear about us?"}
        ask_id = await _ask(client, app_id, Q, heard)
        resp = await client.post(f"/api/asks/{ask_id}/answers/remember",
                                 json={"answers": {Q["key"]: "4 weeks", "how_heard": "LinkedIn"}})
        assert resp.status_code == 200, resp.text
        assert resp.json()["saved"] == [Q["key"]] and resp.json()["skipped"] == ["how_heard"]
        token = (await client.post("/api/tokens", json={"name": "agent"})).json()["token"]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                           headers={"Authorization": f"Bearer {token}"}) as agent:
        refused = await agent.post(f"/api/asks/{ask_id}/answers/remember", json={"answers": {Q["key"]: "1 week"}})
        assert refused.status_code == 403, refused.text
        refused = await agent.post("/api/profile/questionnaire/remember", json={
            "country": "GB", "items": [{"topic": "x", "question": "X?", "answer": "Y"}]})
        assert refused.status_code == 403, refused.text
    assert [r["answer"] for r in _approved(fixture_user_id)] == ["4 weeks"]


@pytest.mark.asyncio
async def test_questionnaire_lists_up_to_ten_per_country_and_a_remembered_row_leaves_it(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        saved = await client.patch("/api/profile", json={"edits": [
            {"path": "user_info.right_to_work", "value": {"countries": [
                {"country": "GB", "work_authorization": "citizen"}, {"country": "US", "work_authorization": "visa"}]}},
            {"path": "user_info.answers", "op": "upsert", "value": {
                "question": "Personal website or portfolio", "answer": "ada.dev", "approved": False,
                "topic": "contact.website"}},
        ]})
        assert saved.status_code == 200, saved.text
        out = (await client.get("/api/profile/questionnaire")).json()["countries"]
        assert [c["country"] for c in out] == ["GB", "US"]
        gb = out[0]["questions"]
        assert 0 < len(gb) <= 10 and not any(q["per_job"] or q["verified"] for q in gb)
        assert gb[0]["label"] == "guessed" and gb[0]["topic"] == "contact.website", "the guessed row comes first"
        row = next(q for q in gb if q["topic"] == "contact.phone")
        resp = await client.post("/api/profile/questionnaire/remember", json={"country": "GB", "items": [
            {"topic": row["topic"], "question": row["question"], "answer": "+44 7700 900123"}]})
        assert resp.status_code == 200 and resp.json() == {"saved": ["contact.phone"]}, resp.text
        after = {c["country"]: [q["topic"] for q in c["questions"]]
                 for c in (await client.get("/api/profile/questionnaire")).json()["countries"]}
        assert "contact.phone" not in after["GB"] and "contact.phone" not in after["US"], "global: off every list"
        # A country-specific row leaves only its own country's list.
        rtw = "right_to_work.GB.needs_sponsorship"
        assert rtw in after["GB"] and "right_to_work.US.needs_sponsorship" in after["US"]
        question = next(q["question"] for q in gb if q["topic"] == rtw)
        resp = await client.post("/api/profile/questionnaire/remember", json={"country": "GB", "items": [
            {"topic": rtw, "question": question, "answer": "No"}]})
        assert resp.status_code == 200, resp.text
        after = {c["country"]: [q["topic"] for q in c["questions"]]
                 for c in (await client.get("/api/profile/questionnaire")).json()["countries"]}
        assert rtw not in after["GB"] and "right_to_work.US.needs_sponsorship" in after["US"]
        saved = {r["topic"]: r.get("country") for r in _approved(fixture_user_id)}
        assert saved == {"contact.phone": None, rtw: "GB"}


def test_a_country_bound_answer_matches_only_that_country():
    from src.services.profile.answer_match import find_answer

    saved = [{"question": "Notice?", "answer": "4 weeks", "approved": True, "topic": "notice", "country": "GB"}]
    assert find_answer(saved, topic="notice", country="GB") is saved[0]
    assert find_answer(saved, topic="notice", country="gb") is saved[0]
    assert find_answer(saved, topic="notice", country="US") is None
    assert find_answer(saved, topic="notice", country=None) is None


def test_only_country_specific_questions_are_bound_to_a_country():
    from src.services.profile.ask_once import is_country_specific

    bound = [("salary.GB", "Expected salary in GB"), ("right_to_work.GB.needs_sponsorship", "Sponsorship?"),
             ("logistics.GB.willing_to_relocate", "Willing to relocate within GB?"),
             ("q_18", "Are you authorized to work in the US?"), ("q_19", "Do you have the right to work in the UK?"),
             ("q_20", "Will you work on site three days a week?"), ("min_pay", "Minimum salary you would accept")]
    free = [("contact.phone", "Phone number"), ("logistics.notice_period", "Notice period"),
            ("contact.linkedin_url", "LinkedIn profile"), ("contact.email", "Email")]
    assert [k for k, q in bound if not is_country_specific(k, q)] == []
    assert [k for k, q in free if is_country_specific(k, q)] == []
