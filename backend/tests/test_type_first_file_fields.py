"""type_first never demands typed words in a CV file field (production, 2026-10-30 20:26 Apply run).

loveholidays (application 329) and Marlabs (327): the tailored CV was uploaded and the form filled and
swept, then check_submit held both on ``type_first_pending`` because an approved saved answer
``{"Resume": "CV v1, artifact 128"}`` (an older upload) was matched to the form's file field "Resume",
and the lock demanded those words be typed into a file input. A file field is not typed text; the file
sent is governed by the CV checks. The lock still holds every TEXT field the user changed (owner rule
2026-10-19). Real HTTP doors, VALUE asserts (rule #21). Helpers are copied, never imported.
"""
from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from src.services.profile.models import AssistantSettings, CVData, UserPreferences, UserProfile

CV_TEXT = "Ada Lovelace\nSenior data engineer\nPython, dbt, Snowflake."
FORM = "https://jobs.ashbyhq.example/loveholidays/apply"
NOTICE = {"question": "Preferred start date", "answer": "1 month", "source": "profile"}
# What the assistant reports for the file field: the tailored CV it uploaded for THIS application.
RESUME_FILE = {"question": "Resume", "answer": "Ada_Lovelace_CV_loveholidays.pdf", "source": "profile"}
# The stale approved answer from production: a label for an older upload, not typable text.
STALE_RESUME = {"question": "Resume", "answer": "CV v1, artifact 128", "approved": True}
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


async def _event(client: AsyncClient, app_id: int, event_type: str, payload: dict[str, Any]) -> int:
    resp = await client.post(f"/api/applications/{app_id}/events", json={"event_type": event_type, "payload": payload})
    assert resp.status_code == 201, resp.text
    return int(resp.json()["event_id"])


async def _fill(client: AsyncClient, app_id: int, answers: list[dict[str, Any]]) -> int:
    """The assistant fills the form and sweeps it, reporting exactly what the form holds."""
    fill = await _event(client, app_id, "form_filled", {"form_url": FORM, "fields_count": len(answers), "answers": answers})
    await _event(client, app_id, "form_swept", {"fields_checked": len(answers), "fixed": 0, "answers": answers})
    return fill


async def _check(client: AsyncClient, app_id: int) -> dict[str, Any]:
    resp = await client.get(f"/api/applications/{app_id}/submit-check", params={"form_url": FORM})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _approved_app(
    client: AsyncClient, user_id: str, saved: list[dict[str, Any]], answers: list[dict[str, Any]]
) -> int:
    """A brought job with a tailored CV, the user's saved answers, a filled + swept form and the web yes."""
    _seed_profile(user_id)
    edit = {"path": "user_info.answers", "value": saved}
    assert (await client.patch("/api/profile", json={"edits": [edit]})).status_code == 200
    resp = await client.post("/api/jobs/bring", json=_AD)
    assert resp.status_code == 200, resp.text
    app_id = int(resp.json()["application_id"])
    fit = {"score": 80, "verdict": "ok", "score_source": "full_ad"}
    assert (await client.put(f"/api/applications/{app_id}/fit", json=fit)).status_code == 200
    resp = await client.post(f"/api/applications/{app_id}/artifacts", json={"kind": "cv", "text": CV_TEXT})
    assert resp.status_code == 201, resp.text
    fill = await _fill(client, app_id, answers)
    yes = await client.post(f"/api/applications/{app_id}/send/approve", params={"form_filled_event_id": fill})
    assert yes.status_code == 201, yes.text
    return app_id


@pytest.mark.asyncio
async def test_a_saved_resume_answer_never_holds_the_cv_file_field(authenticated_async_context, fixture_user_id):
    """Production 2026-10-30 (applications 327, 329): the saved "Resume" label is not typed into the
    file field, so it is not on type_first and the user's yes goes through."""
    async with authenticated_async_context() as client:
        app_id = await _approved_app(client, fixture_user_id, [STALE_RESUME], [NOTICE, RESUME_FILE])
        verdict = await _check(client, app_id)
        assert verdict["type_first"] == [], verdict
        assert (verdict["decision"], verdict["reason"]) == ("submit", "user_approved"), verdict


@pytest.mark.asyncio
async def test_a_text_field_the_user_changed_still_holds_the_submit(authenticated_async_context, fixture_user_id):
    """The owner's lock (2026-10-19) is intact: a TEXT answer the user changed - even one whose question
    mentions the CV - holds the submit until a sweep reports the user's words; the file field beside it
    is never on the list."""
    gap_q = "Is there a gap in your CV you would like to explain?"
    typed_gap = {"question": gap_q, "answer": "No", "source": "profile"}
    users_gap = {"question": gap_q, "answer": "Yes, a 2024 sabbatical", "approved": True}
    async with authenticated_async_context() as client:
        app_id = await _approved_app(
            client, fixture_user_id, [STALE_RESUME, users_gap], [NOTICE, RESUME_FILE, typed_gap]
        )
        locked = await _check(client, app_id)
        assert (locked["decision"], locked["reason"]) == ("fill", "type_first_pending"), locked
        assert locked["type_first"] == [{"question": gap_q, "answer": "Yes, a 2024 sabbatical"}], locked
        # The assistant types the user's words and sweeps: the lock lets go, the yes stands.
        swept = [NOTICE, RESUME_FILE, {**typed_gap, "answer": "Yes, a 2024 sabbatical"}]
        await _event(client, app_id, "form_swept", {"fields_checked": 3, "fixed": 1, "answers": swept})
        verdict = await _check(client, app_id)
        assert (verdict["decision"], verdict["reason"], verdict["type_first"]) == ("submit", "user_approved", [])


def test_only_a_cv_or_letter_question_holding_a_file_is_a_file_answer() -> None:
    """The pure test: the question must ask for the CV or a cover letter AND the field must hold a file
    (a bare file name, check_form's artifact label, or check_form's ``artifact.*`` key); a URL, a typed
    sentence or a non-document question stays typed text the lock holds."""
    from src.services.applications.ready import is_file_answer

    assert is_file_answer({"question": "Resume", "answer": "Ada_CV.pdf"}, "CV v1, artifact 128")
    assert is_file_answer({"question": "Resume/CV", "answer": ""}, "CV v1, artifact 128")
    assert is_file_answer({"question": "CV", "answer": "C:\\fakepath\\Ada CV.docx"}, "something else")
    assert is_file_answer({"question": "Cover letter", "answer": "CV v3 (artifact 140)"}, "Cover letter v1")
    assert is_file_answer({"question": "Attach", "answer": "x", "key": "artifact.cv"}, "y")
    assert not is_file_answer({"question": "Resume", "answer": "I led a team of 5"}, "I led a team of 6")
    assert not is_file_answer({"question": "Link to your CV", "answer": "https://a.example/cv.pdf"},
                              "https://a.example/cv2.pdf")
    assert not is_file_answer({"question": "Is there a gap in your CV?", "answer": "No"}, "Yes")
    assert not is_file_answer({"question": "Portfolio", "answer": "work.pdf"}, "artifact 12 v1")
