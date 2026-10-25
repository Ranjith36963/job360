"""Owner 2026-10-25 - the one-time clean-up of asks filed before one-ask-per-job.

Production had Palantir with 7 open asks and Globalization Partners with 9. The user's own Needs-you read
now tidies them once: per job, family and side, the NEWEST open ask keeps every row (merged under fixed
names) and the older ones close as superseded; an open ask on a closed job closes. Nothing is deleted and a
second read changes nothing. Helpers are defined here, never imported (per-test schema isolation).
"""
from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

_AD = {"title": "AI Engineer I", "company": "Globalization Partners", "location": "London",
       "apply_url": "https://job-boards.greenhouse.io/gp/1", "description": "Build agents. " * 8}


def _row(key: str, question: str, source: str = "none", answer: str = "") -> dict[str, str]:
    return {"key": key, "question": question, "source": source, "answer": answer}


async def _old_style_ask(client: AsyncClient, app_id: int, rows: list[dict[str, str]], monkeypatch) -> int:
    """File an ask the way runs did before one-ask-per-job: never merged."""
    from src.services.applications import asks

    async def none(*_a: Any, **_k: Any) -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(asks, "_open_family", none)
    resp = await client.post("/api/asks", json={"question": "G-P form needs you", "application_id": app_id,
                                                "kind": "question", "questions": rows})
    monkeypatch.undo()
    assert resp.status_code == 201, resp.text
    return int(resp.json()["id"])


@pytest.mark.asyncio
async def test_the_users_read_tidies_duplicates_once(authenticated_async_context, monkeypatch):
    async with authenticated_async_context() as client:
        app_id = int((await client.post("/api/jobs/bring", json=_AD)).json()["application_id"])
        closed_id = int((await client.post("/api/jobs/bring", json={**_AD, "apply_url": "https://x.example/2",
                                                                     "company": "Seamflow"})).json()["application_id"])
        a = await _old_style_ask(client, app_id, [_row("gp.referred", "Referred?"), _row("gender", "Gender?", "memory",
                                                                                          "Male")], monkeypatch)
        b = await _old_style_ask(client, app_id, [_row("referred", "Referred by an employee?")], monkeypatch)
        c = await _old_style_ask(client, app_id, [_row("gp.worked_before", "Worked at G-P?")], monkeypatch)
        stale = await _old_style_ask(client, closed_id, [_row("consent", "Consent?")], monkeypatch)
        rejected = await client.post(f"/api/applications/{closed_id}/events", json={"event_type": "rejected"})
        assert rejected.status_code == 201, rejected.text
        listed = (await client.get("/api/asks")).json()
        assert [x["id"] for x in listed["asks"]] == [c] and listed["open_count"] == 1
        [kept] = listed["asks"]
        assert [(q["key"], q["source"]) for q in kept["questions"]] == [
            ("equality.gender", "memory"), ("referred", "none"), ("worked_before", "none"),
        ]
        every = {x["id"]: x for x in (await client.get("/api/asks", params={"status": "all"})).json()["asks"]}
        assert every[a]["withdrawn_by"] == every[b]["withdrawn_by"] == "superseded"
        assert every[a]["withdrawn_reason"] == f"replaced by ask {c}"
        assert every[stale]["withdrawn_by"] == "job_closed"
        again = (await client.get("/api/asks")).json()
        assert [x["id"] for x in again["asks"]] == [c] and again["asks"][0]["questions"] == kept["questions"]


@pytest.mark.asyncio
async def test_paste_the_question_rows_go_and_the_job_gets_a_note(authenticated_async_context, monkeypatch):
    """Palantir on production: "Paste the two required essay questions" - the assistant reads the form itself."""
    async with authenticated_async_context() as client:
        app_id = int((await client.post("/api/jobs/bring", json=_AD)).json()["application_id"])
        only = int((await client.post("/api/jobs/bring", json={**_AD, "apply_url": "https://x.example/3",
                                                                "company": "Yonder"})).json()["application_id"])
        await _old_style_ask(client, app_id, [_row("palantir.essay_questions", "Paste the two required essay questions")],
                             monkeypatch)
        kept = await _old_style_ask(client, app_id, [_row("gender", "Gender?", "memory", "Male")], monkeypatch)
        gone = await _old_style_ask(client, only, [_row("essay_1", "Essay 1: please paste its prompt")], monkeypatch)
        listed = (await client.get("/api/asks")).json()
        assert [x["id"] for x in listed["asks"]] == [kept]
        assert [q["key"] for q in listed["asks"][0]["questions"]] == ["equality.gender"]
        every = {x["id"]: x for x in (await client.get("/api/asks", params={"status": "all"})).json()["asks"]}
        assert every[gone]["withdrawn_by"] == "system"
        for job in (app_id, only):
            events = (await client.get(f"/api/applications/{job}")).json()["events"]
            notes = [e for e in events if e["event_type"] == "note"]
            assert len(notes) == 1 and "Read this form" in notes[0]["detail"]
        await client.get("/api/asks")  # a second read writes nothing more
        assert len([e for e in (await client.get(f"/api/applications/{only}")).json()["events"]
                    if e["event_type"] == "note"]) == 1
