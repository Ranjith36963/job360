"""Answer once (owner 2026-11-06): a Memory question answered on Needs you is saved into that country's Memory.

The user's own "remember" press on a job's ask writes a Memory-family row (sponsorship, salary...) into Memory
for THAT job's country, in the field's real shape; a different value already in Memory is a conflict (nothing
written); an unclear answer and a per-job row are not saved; an assistant (bearer token, MCP answer_ask)
never writes Memory. Real HTTP doors and VALUE asserts (rule #21). Helpers are local to this module.
"""
from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

SPONSOR = {"key": "sponsorship.de.yes_no", "question": "Do you need sponsorship to work in Germany?"}
PAY = {"key": "salary.de", "question": "What are your salary expectations?"}


def _agent(token: str) -> AsyncClient:
    from src.api.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"})


async def _token(web: AsyncClient) -> str:
    resp = await web.post("/api/tokens", json={"name": "apply-routine"})
    assert resp.status_code == 201, resp.text
    return str(resp.json()["token"])


async def _bring(web: AsyncClient, company: str, country: str = "DE") -> int:
    resp = await web.post("/api/jobs/bring", json={
        "title": "Data Engineer", "company": company, "location": "Berlin", "country": country,
        "apply_url": f"https://{company.lower()}.example/jobs/1", "description": "Build the pipelines. " * 8})
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _ask(agent: AsyncClient, app_id: int, *rows: dict[str, str]) -> int:
    qs = [{**r, "answer": "", "source": "not_known"} for r in rows]
    resp = await agent.post("/api/asks", json={"application_id": app_id, "question": "Form answers",
                                               "kind": "question", "questions": qs})
    assert resp.status_code == 201, resp.text
    return int(resp.json()["id"])


async def _remember(web: AsyncClient, ask_id: int, answers: dict[str, str]) -> dict[str, Any]:
    resp = await web.post(f"/api/asks/{ask_id}/answers/remember", json={"answers": answers})
    assert resp.status_code == 200, resp.text
    return dict(resp.json())


def _profile(user_id: str) -> Any:
    from src.services.profile.storage import load_profile

    return load_profile(user_id)


def _de_rtw(user_id: str) -> Any:
    p = _profile(user_id)
    rows = ((p.user_info.right_to_work or {}).get("countries") or []) if p else []
    return next((r for r in rows if r.get("country") == "DE"), None)


def _de_salary(user_id: str) -> Any:
    p = _profile(user_id)
    return next((r for r in (p.preferences.salary_by_country or []) if r.get("country") == "DE"), None) if p else None


async def _status(web: AsyncClient, ask_id: int) -> str:
    resp = await web.get("/api/asks", params={"status": "all"})
    return next(a["status"] for a in resp.json()["asks"] if a["id"] == ask_id)


@pytest.mark.asyncio
async def test_a_sponsorship_no_on_one_de_job_goes_to_de_memory_and_closes_the_other_de_ask(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as web:
        first, second = await _bring(web, "Acme"), await _bring(web, "Initech")
        async with _agent(await _token(web)) as agent:
            ask_one, ask_two = await _ask(agent, first, SPONSOR), await _ask(agent, second, SPONSOR)
        body = await _remember(web, ask_one, {SPONSOR["key"]: "No"})
        assert body["memory"] == {SPONSOR["key"]: {"path": "user_info.right_to_work",
                                                   "key": "right_to_work.DE.needs_sponsorship"}}
        assert (body["saved"], body["not_saved"], body["skipped"], "conflicts" in body) == ([], {}, [], False)
        assert _de_rtw(fixture_user_id) == {"country": "DE", "needs_sponsorship": False}
        assert not any(r.get("topic") == SPONSOR["key"] for r in _profile(fixture_user_id).user_info.answers or [])
        assert await _status(web, ask_two) == "withdrawn", "the second DE job's same ask is closed by Memory"
        assert await _status(web, ask_one) == "withdrawn", "the answered row's own ask is now Memory's too"
        third = await _bring(web, "Globex")
        async with _agent(await _token(web)) as agent:
            ask_three = await _ask(agent, third, SPONSOR)
        again = await _remember(web, ask_three, {SPONSOR["key"]: "no"})
        assert again["memory"][SPONSOR["key"]]["key"] == "right_to_work.DE.needs_sponsorship", "same value: no-op"
        assert _de_rtw(fixture_user_id) == {"country": "DE", "needs_sponsorship": False}


@pytest.mark.asyncio
async def test_a_salary_range_goes_to_de_salary_in_its_own_currency(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as web:
        app_id = await _bring(web, "Acme")
        async with _agent(await _token(web)) as agent:
            ask_id = await _ask(agent, app_id, PAY)
        body = await _remember(web, ask_id, {PAY["key"]: "50000-60000 EUR"})
        assert body["memory"][PAY["key"]] == {"path": "preferences.salary_by_country", "key": "salary.DE"}
    assert _de_salary(fixture_user_id) == {"country": "DE", "min": 50000, "max": 60000, "currency": "EUR",
                                          "period": "year"}


@pytest.mark.asyncio
async def test_a_different_value_already_in_memory_is_a_conflict_and_nothing_is_written(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as web:
        resp = await web.patch("/api/profile", json={"edits": [{"path": "user_info.right_to_work", "value": {
            "countries": [{"country": "DE", "work_authorization": "visa", "needs_sponsorship": False}]}}]})
        assert resp.status_code == 200, resp.text
        app_id = await _bring(web, "Acme")
        async with _agent(await _token(web)) as agent:
            ask_id = await _ask(agent, app_id, SPONSOR)
        body = await _remember(web, ask_id, {SPONSOR["key"]: "Yes"})
    assert "memory" not in body and body["not_saved"] == {}
    assert body["conflicts"][SPONSOR["key"]].startswith("your DE page says No; this answer says Yes")
    assert _de_rtw(fixture_user_id) == {"country": "DE", "work_authorization": "visa", "needs_sponsorship": False}


@pytest.mark.asyncio
async def test_an_unclear_answer_is_not_saved_and_names_the_page(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as web:
        app_id = await _bring(web, "Acme")
        async with _agent(await _token(web)) as agent:
            ask_id = await _ask(agent, app_id, SPONSOR, PAY)
        body = await _remember(web, ask_id, {SPONSOR["key"]: "yes, but only from May", PAY["key"]: "at least 50k EUR"})
    assert "memory" not in body and "conflicts" not in body
    assert "your DE page" in body["not_saved"][SPONSOR["key"]] and "your DE page" in body["not_saved"][PAY["key"]]
    assert (_de_rtw(fixture_user_id), _de_salary(fixture_user_id)) == (None, None)


@pytest.mark.asyncio
async def test_a_small_range_with_no_period_is_not_saved_and_names_the_page(
    authenticated_async_context, fixture_user_id
):
    """Owner 2026-11-06: no period word reads as a year only from 10000 up; "4000-4500 GBP" could be a month."""
    async with authenticated_async_context() as web:
        app_id = await _bring(web, "Acme")
        async with _agent(await _token(web)) as agent:
            ask_id = await _ask(agent, app_id, PAY)
        body = await _remember(web, ask_id, {PAY["key"]: "4000-4500 GBP"})
    assert "memory" not in body and "conflicts" not in body
    assert "your DE page" in body["not_saved"][PAY["key"]]
    assert _de_salary(fixture_user_id) is None


# Checker on #1526 (2026-11-06): each of these, answered "Yes", once stored needs_sponsorship / relocation TRUE.
INVERTED = [
    ("sponsorship.de.q1", "Are you eligible to work in Germany without sponsorship?"),
    ("sponsorship.de.q2", "Are you free of any need for visa sponsorship?"),
    ("sponsorship.de.q3", "Are you unable to work here without sponsorship?"),
    ("sponsorship.de.q4", "Are you exempt from needing sponsorship?"),
    ("sponsorship.de.q5", "Can you start without requiring sponsorship?"),
    ("sponsorship.de.q6", "Don't you need sponsorship to work in Germany?"),
    ("relocation.de.q7", "Are you unwilling to relocate?"),
    # Re-check at 677cd6f65: glued contractions, another country, scoped relocation, a parenthetical.
    ("sponsorship.de.q8", "Dont you need sponsorship?"),
    ("sponsorship.de.q9", "Doesnt you need sponsorship?"),
    ("sponsorship.de.q10", "Wont you need sponsorship?"),
    ("sponsorship.de.q11", "Arent you need sponsorship?"),
    ("sponsorship.de.q12", "Do you need sponsorship to work in the UK?"),
    ("sponsorship.de.q13", "Do you need sponsorship to work in France?"),
    ("relocation.de.q14", "Are you willing to relocate only within the UK?"),
    ("relocation.de.q15", "Are you open to relocating abroad?"),
    ("sponsorship.de.q16", "Do you need sponsorship? (answer N/A for citizens)"),
    ("sponsorship.de.q17", "Do you only need sponsorship for a second job?"),
]


@pytest.mark.parametrize("key,question", INVERTED)
@pytest.mark.asyncio
async def test_an_inverted_question_answered_yes_is_not_saved(
    authenticated_async_context, fixture_user_id, key, question
):
    async with authenticated_async_context() as web:
        app_id = await _bring(web, "Acme")
        async with _agent(await _token(web)) as agent:
            ask_id = await _ask(agent, app_id, {"key": key, "question": question})
        body = await _remember(web, ask_id, {key: "Yes"})
    assert "memory" not in body and "conflicts" not in body, body
    assert "your DE page" in body["not_saved"][key]
    p = _profile(fixture_user_id)
    assert (_de_rtw(fixture_user_id), (p.user_info.logistics or {}).get("countries") if p else None) in (
        (None, None), (None, [])), "Memory unchanged"


@pytest.mark.parametrize("place,shown", [("EUROPE", "Europe"), ("ANY", "ANY")])
@pytest.mark.asyncio
async def test_a_conflict_names_the_page_the_value_comes_from(
    authenticated_async_context, fixture_user_id, place, shown
):
    async with authenticated_async_context() as web:
        page = [{"country": place, "work_authorization": "visa", "needs_sponsorship": False}]
        resp = await web.patch("/api/profile", json={"edits": [
            {"path": "user_info.right_to_work", "value": {"countries": page}}]})
        assert resp.status_code == 200, resp.text
        app_id = await _bring(web, "Acme")
        async with _agent(await _token(web)) as agent:
            ask_id = await _ask(agent, app_id, SPONSOR)
        body = await _remember(web, ask_id, {SPONSOR["key"]: "Yes"})
    assert "memory" not in body
    assert body["conflicts"][SPONSOR["key"]].startswith(f"your {shown} page says No; this answer says Yes")
    assert _profile(fixture_user_id).user_info.right_to_work["countries"] == page, "nothing written"


@pytest.mark.asyncio
async def test_an_assistant_answering_never_writes_memory(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as web:
        app_id = await _bring(web, "Acme")
        async with _agent(await _token(web)) as agent:
            ask_id = await _ask(agent, app_id, SPONSOR)
            refused = await agent.post(f"/api/asks/{ask_id}/answers/remember", json={"answers": {SPONSOR["key"]: "No"}})
            assert refused.status_code == 403, refused.text
            answered = await agent.post(f"/api/asks/{ask_id}/answer", json={"answers": {SPONSOR["key"]: "No"}})
            assert answered.status_code == 200, answered.text
    assert _de_rtw(fixture_user_id) is None


def test_no_mcp_tool_reaches_a_remember_door():
    """MCP ``answer_ask`` calls ``asks_route.answer_ask`` (the bearer door above); no tool calls a remember."""
    from pathlib import Path

    from src.api import mcp_server

    text = Path(mcp_server.__file__).read_text(encoding="utf-8")
    assert "asks_route.answer_ask(" in text
    assert [w for w in ("remember_ask_row_answers", "remember_questionnaire", "ask_once.remember") if w in text] == []


@pytest.mark.asyncio
async def test_a_per_job_row_is_never_saved(authenticated_async_context, fixture_user_id):
    heard = {"key": "how_heard", "question": "How did you hear about us?"}
    async with authenticated_async_context() as web:
        app_id = await _bring(web, "Acme")
        async with _agent(await _token(web)) as agent:
            ask_id = await _ask(agent, app_id, heard, SPONSOR)
        body = await _remember(web, ask_id, {"how_heard": "LinkedIn", SPONSOR["key"]: "No"})
    assert body["skipped"] == ["how_heard"] and list(body["memory"]) == [SPONSOR["key"]]
    assert not any(r.get("topic") == "how_heard" for r in _profile(fixture_user_id).user_info.answers or [])


def test_shapes_are_converted_or_refused_never_guessed():
    from src.services.profile.answer_to_memory import convert, salary, yes_no

    assert [yes_no(t) for t in ("No", "yes", "No, I do not need it", "No preference", "Yes, but only in May")] == [
        False, True, False, None, None]
    assert salary("€55k per year", "DE") == {"country": "DE", "min": 55000, "max": 55000, "currency": "EUR",
                                             "period": "year"}
    assert salary("4000 to 4500 GBP / month", "GB")["period"] == "month"
    assert [salary(t, "DE") for t in ("50000+ EUR", "$60000", "60000-50000 EUR", "50.000 EUR", "50000",
                                      "4000-4500 GBP")] == [None] * 6
    assert salary("10000 EUR", "DE")["period"] == "year" and salary("9999 EUR", "DE") is None
    rel = "logistics.{cc}.willing_to_relocate"
    assert convert(rel, "Are you willing to relocate?", "Yes", "DE") is True
    assert convert("logistics.{cc}.travel_ok_pct", "Are you willing to travel?", "25%", "DE") == 25
    assert convert("right_to_work.{cc}.onsite", "Are you willing to work on site?", "Only if sponsored", "DE") == "only_if_sponsored"
    assert convert("right_to_work.{cc}.onsite", "Willing to work on-site or hybrid?", "Yes", "DE") is None, "either/or"
    assert convert("logistics.{cc}.travel_ok_pct", "Unable to travel?", "No", "DE") is None
    sponsor = "right_to_work.{cc}.needs_sponsorship"
    assert convert(sponsor, "Will you now or in the future require visa sponsorship?", "Yes", "DE") is True
    assert convert("right_to_work.{cc}.work_authorization", "Allowed to work?", "Yes", "DE") is None
    assert convert("salary.{cc}", "Minimum salary you would accept", "50000 EUR", "DE") is None, "a floor"


def test_every_exact_phrasing_is_saved_and_another_country_is_not():
    """Each allow-listed phrasing (EXACT_PHRASINGS, the job's country written as its name) converts; the
    catalog's own texts too; the same question naming another country does not."""
    from src.services.profile.answer_to_memory import EXACT_PHRASINGS, convert

    templates = {"needs_sponsorship": ("right_to_work.{cc}.needs_sponsorship", "No", False),
                 "willing_to_relocate": ("logistics.{cc}.willing_to_relocate", "Yes", True),
                 "onsite": ("right_to_work.{cc}.onsite", "No", "no"),
                 "travel_ok_pct": ("logistics.{cc}.travel_ok_pct", "20%", 20),
                 "salary": ("salary.{cc}", "50000-60000 EUR",
                            {"country": "DE", "min": 50000, "max": 60000, "currency": "EUR", "period": "year"})}
    for field, phrases in EXACT_PHRASINGS.items():
        template, answer, want = templates[field]
        for phrase in sorted(phrases):
            question = phrase.replace("<country>", "Germany").capitalize() + "?"
            assert convert(template, question, answer, "DE") == want, question
            if "<country>" in phrase:
                assert convert(template, phrase.replace("<country>", "France") + "?", answer, "DE") is None, phrase
    sponsor = "right_to_work.{cc}.needs_sponsorship"
    for question in ("Will you now or in the future need visa sponsorship in DE?",  # the catalog's own text
                     "Do you need sponsorship to work in DE? (Yes/No)", "Do you need sponsorship to work in Germany?"):
        assert convert(sponsor, question, "No", "DE") is False, question
    assert convert("logistics.{cc}.willing_to_relocate", "Willing to relocate within Germany?", "Yes", "DE") is True
    assert convert("salary.{cc}", "Expected salary in Germany", "55000 EUR", "DE")["min"] == 55000
    assert convert(sponsor, "Do you need sponsorship to work in the United Kingdom?", "No", "DE") is None
    assert convert(sponsor, "Do you need sponsorship to work in the United Kingdom?", "No", "GB") is False


def test_uk_nations_count_as_the_uk_and_lowercase_us_is_never_a_country():
    """Pinned from the independent check (2026-11-06): England, Scotland and Wales name the UK's own
    sponsorship system, so a GB job saves them; on a DE job they are another country and nothing is saved.
    Lowercase "us" is the pronoun, never the United States."""
    from src.services.profile.answer_to_memory import convert

    sponsor = "right_to_work.{cc}.needs_sponsorship"
    for nation in ("England", "Scotland", "Wales"):
        assert convert(sponsor, f"Do you need sponsorship to work in {nation}?", "No", "GB") is False, nation
        assert convert(sponsor, f"Do you need sponsorship to work in {nation}?", "No", "DE") is None, nation
    assert convert(sponsor, "Do you need us to sponsor you?", "No", "US") is None
    assert convert(sponsor, "Do you need sponsorship to work in US?", "No", "US") is False
