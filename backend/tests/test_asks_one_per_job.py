"""Owner 2026-10-25 - one open ask per job and kind.

On production every hourly Apply run filed the same form questions again (Palantir: 7 open asks, 36 rows,
"how did you hear" six times under four names). Now a new question ask on a job MERGES with the job's open
one: the rows join under fixed names, the older asks close as "superseded" (kept, never deleted), and a
newer "not known yet" never drops an answer an older row proposed. A blocker (CAPTCHA, sign-in...) is its
own family. The open-asks cap only stops a NEW card, never a merge. Helpers are defined here, never imported.
"""
from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from src.services.applications import asks

_AD = {"title": "Forward Deployed AI Engineer", "company": "Palantir", "location": "London",
       "apply_url": "https://jobs.lever.co/palantir/1", "description": "Build with customers. " * 8}


async def _bring(client: AsyncClient, n: int = 1) -> int:
    resp = await client.post("/api/jobs/bring", json={**_AD, "apply_url": f"https://jobs.lever.co/palantir/{n}",
                                                      "company": f"Palantir {n}" if n > 1 else "Palantir"})
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


def _row(key: str, question: str, source: str = "none", answer: str = "") -> dict[str, str]:
    return {"key": key, "question": question, "source": source, "answer": answer}


async def _ask(client: AsyncClient, app_id: int, rows: list[dict[str, str]], question: str = "Palantir form needs you"):
    resp = await client.post("/api/asks", json={"question": question, "application_id": app_id, "kind": "question",
                                                "questions": rows})
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _open(client: AsyncClient, app_id: int) -> list[dict[str, Any]]:
    return [a for a in (await client.get(f"/api/applications/{app_id}")).json()["asks"] if a["status"] == "open"]


@pytest.mark.parametrize(("key", "fixed"), [
    ("how_heard", "how_heard"), ("heard_from", "how_heard"), ("referral_source", "how_heard"),
    ("palantir_how_heard", "how_heard"), ("palantir.consents", "consent"), ("gp.referred", "referred"),
    ("gp_gender", "equality.gender"), ("equality.ethnicity", "equality.ethnicity"), ("essay_1", "essay_1"),
    ("cv.unsupported", "cv_unsupported_terms"), ("salary_expectation", "salary_expectation"),
])
def test_common_questions_get_one_fixed_name(key, fixed):
    assert asks.canonical_key(key) == fixed


def test_a_newer_not_known_never_drops_a_proposed_answer():
    merged = asks.merge_questions(
        [_row("palantir_how_heard", "How did you hear about us", "guess", "Palantir Website")],
        [_row("heard_from", "How did you hear about this job?"), _row("university", "University?", "cv", "Herts")],
    )
    assert [(q["key"], q["source"], q["answer"]) for q in merged] == [
        ("how_heard", "guess", "Palantir Website"), ("university", "cv", "Herts"),
    ]
    assert merged[0]["question"] == "How did you hear about this job?"  # the newest wording


@pytest.mark.asyncio
async def test_three_hourly_runs_leave_one_open_ask(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        one = await _ask(client, app_id, [_row("palantir_how_heard", "How did you hear?"), _row("essay_1", "Essay 1")])
        two = await _ask(client, app_id, [_row("heard_from", "How did you hear about this job?"),
                                          _row("consent", "Tick consent?")])
        three = await _ask(client, app_id, [_row("referral_source", "How did you hear about Palantir?"),
                                            _row("university", "University?", "cv", "University of Hertfordshire")])
        assert (two["merged_from"], three["merged_from"]) == ([one["id"]], [two["id"]])
        [left] = await _open(client, app_id)
        assert left["id"] == three["id"]
        assert [q["key"] for q in left["questions"]] == ["essay_1", "consent", "how_heard", "university"]
        every = {a["id"]: a for a in (await client.get(f"/api/applications/{app_id}")).json()["asks"]}
        assert every[one["id"]]["withdrawn_by"] == "superseded"
        assert every[one["id"]]["withdrawn_reason"] == f"replaced by ask {two['id']}"
        answered = await client.post(f"/api/asks/{three['id']}/answer", json={"answers": {
            "essay_1": "x", "consent": "Yes", "how_heard": "LinkedIn", "university": "University of Hertfordshire"}})
        assert answered.status_code == 200, answered.text


@pytest.mark.asyncio
async def test_a_sentence_only_ask_is_kept_as_a_row_and_a_blocker_is_its_own_card(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        cv = (await client.post("/api/asks", json={"question": "Please approve CV 87.", "application_id": app_id})).json()
        captcha = await client.post("/api/asks", json={"question": "Solve the CAPTCHA.", "application_id": app_id,
                                                       "kind": "captcha"})
        assert captcha.status_code == 201, captcha.text
        form = await _ask(client, app_id, [_row("gender", "Gender?", "memory", "Male")])
        assert form["merged_from"] == [cv["id"]]
        assert [q["key"] for q in form["questions"]] == [f"earlier_{cv['id']}", "equality.gender"]
        assert {a["kind"] for a in await _open(client, app_id)} == {"captcha", "question"}
        again = await client.post("/api/asks", json={"question": "Solve the CAPTCHA again.", "application_id": app_id,
                                                     "kind": "captcha"})
        assert again.json()["merged_from"] == [captcha.json()["id"]]
        assert len(await _open(client, app_id)) == 2


@pytest.mark.asyncio
async def test_a_sentence_only_ask_merged_into_rows_keeps_its_sentence(authenticated_async_context):
    """Review of #1215: a sentence-only ask on a job whose open ask has rows lost its own question."""
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        await _ask(client, app_id, [_row("gender", "Gender?", "memory", "Male")])
        later = (await client.post("/api/asks", json={"question": "Please approve CV 88.", "application_id": app_id,
                                                      "kind": "question"})).json()
        assert [q["question"] for q in later["questions"]] == ["Gender?", "Please approve CV 88."]
        assert len(await _open(client, app_id)) == 1


@pytest.mark.asyncio
async def test_the_cap_stops_a_new_card_never_a_merge(authenticated_async_context, monkeypatch):
    from src.core import settings

    monkeypatch.setattr(settings, "ASKS_MAX_OPEN_PER_USER", 1)
    async with authenticated_async_context() as client:
        first, second = await _bring(client, 1), await _bring(client, 2)
        await _ask(client, first, [_row("languages", "Languages?")])
        assert (await client.post("/api/asks", json={"question": "Again", "application_id": first, "kind": "question",
                                                     "questions": [_row("languages", "Which languages?")]})).status_code == 201
        refused = await client.post("/api/asks", json={"question": "New job", "application_id": second})
        assert refused.status_code == 429
