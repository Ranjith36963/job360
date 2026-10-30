"""check_form answers a CV file field with THIS application's tailored CV (production, 2026-10-30).

The 20:26 scheduled Apply run: the owner's approved saved answer ``{"Resume": "CV v1, artifact 128"}``
(an old generic upload) matched every form's "Resume" file field, so check_form handed the assistant
that stale label instead of the CV tailored for the application (loveholidays 329 got artifact 133,
Marlabs 327 its own). The fallback to the tailored artifact ran only when NO saved answer matched.
Downstream the stale label then set ``changed_to`` on the Ready card's file row (held from Send all) and
made a re-fill that changed only the file row cancel the user's yes (``approval_stale`` form_fill).
A TEXT question about the CV is never a file field and keeps the saved words (owner's 2026-10-19 lock).
Real HTTP doors, VALUE asserts (rule #21). Helpers are copied, never imported.
"""
from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from src.services.applications import kit as kit_service
from src.services.profile.models import AssistantSettings, CVData, UserPreferences, UserProfile

FORM = "https://jobs.ashbyhq.example/loveholidays/apply"
GAP_Q = "Is there a gap in your CV?"
STALE_RESUME = {"question": "Resume", "answer": "CV v1, artifact 128", "approved": True}
USERS_GAP = {"question": GAP_Q, "answer": "Yes, a 2024 sabbatical", "approved": True}
NOTICE = {"question": "Preferred start date", "answer": "1 month", "source": "profile"}
_AD = {
    "title": "Data Engineer", "company": "loveholidays", "location": "London", "country": "GB", "remote": False,
    "apply_url": "https://loveholidays.example/careers/7", "description": "Build the pipelines. Python, dbt.",
}


def _seed_profile(user_id: str) -> None:
    from src.services.profile.storage import save_assistant_settings, save_profile

    save_profile(
        UserProfile(cv_data=CVData(raw_text="Python data engineer.", name="Ada Lovelace"),
                    preferences=UserPreferences(target_job_titles=["Data Engineer"])),
        user_id, source_action="cv_upload",
    )
    save_assistant_settings(
        user_id, AssistantSettings(apply_min_score=50, above_line="auto", below_line="auto"), "test_seed"
    )


async def _app(client: AsyncClient, user_id: str, saved: list[dict[str, Any]], tailored: bool = True) -> tuple[int, int]:
    """A brought job, the user's saved answers and (``tailored``) a CV saved for THIS application."""
    _seed_profile(user_id)
    edit = {"path": "user_info.answers", "value": saved}
    assert (await client.patch("/api/profile", json={"edits": [edit]})).status_code == 200
    resp = await client.post("/api/jobs/bring", json=_AD)
    assert resp.status_code == 200, resp.text
    app_id = int(resp.json()["application_id"])
    if not tailored:
        return app_id, 0
    cv = await client.post(f"/api/applications/{app_id}/artifacts", json={"kind": "cv", "text": "Ada\nTailored CV."})
    assert cv.status_code == 201, cv.text
    return app_id, int(cv.json()["artifact_id"])


async def _form_check(client: AsyncClient, app_id: int, questions: list[dict[str, Any]]) -> dict[str, Any]:
    resp = await client.post(f"/api/applications/{app_id}/kit/form-check", json={"form_url": FORM, "questions": questions})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _event(client: AsyncClient, app_id: int, event_type: str, payload: dict[str, Any]) -> int:
    resp = await client.post(f"/api/applications/{app_id}/events", json={"event_type": event_type, "payload": payload})
    assert resp.status_code == 201, resp.text
    return int(resp.json()["event_id"])


async def _fill(client: AsyncClient, app_id: int, answers: list[dict[str, Any]]) -> int:
    fill = await _event(client, app_id, "form_filled", {"form_url": FORM, "fields_count": len(answers), "answers": answers})
    await _event(client, app_id, "form_swept", {"fields_checked": len(answers), "fixed": 0, "answers": answers})
    return fill


@pytest.mark.asyncio
async def test_a_cv_file_field_gets_the_tailored_cv_even_when_a_saved_answer_matches(
    authenticated_async_context, fixture_user_id
):
    """Production 2026-10-30 (applications 327, 329): the stale "CV v1, artifact 128" label lost to
    the CV saved for this application; the TEXT question about the CV keeps the user's saved words."""
    async with authenticated_async_context() as client:
        app_id, cv_id = await _app(client, fixture_user_id, [STALE_RESUME, USERS_GAP])
        body = await _form_check(client, app_id, [
            {"question": "Resume", "field_type": "file"},
            {"question": GAP_Q, "field_type": "text"},
        ])
        rows = [(a["question"], a["key"], a["value"], a["source"]) for a in body["answered"]]
        assert rows == [
            ("Resume", "artifact.cv", f"CV v1 (artifact {cv_id})", "artifact"),
            (GAP_Q, GAP_Q, "Yes, a 2024 sabbatical", "approved_text"),
        ], body
        assert body["missing"] == []


@pytest.mark.asyncio
async def test_with_no_tailored_cv_the_saved_answer_still_answers_the_file_field(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        app_id, _ = await _app(client, fixture_user_id, [STALE_RESUME], tailored=False)
        body = await _form_check(client, app_id, [{"question": "Resume", "field_type": "file"}])
        assert [(a["key"], a["value"], a["source"]) for a in body["answered"]] == [
            ("Resume", "CV v1, artifact 128", "approved_text")
        ], body


def test_pure_saved_answer_loses_only_on_a_cv_or_letter_file_field_with_a_document() -> None:
    answers = {"approved_text": [
        {"key": "Resume", "value": "CV v1, artifact 128", "source": "approved_text", "state": "confirmed"},
        {"key": "Cover letter", "value": "Letter v1, artifact 9", "source": "approved_text", "state": "confirmed"},
    ]}
    docs = {"cv": {"id": 133, "version_no": 2}}
    qs = [
        {"question": "Resume", "field_type": "file"},
        {"question": "Resume", "field_type": "text"},
        {"question": "Cover letter", "field_type": "file"},
    ]
    out = kit_service.form_check(answers, qs, documents=docs)
    assert [(a["field_type"], a["key"], a["value"]) for a in out["answered"]] == [
        ("file", "artifact.cv", "CV v2 (artifact 133)"),
        ("text", "Resume", "CV v1, artifact 128"),
        ("file", "Cover letter", "Letter v1, artifact 9"),  # no letter saved for this application: unchanged
    ]


@pytest.mark.asyncio
async def test_a_ready_card_differing_only_on_the_file_row_is_not_changed(authenticated_async_context, fixture_user_id):
    """The Ready card never sets ``changed_to`` on the CV file row, so it is not held from Send all;
    a TEXT row the user changed still reads ``changed``."""
    async with authenticated_async_context() as client:
        app_id, cv_id = await _app(client, fixture_user_id, [STALE_RESUME, USERS_GAP])
        resume = {"question": "Resume", "answer": f"CV v1 (artifact {cv_id})", "source": "profile"}
        gap = {"question": GAP_Q, "answer": "Yes, a 2024 sabbatical", "source": "approved"}
        await _fill(client, app_id, [NOTICE, resume, gap])
        resp = await client.get("/api/ready-to-send", params={"application_id": app_id})
        assert resp.status_code == 200, resp.text
        card = resp.json()["items"][0]
        by_q = {a["question"]: a for a in card["answers"]}
        assert (by_q["Resume"]["changed_to"], by_q["Resume"]["changed_reason"]) == (None, None), card
        assert "changed" not in [f["code"] for f in card["flags"]], card["flags"]
        # The lock is intact for text: a typed gap answer that is not the user's word is "changed".
        await _fill(client, app_id, [NOTICE, resume, {**gap, "answer": "No"}])
        resp = await client.get("/api/ready-to-send", params={"application_id": app_id})
        card = resp.json()["items"][0]
        assert {a["question"]: a["changed_to"] for a in card["answers"]}[GAP_Q] == "Yes, a 2024 sabbatical"
        assert "changed" in [f["code"] for f in card["flags"]]


@pytest.mark.asyncio
async def test_a_refill_differing_only_on_the_file_row_keeps_the_yes(authenticated_async_context, fixture_user_id):
    """approval_stale: a re-fill whose only change is the CV file row (a re-uploaded file) keeps the
    user's yes; a re-fill that changes a typed text answer away from the user's word still cancels it."""
    from src.api.dependencies import get_db
    from src.services.applications import ready, spine

    async with authenticated_async_context() as client:
        app_id, cv_id = await _app(client, fixture_user_id, [STALE_RESUME, USERS_GAP])
        resume = {"question": "Resume", "answer": "Ada_Lovelace_CV.pdf", "source": "profile"}
        gap = {"question": GAP_Q, "answer": "Yes, a 2024 sabbatical", "source": "approved"}
        was = await _fill(client, app_id, [NOTICE, resume, gap])
        now = await _fill(client, app_id, [NOTICE, {**resume, "answer": f"CV v1 (artifact {cv_id})"}, gap])
        text = await _fill(client, app_id, [NOTICE, resume, {**gap, "answer": "No"}])
        db = await get_db()
        row = await spine.get_owned_application(db, fixture_user_id, app_id)
        assert await ready.refill_keeps_answers(db, fixture_user_id, row, was, now) is True
        assert await ready.refill_keeps_answers(db, fixture_user_id, row, was, text) is False
