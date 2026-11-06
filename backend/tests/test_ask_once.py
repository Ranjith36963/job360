"""Ask once (owner answers 2026-11-02, questions 2 and 3).

Only the owner's "remember" press saves an answer for reuse. Memory first (owner 2026-11-05, plan S2): a
question in Memory's families (right to work, sponsorship, notice, contact...) is never saved: the user sets
it on the country page in Job360. Real HTTP doors and VALUE asserts (rule #21).
"""
from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

Q = {"key": "take_home", "question": "Are you happy to do a take-home exercise?"}  # outside Memory: saved
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


def _memory(user_id: str) -> tuple[Any, Any]:
    from src.services.profile.storage import load_profile

    profile = load_profile(user_id)
    return (None, None) if profile is None else (profile.user_info.answers, profile.user_info.right_to_work)


@pytest.mark.asyncio
async def test_a_memory_row_is_never_remembered_as_a_saved_answer(authenticated_async_context, fixture_user_id):
    """Plan S2 (owner 2026-11-05): a Memory row is never a saved answer. Answer once (owner 2026-11-06): a clean
    GB sponsorship "No" goes into GB's Memory; a PL right-to-work "Yes" (no status) saves nothing and names the
    page; an assistant's press is refused and writes nothing."""
    # owner 2026-11-06: answer once writes Memory (refines S2)
    from src.api.main import app

    rtw = {"key": "pl_rtw", "question": "Do you have the right to work in Poland?"}
    async with authenticated_async_context() as client:
        before = _memory(fixture_user_id)
        gb_app = await _bring(client, "AcmeGB", "GB")
        gb_ask = await _ask(client, gb_app, VISA)
        token = (await client.post("/api/tokens", json={"name": "agent"})).json()["token"]
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                               headers={"Authorization": f"Bearer {token}"}) as agent:
            refused = await agent.post(f"/api/asks/{gb_ask}/answers/remember", json={"answers": {VISA["key"]: "No"}})
            assert refused.status_code == 403, refused.text
        assert _memory(fixture_user_id) == before, "an assistant's press writes nothing"
        resp = await client.post(f"/api/asks/{gb_ask}/answers/remember", json={"answers": {VISA["key"]: "No"}})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert (body["saved"], body["skipped"], body["country"], body["not_saved"]) == ([], [], "GB", {})
        assert body["memory"] == {VISA["key"]: {"path": "user_info.right_to_work",
                                                "key": "right_to_work.GB.needs_sponsorship"}}
        answers, right_to_work = _memory(fixture_user_id)
        assert right_to_work["countries"] == [{"country": "GB", "needs_sponsorship": False}]
        assert _approved(fixture_user_id) == [] and not answers, "never a saved answer"
        assert await _form_check_missing(client, gb_app, VISA) == [], "GB's Memory now answers it"
        after_gb = _memory(fixture_user_id)
        for country, row, answer in (("PL", rtw, "Yes"),):
            app_id = await _bring(client, "Acme" + country, country)
            ask_id = await _ask(client, app_id, row)
            resp = await client.post(f"/api/asks/{ask_id}/answers/remember", json={"answers": {row["key"]: answer}})
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert (body["saved"], body["skipped"], body["country"]) == ([], [row["key"]], country)
            assert f"your {country} page" in body["not_saved"][row["key"]]
            assert "user_info.right_to_work" in body["not_saved"][row["key"]]
            assert _memory(fixture_user_id) == after_gb, "no saved answer, no Memory write"
            assert await _form_check_missing(client, app_id, row) == [row["question"]], "Memory still asks"


@pytest.mark.asyncio
async def test_an_unclear_answer_to_a_memory_row_saves_nothing_and_says_so(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id = await _bring(client, "Acme", "GB")
        ask_id = await _ask(client, app_id, VISA)
        resp = await client.post(f"/api/asks/{ask_id}/answers/remember", json={"answers": {VISA["key"]: "depends"}})
        assert resp.status_code == 200, resp.text
        assert resp.json()["saved"] == [] and "your GB page" in resp.json()["not_saved"][VISA["key"]]
    assert _approved(fixture_user_id) == []


@pytest.mark.asyncio
async def test_a_global_answer_remembered_once_fills_every_country(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        first = await _bring(client, "Acme", "GB")
        ask_id = await _ask(client, first, Q)
        resp = await client.post(f"/api/asks/{ask_id}/answers/remember", json={"answers": {Q["key"]: "Yes"}})
        assert resp.status_code == 200 and resp.json()["saved"] == [Q["key"]], resp.text
        assert [(r["topic"], r["answer"], r.get("country")) for r in _approved(fixture_user_id)] == [
            (Q["key"], "Yes", None)], "an answer outside Memory is the same in every country: saved global"
        other = await _bring(client, "Initech", "US")
        assert await _form_check_missing(client, other) == [], "the US form is filled without asking"
        await _fill_guess(client, other, "Yes")
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
                                 json={"answers": {Q["key"]: "Yes", "how_heard": "LinkedIn"}})
        assert resp.status_code == 200, resp.text
        assert resp.json()["saved"] == [Q["key"]] and resp.json()["skipped"] == ["how_heard"]
        token = (await client.post("/api/tokens", json={"name": "agent"})).json()["token"]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                           headers={"Authorization": f"Bearer {token}"}) as agent:
        refused = await agent.post(f"/api/asks/{ask_id}/answers/remember", json={"answers": {Q["key"]: "No"}})
        assert refused.status_code == 403, refused.text
        refused = await agent.post("/api/profile/questionnaire/remember", json={
            "country": "GB", "items": [{"topic": "x", "question": "X?", "answer": "Y"}]})
        assert refused.status_code == 403, refused.text
    assert [r["answer"] for r in _approved(fixture_user_id)] == ["Yes"]


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
        row = next(q for q in gb if q["topic"] == "contact.website")
        resp = await client.post("/api/profile/questionnaire/remember", json={"country": "GB", "items": [
            {"topic": row["topic"], "question": row["question"], "answer": "ada.dev"}]})
        assert resp.status_code == 200 and resp.json() == {"saved": ["contact.website"], "not_saved": {}}, resp.text
        after = {c["country"]: [q["topic"] for q in c["questions"]]
                 for c in (await client.get("/api/profile/questionnaire")).json()["countries"]}
        assert "contact.website" not in after["GB"] and "contact.website" not in after["US"], "global: off every list"
        # Memory first (plan S2): a Memory row (phone, sponsorship) is never a saved answer.
        # owner 2026-11-06: answer once writes Memory (refines S2)
        rtw = "right_to_work.GB.needs_sponsorship"
        assert rtw in after["GB"] and "right_to_work.US.needs_sponsorship" in after["US"]
        question = next(q["question"] for q in gb if q["topic"] == rtw)
        phone = next(q["question"] for q in gb if q["topic"] == "contact.phone")
        resp = await client.post("/api/profile/questionnaire/remember", json={"country": "GB", "items": [
            {"topic": rtw, "question": question, "answer": "No"},
            {"topic": "contact.phone", "question": phone, "answer": "+44 7700 900123"}]})
        assert resp.status_code == 200 and resp.json()["saved"] == [], resp.text
        assert resp.json()["not_saved"] == {} and resp.json()["memory"] == {
            rtw: {"path": "user_info.right_to_work", "key": rtw},
            "contact.phone": {"path": "user_info.contact", "key": "contact.phone"}}
        from src.services.profile.storage import load_profile

        info = load_profile(fixture_user_id).user_info
        assert info.right_to_work["countries"] == [
            {"country": "GB", "work_authorization": "citizen", "needs_sponsorship": False},
            {"country": "US", "work_authorization": "visa"}], "GB's page only; US untouched"
        assert info.contact["phone"] == "+44 7700 900123"
        after = {c["country"]: [q["topic"] for q in c["questions"]]
                 for c in (await client.get("/api/profile/questionnaire")).json()["countries"]}
        assert rtw not in after["GB"] and "contact.phone" not in after["GB"], "Memory answers them now"
        assert "right_to_work.US.needs_sponsorship" in after["US"], "a GB answer never fills the US page"
        saved = {r["topic"]: r.get("country") for r in _approved(fixture_user_id)}
        assert saved == {"contact.website": None}


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


def test_an_answer_the_owner_bound_to_this_country_outranks_one_bound_to_none():
    from src.services.profile.answer_match import resolve_answer

    legacy = {"question": "Expected salary?", "answer": "60000", "approved": True, "topic": "salary.pay"}
    pressed = {**legacy, "answer": "70000", "country": "GB"}
    assert resolve_answer([legacy, pressed], topic="salary.pay", country="GB") == (pressed, None)
    assert resolve_answer([legacy, pressed], topic="salary.pay", country="US") == (legacy, None)
    two = [legacy, {**legacy, "answer": "65000"}]  # still never a guess between two at one level
    assert resolve_answer(two, topic="salary.pay", country="US")[0] is None


@pytest.mark.asyncio
async def test_the_answer_once_card_counts_once_and_the_newer_press_wins(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        saved = await client.patch("/api/profile", json={"edits": [
            {"path": "user_info.right_to_work", "value": {"countries": [
                {"country": "GB", "work_authorization": "citizen"}, {"country": "US", "work_authorization": "visa"}]}},
            {"path": "user_info.answers", "value": [{"question": "Phone number", "answer": "+44 7700 900123",
                                                     "approved": True, "topic": "contact.phone"}]},
        ]})
        assert saved.status_code == 200, saved.text
        topics = {c["country"]: [q["topic"] for q in c["questions"]]
                  for c in (await client.get("/api/profile/questionnaire")).json()["countries"]}
        assert "contact.phone" not in topics["GB"] and "contact.phone" not in topics["US"]
        site = "contact.website"
        count = (await client.get("/api/needs-you-count")).json()
        rows = sum(len(t) for t in topics.values())
        assert (count["answer_once"], count["n"]) == (rows, 1), "the questionnaire is one card on the count"
        question = "Personal website or portfolio"
        for answer in ("old.dev", "ada.dev"):  # pressed twice: the newer press replaces the older, never a tie
            resp = await client.post("/api/profile/questionnaire/remember", json={"country": "GB", "items": [
                {"topic": site, "question": question, "answer": answer}]})
            assert resp.status_code == 200, resp.text
        from src.services.profile.answer_match import find_answer
        from src.services.profile.storage import load_profile

        answers = load_profile(fixture_user_id).user_info.answers
        assert find_answer(answers, topic=site, country="GB")["answer"] == "ada.dev"
        gb = [q["topic"] for q in (await client.get("/api/profile/questionnaire")).json()["countries"][0]["questions"]]
        assert site not in gb
