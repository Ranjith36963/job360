"""Weak spot #11 wave 2 - ``check_form`` resolves a FORM question to a saved answer.

Reference (opened 2026-10-15): trackly-cli answer-resolution.md:49-52 (narrow-to-broad, the narrower
wins, never widen), :56-60 (aliases are a fallback; ambiguity fails closed), :145 (certification is
never reusable); neonwatty job-apply-store.py:452-471 (question OR alias) and :475-540 (upsert).
Real doors (HTTP), asserting VALUES (rule #21).
"""
from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from src.services.applications import kit as kit_service
from src.services.profile.answer_match import resolve_answer
from src.services.profile.models import AssistantSettings, CVData, UserPreferences, UserProfile
from src.services.profile.user_info import validate_user_info

NOW = "2026-10-01T09:00:00+00:00"
_AD = {
    "title": "Data Engineer", "company": "Northwind", "location": "London", "country": "GB", "remote": False,
    "apply_url": "https://northwind.example/careers/7",
    "description": "Build the pipelines. Python, dbt, Snowflake.",
}


def _rec(question: str, answer: str, **extra: Any) -> dict[str, Any]:
    return {"question": question, "answer": answer, "approved": True, "recorded_at": NOW, **extra}


def _seed_profile(user_id: str) -> None:
    from src.services.profile.storage import save_assistant_settings, save_profile

    save_profile(
        UserProfile(cv_data=CVData(raw_text="Python.", name="Ada Lovelace"),
                    preferences=UserPreferences(target_job_titles=["Data Engineer"])),
        user_id, source_action="cv_upload",
    )
    save_assistant_settings(user_id, AssistantSettings(apply_min_score=50, above_line="auto", below_line="auto"), "t")


async def _setup(client: AsyncClient, user_id: str, answers: list[dict[str, Any]]) -> int:
    _seed_profile(user_id)
    app_id = int((await client.post("/api/jobs/bring", json=_AD)).json()["application_id"])
    resp = await client.patch("/api/profile", json={"edits": [{"path": "user_info.answers", "value": answers}]})
    assert resp.status_code == 200, resp.text
    return app_id


async def _check(client: AsyncClient, app_id: int, questions: list[dict[str, Any]]) -> dict[str, Any]:
    resp = await client.post(f"/api/applications/{app_id}/kit/form-check", json={"questions": questions})
    assert resp.status_code == 200, resp.text
    return dict(resp.json())


@pytest.mark.asyncio
async def test_alias_topic_and_wording_resolve_to_the_saved_value(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id = await _setup(client, fixture_user_id, [
            _rec("Can you work from India?", "Yes", topic="work_from.IN",
                 aliases=["Are you able to relocate to India?"]),
        ])
        body = await _check(client, app_id, [
            {"question": "Are you able to relocate to India?"},  # alias
            {"question": "can you work from india"},  # wording, case and punctuation ignored
            {"question": "Willing to work abroad?", "key": "work_from.IN"},  # key = the topic
            {"question": "Do you hold a dog licence?"},
        ])
        assert [(a["value"], a["matched_by"]) for a in body["answered"]] == [
            ("Yes", "wording"), ("Yes", "wording"), ("Yes", "topic"),
        ]
        assert [m["question"] for m in body["missing"]] == ["Do you hold a dog licence?"]


@pytest.mark.asyncio
async def test_company_answer_is_narrower_and_wins(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id = await _setup(client, fixture_user_id, [
            _rec("Work from India?", "Yes", topic="work_from.IN"),
            _rec("Work from India? (Northwind)", "No", topic="work_from.IN", company="Northwind"),
            _rec("Why us?", "Southwind pipelines.", company="Southwind"),
        ])
        body = await _check(client, app_id, [
            {"question": "x", "key": "work_from.IN"}, {"question": "Why us?"},
        ])
        assert [a["value"] for a in body["answered"]] == ["No"], "Northwind's own answer, not the general Yes"
        assert [m["question"] for m in body["missing"]] == ["Why us?"], "another company's answer never widens"


def test_disagreeing_answers_fail_closed_with_a_note():
    cands = [_rec("A?", "Yes", topic="t"), _rec("B?", "No", topic="t")]
    rec, note = resolve_answer(cands, topic="t")
    assert rec is None and note and note.startswith("ambiguous")
    out = kit_service.form_check({}, [{"question": "A?", "key": "t"}], cands)
    assert out["answered"] == [] and out["missing"][0]["why"].startswith("ambiguous")


@pytest.mark.asyncio
async def test_unapproved_and_consent_answers_never_match(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id = await _setup(client, fixture_user_id, [
            _rec("Do you agree?", "Yes", topic="consent.data"),
            _rec("I certify this is true", "Yes", topic="certify.truth"),
            {**_rec("Salary?", "50k"), "approved": False},
        ])
        body = await _check(client, app_id, [
            {"question": "Do you agree?"}, {"question": "I certify this is true"}, {"question": "Salary?"},
            {"question": "x", "key": "consent.data"},
        ])
        assert body["answered"] == [] and body["counts"]["missing"] == 4


def test_same_topic_and_company_is_one_record_the_later_wins():
    out = validate_user_info("user_info.answers", [
        _rec("Q1?", "old", topic="a.b"), _rec("Q2?", "new", topic="a.b"),
        _rec("Q3?", "acme", topic="a.b", company="Acme"), _rec("Q4?", "no topic"), _rec("Q5?", "no topic 2"),
    ])
    assert [(r["question"], r["answer"]) for r in out] == [
        ("Q2?", "new"), ("Q3?", "acme"), ("Q4?", "no topic"), ("Q5?", "no topic 2"),
    ]
