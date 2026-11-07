"""Autonomy slice 3 (watch-only shadow, 2026-11-02): ``evidence.py`` says what autonomy WOULD clear; the
check_submit verdict never changes. Plus: an official ATS host is not a new site.

Pure tests need no database; the rest go through the real doors on the real Postgres.
Helpers are copied, never imported from another test module (schema isolation)."""
from __future__ import annotations

import json
from typing import Any

import pytest
from httpx import AsyncClient

from src.services.applications import evidence

BANK = (
    "Data engineer at Acme Corp, 2019 - present. Built ETL pipelines in Python and Snowflake, "
    "cutting warehouse costs 30%. BSc Computer Science, University of Leeds."
)
GROUNDED = (
    "At Acme Corp I built ETL pipelines in Python and Snowflake that cut warehouse costs by 30%. "
    "My degree from the University of Leeds gave me the grounding for it."
)
GB_ANSWER = {
    "question": "Do you require visa sponsorship?", "answer": "No", "approved": True,
    "topic": "sponsorship.GB.yes_no", "country": "GB",
}
SPONSOR_FILL = {"question": "Will you require visa sponsorship?", "answer": "No", "key": "sponsorship.GB.yes_no"}


def _essay(answer: str, prior: list[str] | None = None, *, sensitive: bool = False) -> dict[str, Any]:
    return evidence.essay_verdict("Why this role?", "why_role", answer, BANK, prior or [], sensitive=sensitive)


def _profile(answers: list[dict[str, Any]]) -> Any:
    from src.services.profile.models import CVData, UserInfo, UserProfile

    return UserProfile(cv_data=CVData(raw_text=BANK, name="Ada Lovelace"), user_info=UserInfo(answers=answers))


# ═══════════════════════════════════════════════════════════════════════════
# The pure checker
# ═══════════════════════════════════════════════════════════════════════════


def test_a_grounded_essay_would_clear():
    got = _essay(GROUNDED)
    assert got == {"question_key": "why_role", "would_clear": True, "reasons": []}


def test_an_invented_number_fails_and_names_the_token():
    got = _essay(GROUNDED.replace("30%", "45%"))
    assert got["would_clear"] is False
    assert got["reasons"] == ["unsupported:45%"]


def test_an_invented_employer_fails_and_names_the_token():
    got = _essay(GROUNDED.replace("Acme Corp", "Initech Corp"))
    assert got["would_clear"] is False
    assert got["reasons"] == ["unsupported:Initech"]


def test_a_near_copy_of_a_sent_essay_is_the_same_playbook():
    sent = GROUNDED + " I would bring the same care to Northwind."
    got = _essay(GROUNDED + " I would bring the same care to Southwind.", [sent])
    assert got["would_clear"] is False
    assert "same_playbook" in got["reasons"]
    assert _essay(GROUNDED, ["A completely different answer about teaching and travel."])["would_clear"] is True


def test_a_sensitive_question_never_clears():
    assert _essay(GROUNDED, sensitive=True)["reasons"] == ["sensitive"]


def test_right_to_work_matches_only_this_jobs_country():
    profile = _profile([GB_ANSWER])
    gb = evidence.rtw_verdict([SPONSOR_FILL], profile, "northwind", "GB")
    assert gb["ok"] is True
    # a US job reusing the GB answer: no saved US answer, a hard fail
    us = evidence.rtw_verdict([SPONSOR_FILL], profile, "northwind", "US")
    assert us["ok"] is False
    assert us["detail"].startswith("no saved answer for US")
    # the GB job answered differently from the saved GB answer
    wrong = evidence.rtw_verdict([{**SPONSOR_FILL, "answer": "Yes"}], profile, "northwind", "GB")
    assert wrong["ok"] is False and wrong["detail"].startswith("differs from the saved GB answer")
    assert evidence.rtw_verdict([], profile, "northwind", "US") == {"ok": True, "detail": "no right-to-work question"}


def test_cv_check_uses_check_text_and_bullet_overlap():
    profile = _profile([])
    cv = "Ada Lovelace\nEXPERIENCE\n- Built ETL pipelines in Python and Snowflake, cutting warehouse costs 30%.\n"
    assert evidence.cv_verdict(cv, profile) == {"would_clear": True, "reasons": []}
    invented = cv + "- Led a team of 12 engineers on a Kubernetes migration.\n"
    got = evidence.cv_verdict(invented, profile)
    assert got["would_clear"] is False
    assert got["reasons"] == ["unsupported_numbers", "unsupported_terms", "bullet_unmatched"]  # Kubernetes (2026-11-07)
    assert evidence.cv_verdict(None, profile) == {"would_clear": False, "reasons": ["no_cv"]}


def test_the_event_payload_keeps_codes_only():
    shadow = {
        "watch_only": True, "essays": [_essay(GROUNDED.replace("Acme Corp", "Initech Corp"))],
        "cv": {"would_clear": True, "reasons": []}, "rtw": {"ok": False, "detail": "no saved answer for US"},
    }
    assert evidence.event_payload(shadow) == {
        "essays": [{"question_key": "why_role", "would_clear": False, "reasons": ["unsupported"]}],
        "cv": {"would_clear": True, "reasons": []}, "rtw_ok": False,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Real-database helpers
# ═══════════════════════════════════════════════════════════════════════════

FORM = "https://careers.northwind.example/apply/7"
CV_TEXT = "Ada Lovelace\nEXPERIENCE\n- Built ETL pipelines in Python and Snowflake, cutting warehouse costs 30%.\n"


def _seed_profile(user_id: str) -> None:
    from src.services.profile.models import CVData, UserInfo, UserProfile
    from src.services.profile.storage import save_profile, save_user_info

    save_profile(UserProfile(cv_data=CVData(raw_text=BANK, name="Ada Lovelace", skills=["Python"])),
                 user_id, source_action="cv_upload")
    save_user_info(user_id, UserInfo(answers=[GB_ANSWER]), "test")


async def _bring(client: AsyncClient, n: int, country: str = "GB") -> int:
    resp = await client.post("/api/jobs/bring", json={
        "title": f"Data Engineer {n}", "company": "Northwind", "location": "London", "country": country,
        "apply_url": f"https://northwind.example/careers/{n}", "description": "Build the pipelines. Python.",
    })
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _event(client: AsyncClient, app_id: int, event_type: str, **body: Any):
    return await client.post(f"/api/applications/{app_id}/events", json={"event_type": event_type, **body})


async def _fill(client: AsyncClient, app_id: int, essay: str) -> None:
    assert (await client.post(f"/api/applications/{app_id}/artifacts", json={"kind": "cv", "text": CV_TEXT})).status_code == 201
    checked = await _event(client, app_id, "form_checked", payload={"questions_count": 2, "answered": 2, "missing": 0})
    assert checked.status_code == 201, checked.text
    answers = [
        {"question": "Why this role?", "answer": essay, "source": "written", "key": "why_role"},
        {**{k: SPONSOR_FILL[k] for k in ("question", "answer", "key")}, "source": "memory"},
    ]
    filled = await _event(client, app_id, "form_filled", payload={"form_url": FORM, "fields_count": 2, "answers": answers})
    assert filled.status_code == 201, filled.text
    assert (await _event(client, app_id, "form_swept", payload={"fields_checked": 2, "fixed": 0})).status_code == 201


async def _check(client: AsyncClient, app_id: int) -> dict[str, Any]:
    resp = await client.get(f"/api/applications/{app_id}/submit-check", params={"form_url": FORM})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _db():
    from src.api import dependencies as api_deps

    return await api_deps.get_db()


async def _shadow_events(app_id: int) -> list[dict[str, Any]]:
    cur = await (await _db())._db.execute(
        "SELECT payload FROM application_events WHERE application_id = ? AND event_type = 'autonomy_shadow' ORDER BY id",
        (app_id,),
    )
    return [dict(r) for r in await cur.fetchall()]


VERDICT_KEYS = ("decision", "reason", "detail", "next_action", "stop_run", "type_first", "daily_left", "company_left")


# ═══════════════════════════════════════════════════════════════════════════
# check_submit: the shadow is shown and logged, the verdict is unchanged
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_check_submit_shows_the_shadow_and_its_verdict_is_identical_without_it(
    authenticated_async_context, fixture_user_id, monkeypatch
):
    from datetime import datetime, timezone

    from src.services.applications import kit, spine

    invented = GROUNDED.replace("Acme Corp", "Initech Corp")
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client, 1)
        await _fill(client, app_id, invented)
        body = await _check(client, app_id)
        shadow = body["autonomy_shadow"]
        assert shadow["watch_only"] is True
        assert shadow["essays"] == [{"question_key": "why_role", "would_clear": False, "reasons": ["unsupported:Initech"]}]
        assert shadow["cv"] == {"would_clear": True, "reasons": []}
        assert shadow["rtw"]["ok"] is True
        # the same gate with no shadow at all (kit.submit_verdict never asks for one)
        db = await _db()
        row = await spine.get_owned_application(db, fixture_user_id, app_id)
        plain = await kit.submit_verdict(db, fixture_user_id, row, FORM, datetime.now(timezone.utc), email_verified=True)
        assert (body["decision"], body["reason"]) == (plain.decision, plain.reason)
        # a checker exception: same verdict, no shadow, no event
        before = len(await _shadow_events(app_id))

        async def boom(*_a: Any, **_k: Any) -> dict[str, Any]:
            raise RuntimeError("checker bug")

        monkeypatch.setattr(evidence, "for_application", boom)
        broken = await _check(client, app_id)
        assert broken["autonomy_shadow"] is None
        assert {k: broken[k] for k in VERDICT_KEYS} == {k: body[k] for k in VERDICT_KEYS}
        assert len(await _shadow_events(app_id)) == before


@pytest.mark.asyncio
async def test_one_shadow_event_per_check_with_codes_and_no_answer_text(authenticated_async_context, fixture_user_id):
    invented = GROUNDED.replace("Acme Corp", "Initech Corp")
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client, 1)
        await _fill(client, app_id, invented)
        await _check(client, app_id)
        await _check(client, app_id)
        rows = await _shadow_events(app_id)
        assert len(rows) == 2
        payload = rows[0]["payload"] if isinstance(rows[0]["payload"], dict) else json.loads(rows[0]["payload"])
        assert payload["essays"] == [{"question_key": "why_role", "would_clear": False, "reasons": ["unsupported"]}]
        assert payload["rtw_ok"] is True
        words = set(json.dumps(payload).replace('"', " ").split())
        assert "Initech" not in words and "Initech" not in json.dumps(payload)
        assert "warehouse" not in json.dumps(payload) and "No" not in words
        # nobody else may write it
        refused = await _event(client, app_id, "autonomy_shadow", payload={})
        assert refused.status_code == 422, refused.text


@pytest.mark.asyncio
async def test_a_us_job_reusing_the_gb_sponsorship_answer_is_flagged(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client, 2, country="US")
        await _fill(client, app_id, GROUNDED)
        shadow = (await _check(client, app_id))["autonomy_shadow"]
        assert shadow["rtw"]["ok"] is False
        assert shadow["rtw"]["detail"].startswith("no saved answer for US")
        assert shadow["essays"][0]["would_clear"] is True


# ═══════════════════════════════════════════════════════════════════════════
# An official ATS host is a known application platform, not a new site
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_an_official_ats_host_is_never_a_new_site(authenticated_async_context, fixture_user_id):
    from src.services.applications import kit

    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client, 3)
        db = await _db()
        for host in ("boards.greenhouse.io", "jobs.lever.co", "acme.wd3.myworkdayjobs.com"):
            assert await kit._new_site(db, fixture_user_id, app_id, host) is False
        # look-alikes and the shared RippleHire sandbox are not official: still new
        for host in ("evil-greenhouse.io", "greenhouse.io.evil.example", "app.ripplehire.com", "careers.northwind.example"):
            assert await kit._new_site(db, fixture_user_id, app_id, host) is True
