"""Owner rule 2026-10-26: "Don't leave any questions blank" - a strict gate at submit.

Production showed the hole: application 138 (Alaan) went out with Pronouns blank although the user had
answered "he/him" in the job's ask - an ask that HOLDS a blank used to clear ``blank_unasked``. Now
check_submit asks ``answer_blank`` while the newest fill still has an empty field the user did not answer
"Leave blank" (asked or not), and ``answer_placeholder`` for stand-in text typed into the form. An ask row
the assistant cannot even guess is source ``not_known`` with an empty answer. Helpers are copied, never
imported from another test module.
"""
from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from src.services.profile.models import AssistantSettings, CVData, UserPreferences, UserProfile

FORM = "https://ats.rippling.example/alaan/jobs/1/apply"


def _seed_profile(user_id: str) -> None:
    from src.services.profile.storage import save_assistant_settings, save_profile

    save_profile(
        UserProfile(
            cv_data=CVData(raw_text="Python engineer.", name="Ada Lovelace", location="Dubai, AE"),
            preferences=UserPreferences(target_job_titles=["Engineer"]),
        ),
        user_id, source_action="cv_upload",
    )
    save_assistant_settings(
        user_id, AssistantSettings(apply_min_score=50, above_line="auto", below_line="auto"), "test_seed"
    )


async def _bring(client: AsyncClient) -> int:
    resp = await client.post("/api/jobs/bring", json={
        "title": "Software Engineer", "company": "Alaan", "location": "Dubai", "country": "AE", "remote": False,
        "apply_url": FORM, "description": "Build finance automation. Python.",
    })
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _ok(client: AsyncClient, app_id: int, event_type: str, payload: dict[str, Any]) -> None:
    resp = await client.post(f"/api/applications/{app_id}/events", json={"event_type": event_type, "payload": payload})
    assert resp.status_code == 201, resp.text


async def _fill(
    client: AsyncClient, app_id: int, skipped: list[str], answers: list[dict[str, str]] | None = None
) -> None:
    """A complete, swept form: 4 typed fields plus the named blanks (the counts match check_form)."""
    total = 4 + len(skipped)
    await _ok(client, app_id, "form_checked", {"questions_count": total, "answered": 4, "missing": len(skipped),
                                                "missing_required": 0})
    payload: dict[str, Any] = {"form_url": FORM, "fields_count": 4, "fields_skipped": skipped}
    if answers is not None:
        payload["answers"] = answers
    await _ok(client, app_id, "form_filled", payload)
    await _ok(client, app_id, "form_swept", {"fields_checked": 4, "fixed": 0})


async def _verdict(client: AsyncClient, app_id: int) -> dict[str, Any]:
    resp = await client.get(f"/api/applications/{app_id}/submit-check", params={"form_url": FORM})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _ask(client: AsyncClient, app_id: int, rows: list[dict[str, Any]]) -> Any:
    return await client.post("/api/asks", json={"question": "Form answers for Alaan", "application_id": app_id,
                                                "kind": "question", "questions": rows})


PRONOUNS = {"key": "pronouns", "question": "Pronouns", "answer": "he/him", "source": "guess",
            "choices": ["he/him", "she/her", "Leave blank"]}


@pytest.mark.asyncio
async def test_an_open_ask_is_not_an_answer(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        await _fill(client, app_id, ["Pronouns"])
        assert (await _ask(client, app_id, [PRONOUNS])).status_code == 201
        v = await _verdict(client, app_id)
        assert (v["decision"], v["reason"]) == ("ask", "answer_blank") and "Pronouns" in v["detail"]


@pytest.mark.asyncio
async def test_an_answered_ask_must_be_typed_in(authenticated_async_context, fixture_user_id):
    """Alaan, 2026-10-26: the user said he/him, the form still had Pronouns blank, the job went out."""
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        await _fill(client, app_id, ["Pronouns"])
        ask = (await _ask(client, app_id, [PRONOUNS])).json()
        resp = await client.post(f"/api/asks/{ask['id']}/answer", json={"answers": {"pronouns": "he/him"}})
        assert resp.status_code == 200, resp.text
        assert (await _verdict(client, app_id))["reason"] == "answer_blank"
        await _fill(client, app_id, [])  # typed it in: the new fill skips nothing
        assert (await _verdict(client, app_id))["reason"] not in ("answer_blank", "blank_unasked")


@pytest.mark.asyncio
async def test_the_users_leave_blank_lets_a_field_stay_empty(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        await _fill(client, app_id, ["Pronouns"])
        ask = (await _ask(client, app_id, [PRONOUNS])).json()
        resp = await client.post(f"/api/asks/{ask['id']}/answer", json={"answers": {"pronouns": "Leave blank"}})
        assert resp.status_code == 200, resp.text
        assert (await _verdict(client, app_id))["reason"] not in ("answer_blank", "blank_unasked")


@pytest.mark.asyncio
async def test_a_typed_answer_with_no_text_is_blank(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        await _fill(client, app_id, [], [{"question": "Website", "answer": "", "source": "profile"}])
        v = await _verdict(client, app_id)
        assert (v["decision"], v["reason"]) == ("ask", "answer_blank") and "Website" in v["detail"]


@pytest.mark.asyncio
async def test_stand_in_text_in_the_form_is_never_sent(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        await _fill(client, app_id, [], [{"question": "Postcode", "answer": "unknown, owner to supply",
                                          "source": "profile"}])
        v = await _verdict(client, app_id)
        assert (v["decision"], v["reason"]) == ("ask", "answer_placeholder") and "Postcode" in v["detail"]


def test_a_yes_never_clears_the_strict_gate():
    from src.services.profile import assistant_settings as rules

    assert {"answer_blank", "answer_placeholder"} <= set(rules.REASONS)
    d = rules.answer_blank_decision(tuple(f"Q{i}" for i in range(10)))
    assert (d.decision, d.reason) == ("ask", "answer_blank") and "(and 2 more)" in d.detail
    d = rules.answer_placeholder_decision(("Postcode",))
    assert (d.decision, d.reason) == ("ask", "answer_placeholder") and "Postcode" in d.detail


@pytest.mark.parametrize("answer,flag", [
    ("TBD", True), ("unknown, owner to supply", True), ("Not found; owner to supply", True),
    ("Owner action needed", True), ("Dear [Company name] team", True), ("{{role}}", True),
    ("<insert answer>", True), ("Why us?", True), ("I don't know", True),
    ("N/A", False), ("No", False), ("he/him", False), ("I built [an] API", False), ("see [1]", False),
    ("<5 years experience>", False), ("", False),
])
def test_what_counts_as_stand_in_text(answer, flag):
    from src.services.applications.kit import is_placeholder

    assert is_placeholder("Why us?", answer) is flag


# ── Asks: "Not known" is an empty answer with source not_known; stand-in text is refused ──────────


@pytest.mark.asyncio
async def test_an_ask_row_the_assistant_cannot_guess_is_not_known(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        stand_in = await _ask(client, app_id, [{"key": "postcode", "question": "Postcode",
                                                "answer": "unknown, owner to supply", "source": "guess"}])
        assert stand_in.status_code == 422 and "not_known" in stand_in.text
        with_text = await _ask(client, app_id, [{"key": "postcode", "question": "Postcode", "answer": "E1",
                                                 "source": "not_known"}])
        assert with_text.status_code == 422
        made = await _ask(client, app_id, [{"key": "postcode", "question": "Postcode", "answer": "",
                                            "source": "not_known"}])
        assert made.status_code == 201, made.text
        row = made.json()["questions"][0]
        assert (row["key"], row["answer"], row["source"]) == ("postcode", "", "not_known")


def test_a_newer_not_known_row_never_drops_an_older_answer():
    from src.services.applications.asks import merge_questions

    older = [{"key": "postcode", "question": "Postcode", "answer": "E1 6AN", "source": "guess"}]
    newer = [{"key": "postcode", "question": "Postcode", "answer": "", "source": "not_known"}]
    assert merge_questions(older, newer) == older
