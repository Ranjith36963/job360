"""Verify / Edit one answer on a Ready-to-send card (owner decision 2026-10-18).

Real HTTP doors, real rows, VALUE asserts (rule #21): the user's word lands in
memory, the card reads it back, and an edited answer holds the Send.
"""
from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

FORM = "https://careers.example/apply/9"
_AD = {
    "title": "Data Engineer", "company": "Certa", "location": "Remote", "country": "IN", "remote": True,
    "apply_url": "https://certa.example/careers/9", "description": "Build the pipelines.",
}
NAME = {"question": "Name", "answer": "Ada Lovelace", "source": "memory", "key": "contact.legal_last_name"}
GUESS = {"question": "Job requirement to be located in India - do you meet it?", "answer": "Yes", "source": "guessed"}
GENDER = {"question": "Gender (voluntary)", "answer": "Female", "source": "memory", "key": "equality.gender"}
VISA = {"question": "Require sponsorship?", "answer": "No", "source": "memory", "key": "right_to_work.IN.needs_sponsorship"}


def _e(path: str, value: Any) -> dict[str, Any]:
    return {"path": path, "value": value}


async def _bring(client: AsyncClient) -> int:
    resp = await client.post("/api/jobs/bring", json=_AD)
    assert resp.status_code == 200, resp.text
    app_id = int(resp.json()["application_id"])
    cv = {"kind": "cv", "text": "Ada Lovelace\nData engineer."}
    assert (await client.post(f"/api/applications/{app_id}/artifacts", json=cv)).status_code == 201
    return app_id


async def _fill(client: AsyncClient, app_id: int, answers: list[dict[str, Any]]) -> None:
    payload = {"form_url": FORM, "fields_count": len(answers), "answers": answers}
    resp = await client.post(f"/api/applications/{app_id}/events", json={"event_type": "form_filled", "payload": payload})
    assert resp.status_code == 201, resp.text


async def _sweep(client: AsyncClient, app_id: int, answer: str) -> None:
    """The assistant re-reads the one field and reports what the form now holds."""
    payload = {"fields_checked": 1, "fixed": 1,
               "answers": [{"question": GUESS["question"], "answer": answer, "source": "approved"}]}
    resp = await client.post(f"/api/applications/{app_id}/events", json={"event_type": "form_swept", "payload": payload})
    assert resp.status_code == 201, resp.text


async def _card(client: AsyncClient, app_id: int) -> dict[str, Any]:
    resp = await client.get("/api/ready-to-send", params={"application_id": app_id})
    assert resp.status_code == 200, resp.text
    return resp.json()["items"][0]


async def _confirm(client: AsyncClient, app_id: int, index: int, fill: int, answer: str | None = None):
    body: dict[str, Any] = {"form_filled_event_id": fill}
    if answer is not None:
        body["answer"] = answer
    return await client.post(f"/api/applications/{app_id}/answers/{index}/confirm", json=body)


def _approved(user_id: str) -> list[dict[str, Any]]:
    from src.services.profile.storage import load_profile

    profile = load_profile(user_id)  # None = nothing was ever written
    return [] if profile is None else [r for r in profile.user_info.answers or [] if r.get("approved") is True]


def _codes(card: dict[str, Any]) -> list[str]:
    """The answer flags only (the job-country "missing" facts are another test's business)."""
    return [f["code"] for f in card["flags"] if f["code"] != "missing"]


@pytest.mark.asyncio
async def test_verify_a_guess_saves_it_as_the_users_answer_and_the_next_fill_reads_verified(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        await _fill(client, app_id, [GUESS])
        card = await _card(client, app_id)
        assert _codes(card) == ["guessed"] and card["answers"][0]["verified"] is False
        resp = await _confirm(client, app_id, 0, card["form_filled_event_id"])
        assert resp.status_code == 200, resp.text
        after = resp.json()
        assert after["answers"][0]["verified"] is True and after["answers"][0]["changed_to"] is None
        assert _codes(after) == [], "a verified guess is no longer a guess"
        assert [(r["question"], r["answer"]) for r in _approved(fixture_user_id)] == [(GUESS["question"], "Yes")]
        await _fill(client, app_id, [GUESS])  # the assistant fills again: still the user's word
        assert (await _card(client, app_id))["answers"][0]["verified"] is True


@pytest.mark.asyncio
async def test_the_submit_gate_reads_an_unverified_guess_the_same_way_the_card_does(
    authenticated_async_context, fixture_user_id
):
    """Owner rule 2026-10-19: a guess is not verified, so check_submit must ask. The
    gate fact (``kit.gate_facts`` -> ``ready.has_unverified_guess``) and the card's
    ``guessed`` flag read the same rows, so a Verify clears both together."""
    from src.api import dependencies as api_deps
    from src.services.applications import kit, spine

    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        db = await api_deps.get_db()

        async def gate_says_guessed() -> bool:
            row = await spine.get_owned_application(db, fixture_user_id, app_id)
            facts = await kit.gate_facts(db, fixture_user_id, row, email_verified=True)
            return facts.guessed_unverified

        await _fill(client, app_id, [NAME])
        assert await gate_says_guessed() is False, "no guess, nothing to ask"
        await _fill(client, app_id, [NAME, GUESS])
        card = await _card(client, app_id)
        assert "guessed" in _codes(card) and await gate_says_guessed() is True
        assert (await _confirm(client, app_id, 1, card["form_filled_event_id"])).status_code == 200
        await _fill(client, app_id, [NAME, GUESS])  # the next fill types the now-verified answer
        assert await gate_says_guessed() is False, "the user's Verify clears the gate too"


@pytest.mark.asyncio
async def test_an_edited_guess_is_the_users_word_and_the_type_first_lock_holds_it(
    authenticated_async_context, fixture_user_id
):
    """Edit turns the card's flag from ``guessed`` to ``changed``: the answer is now the user's
    word, so it is no longer an unverified guess - and the type-first lock (not the guess gate)
    holds every submit until a sweep shows the form holds that word."""
    from src.api import dependencies as api_deps
    from src.services.applications import kit, ready, spine
    from src.services.profile import assistant_settings as settings_rules

    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        db = await api_deps.get_db()
        await _fill(client, app_id, [GUESS])
        card = await _card(client, app_id)
        assert (await _confirm(client, app_id, 0, card["form_filled_event_id"], answer="No")).status_code == 200
        assert "changed" in _codes(await _card(client, app_id))
        row = await spine.get_owned_application(db, fixture_user_id, app_id)
        facts = await kit.gate_facts(db, fixture_user_id, row, email_verified=True)
        assert facts.guessed_unverified is False, "an edited guess is the user's word, not a guess"
        pending = await ready.changed_answers(db, fixture_user_id, row)
        assert pending == [{"question": GUESS["question"], "answer": "No"}]
        held = ready.type_first_lock(settings_rules.SubmitDecision("submit", "user_approved", ""), pending)
        assert (held.decision, held.reason) == ("stop", "type_first_pending")


@pytest.mark.asyncio
async def test_edit_is_verified_send_stays_open_and_submit_check_hands_over_the_users_word(
    authenticated_async_context, fixture_user_id
):
    word = "No - I live in London, open to relocating"
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        await _fill(client, app_id, [GUESS])
        fill = (await _card(client, app_id))["form_filled_event_id"]
        resp = await _confirm(client, app_id, 0, fill, word)
        assert resp.status_code == 200, resp.text
        card = resp.json()
        assert (card["answers"][0]["changed_to"], card["answers"][0]["verified"]) == (word, True)
        assert _codes(card) == ["changed"], "held from Send all, never from the user's own Send"
        assert [r["answer"] for r in _approved(fixture_user_id)] == [word]
        # The user's Send is the yes: no refill needed first.
        approve = await client.post(f"/api/applications/{app_id}/send/approve", params={"form_filled_event_id": fill})
        assert approve.status_code == 201, approve.text
        # ...and the gate tells the assistant what to type before it submits, after the yes too.
        check = await client.get(f"/api/applications/{app_id}/submit-check", params={"form_url": FORM})
        assert check.status_code == 200, check.text
        assert check.json()["type_first"] == [{"question": GUESS["question"], "answer": word}]
        # The LOCK: even after the yes, no submit until a sweep proves the form holds the user's words.
        # Not swept yet: an earlier stop (form_not_swept) answers first - either way, no submit.
        assert (check.json()["decision"], check.json()["reason"]) == ("fill", "form_not_swept"), check.json()
        # Negative control: a sweep that still reads the OLD answer does not open the lock.
        await _sweep(client, app_id, GUESS["answer"])
        check = await client.get(f"/api/applications/{app_id}/submit-check", params={"form_url": FORM})
        assert (check.json()["decision"], check.json()["reason"]) == ("fill", "type_first_pending"), check.json()
        assert check.json()["type_first"] == [{"question": GUESS["question"], "answer": word}], check.json()
        # The assistant types the user's word and sweeps: the lock opens (the yes still stands).
        await _sweep(client, app_id, word)
        check = await client.get(f"/api/applications/{app_id}/submit-check", params={"form_url": FORM})
        assert check.json()["type_first"] == [] and check.json()["reason"] != "type_first_pending", check.json()
        # A newer sweep of ANOTHER page (it does not hold this answer) must not re-lock it (CodeRabbit #897).
        other = {"fields_checked": 1, "fixed": 0, "page": 2, "pages_total": 2,
                 "answers": [{"question": "Notice period", "answer": "1 month", "source": "memory"}]}
        resp = await client.post(f"/api/applications/{app_id}/events", json={"event_type": "form_swept", "payload": other})
        assert resp.status_code == 201, resp.text
        check = await client.get(f"/api/applications/{app_id}/submit-check", params={"form_url": FORM})
        assert check.json()["type_first"] == [], check.json()
        # Once the assistant fills with the user's word, nothing is left to type.
        await _fill(client, app_id, [{**GUESS, "answer": word, "source": "approved"}])
        card = await _card(client, app_id)
        assert card["answers"][0]["verified"] is True and _codes(card) == []
        check = await client.get(f"/api/applications/{app_id}/submit-check", params={"form_url": FORM})
        assert check.json()["type_first"] == []


@pytest.mark.asyncio
async def test_a_filled_topic_wins_over_a_same_worded_answer_saved_for_another_country(
    authenticated_async_context, fixture_user_id
):
    """Found by the 2026-10-20 simulation (application 107): the user saved "Current location"
    = Bangalore on an India form and the international topic = London. A UK fill that typed the
    topic's London is not "changed" to Bangalore; with no topic named, the wording still matches."""
    from src.api import dependencies as api_deps
    from src.services.applications import ready, spine

    answers = [
        {"question": "Current location", "answer": "Bangalore, India", "approved": True},
        {"question": "Current location / city on international (non-India) job forms",
         "answer": "London, United Kingdom", "approved": True, "topic": "location.current.intl"},
    ]
    async with authenticated_async_context() as client:
        resp = await client.patch("/api/profile", json={"edits": [_e("user_info.answers", answers)]})
        assert resp.status_code == 200, resp.text
        app_id = await _bring(client)
        db = await api_deps.get_db()
        row = await spine.get_owned_application(db, fixture_user_id, app_id)
        typed = {"question": "Current location", "answer": "London, United Kingdom", "source": "approved",
                 "key": "location.current.intl"}
        await _fill(client, app_id, [typed])
        assert await ready.changed_answers(db, fixture_user_id, row) == []
        # No topic named: the same-worded saved answer is the user's word, as before.
        await _fill(client, app_id, [{k: v for k, v in typed.items() if k != "key"}])
        assert await ready.changed_answers(db, fixture_user_id, row) == [
            {"question": "Current location", "answer": "Bangalore, India"}
        ]


def test_the_lock_turns_submit_and_ask_into_stop_and_keeps_an_earlier_stop():
    from src.services.applications.ready import type_first_lock
    from src.services.profile.assistant_settings import REASONS, SubmitDecision

    pending = [{"question": "Located in India?", "answer": "Yes"}]
    yes = SubmitDecision("submit", "user_approved", "The user said yes.")
    ask = SubmitDecision("ask", "practice_run", "Ask first.")
    stop = SubmitDecision("stop", "form_not_swept", "Sweep first.")
    for verdict in (yes, ask):
        locked = type_first_lock(verdict, pending)
        assert (locked.decision, locked.reason) == ("stop", "type_first_pending")
        assert "form_swept" in locked.detail and "1 answer" in locked.detail
    assert type_first_lock(stop, pending) is stop, "an earlier stop is no less a stop"
    assert type_first_lock(yes, []) is yes, "nothing left to type: the yes goes through"
    assert "type_first_pending" in REASONS


@pytest.mark.asyncio
async def test_sensitive_memory_goes_to_its_field_never_the_approved_list(authenticated_async_context, fixture_user_id):
    from src.services.profile.storage import load_profile

    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        await _fill(client, app_id, [GENDER])
        card = await _card(client, app_id)
        assert _codes(card) == ["sensitive", "not_in_memory"]
        # G023 (slice 11): not in memory, so the fill kept only its hash - Verify cannot see it, Edit types it.
        assert (card["answers"][0]["redacted"], card["answers"][0]["answer"]) == (True, "")
        refused = await _confirm(client, app_id, 0, card["form_filled_event_id"])
        assert refused.status_code == 422 and "use Edit" in refused.json()["detail"]
        resp = await _confirm(client, app_id, 0, card["form_filled_event_id"], "Female")
        assert resp.status_code == 200, resp.text
        assert resp.json()["answers"][0]["verified"] is True and _codes(resp.json()) == []
        profile = load_profile(fixture_user_id)
        assert profile is not None and profile.user_info.equality["gender"] == "Female"
        assert _approved(fixture_user_id) == [], "a sensitive answer never rides in the approved list"
        resp = await _confirm(client, app_id, 0, card["form_filled_event_id"], "Prefer not to say")
        assert resp.json()["answers"][0]["changed_to"] == "Prefer not to say"
        profile = load_profile(fixture_user_id)
        assert profile is not None and profile.user_info.equality["gender"] == "Prefer not to say"


@pytest.mark.asyncio
async def test_verify_memory_an_assistant_saved_makes_it_the_users_own(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        token = (await client.post("/api/tokens", json={"name": "claude-code"})).json()["token"]
        app_id = await _bring(client)
    from src.api.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    ) as agent:
        resp = await agent.patch("/api/profile", json={"edits": [
            {**_e("user_info.right_to_work", {"countries": [{"country": "IN", "needs_sponsorship": False}]}),
             "remember": True},
            _e("user_info.contact", {"legal_last_name": "Lovelace"}),
        ]})
        assert resp.status_code == 200, resp.text
        await _fill(agent, app_id, [VISA, NAME])
        fill = (await _card(agent, app_id))["form_filled_event_id"]
        refused = await _confirm(agent, app_id, 0, fill)
        assert refused.status_code in (401, 403), "an assistant can never vouch for its own answer"
    async with authenticated_async_context() as client:
        card = await _card(client, app_id)
        assert [a["verified"] for a in card["answers"]] == [False, False]
        visa = (await _confirm(client, app_id, 0, fill)).json()["answers"][0]
        assert (visa["verified"], visa["saved_by"]) == (True, "web")
        assert _approved(fixture_user_id) == [], "visa status stays in memory only"
        # Name was typed from the wrong field: verifying it saves the exact wording the user stands behind.
        name = (await _confirm(client, app_id, 1, fill)).json()["answers"][1]
        assert name["verified"] is True
        assert [(r["question"], r["answer"]) for r in _approved(fixture_user_id)] == [("Name", "Ada Lovelace")]


@pytest.mark.asyncio
async def test_refusals_write_nothing(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        await _fill(client, app_id, [GUESS, {"question": "Middle name", "answer": "", "source": "profile"}])
        fill = (await _card(client, app_id))["form_filled_event_id"]
        assert (await _confirm(client, app_id, 0, fill + 999)).status_code == 409
        assert (await _confirm(client, app_id, 7, fill)).status_code == 422
        assert (await _confirm(client, app_id, 1, fill)).status_code == 422, "nothing to verify on a blank"
        assert (await _confirm(client, app_id, 0, fill, "   ")).status_code == 422
        assert (await _confirm(client, 999999, 0, fill)).status_code == 404
        assert _approved(fixture_user_id) == []


def test_verify_keeps_a_same_worded_answer_saved_for_another_topic():
    """Verifying "Notice period?" on an India form must not delete the UK answer the user saved
    under the same wording; only the answer on the same topic (or an untopiced one) is replaced."""
    from types import SimpleNamespace

    from src.services.applications.ready import _with_approved

    uk = {"question": "Notice period?", "answer": "Immediately", "approved": True, "topic": "notice_period.GB.text"}
    old = {"question": "Notice period?", "answer": "1 month", "approved": True, "topic": "notice_period.IN.text"}
    plain = {"question": "Notice period?", "answer": "2 weeks", "approved": True}
    profile = SimpleNamespace(user_info=SimpleNamespace(answers=[uk, old, plain]))

    out = _with_approved(profile, "Notice period?", "30 days", None, "notice_period.IN.text")
    assert uk in out and old not in out and plain in out
    assert out[-1] == {"question": "Notice period?", "answer": "30 days", "approved": True,
                       "topic": "notice_period.IN.text"}
    # An answer with no topic replaces only the untopiced one, never a topic'd answer.
    out = _with_approved(profile, "Notice period?", "30 days", None)
    assert uk in out and old in out and plain not in out
    assert out[-1] == {"question": "Notice period?", "answer": "30 days", "approved": True}


# -- Whose act made an answer differ (application 122, 2026-10-25) -----------------------------
# The owner was asleep; the Dwelly card still said "You changed an answer: Phone". Only a web save
# AFTER the fill is the owner's edit; memory saved before it is a saved answer the assistant types.


def _changed(card: dict[str, Any]) -> list[tuple[str, Any, Any]]:
    return [(a["question"], a["changed_to"], a["changed_reason"]) for a in card["answers"]]


def _changed_texts(card: dict[str, Any]) -> list[str]:
    return [f["text"] for f in card["flags"] if f["code"] == "changed"]


@pytest.mark.asyncio
async def test_an_edit_after_the_fill_is_an_owner_edit(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        await _fill(client, app_id, [GUESS])
        fill = (await _card(client, app_id))["form_filled_event_id"]
        card = (await _confirm(client, app_id, 0, fill, "No")).json()
        assert _changed(card) == [(GUESS["question"], "No", "owner_edit")]
        assert _changed_texts(card) == [f"You changed an answer: {GUESS['question']} — your assistant types "
                                        "your answer before it sends"]


@pytest.mark.asyncio
async def test_an_approved_answer_saved_before_the_fill_is_a_saved_answer_not_an_edit(
    authenticated_async_context, fixture_user_id
):
    """The Needs-you approve (PR #1219) saves the owner's yes from their web session BEFORE the
    assistant fills; the fill then typed something else. The gate still hands over the saved word."""
    from src.api import dependencies as api_deps
    from src.services.applications import ready, spine

    rtw = {"question": "Location and right to work", "answer": "Yes", "approved": True,
           "topic": "right_to_work.GB.yes_no"}
    async with authenticated_async_context() as client:
        resp = await client.patch("/api/profile", json={"edits": [_e("user_info.answers", [rtw])]})
        assert resp.status_code == 200, resp.text
        app_id = await _bring(client)
        await _fill(client, app_id, [{"question": rtw["question"], "answer": "", "source": "approved",
                                      "key": rtw["topic"]}])
        card = await _card(client, app_id)
        assert _changed(card) == [(rtw["question"], "Yes", "saved_answer")]
        assert _changed_texts(card) == ["Your saved answer will be typed: Location and right to work"]
        assert not any("You changed" in f["text"] for f in card["flags"])
        db = await api_deps.get_db()
        row = await spine.get_owned_application(db, fixture_user_id, app_id)
        assert await ready.changed_answers(db, fixture_user_id, row) == [
            {"question": rtw["question"], "answer": "Yes"}
        ], "the gate still types the saved word"


@pytest.mark.asyncio
async def test_an_answer_approved_for_this_employer_says_so(authenticated_async_context, fixture_user_id):
    why = {"question": "Why Certa?", "answer": "I like the pipelines.", "approved": True, "company": "Certa"}
    async with authenticated_async_context() as client:
        resp = await client.patch("/api/profile", json={"edits": [_e("user_info.answers", [why])]})
        assert resp.status_code == 200, resp.text
        app_id = await _bring(client)
        await _fill(client, app_id, [{"question": "Why Certa?", "answer": "Because.", "source": "written"}])
        card = await _card(client, app_id)
        assert _changed(card) == [("Why Certa?", "I like the pipelines.", "approved_for_employer")]
        assert _changed_texts(card) == ["Your approved answer for this employer will be typed: Why Certa?"]


@pytest.mark.asyncio
async def test_a_phone_saved_on_the_web_before_the_fill_is_a_saved_answer(
    authenticated_async_context, fixture_user_id
):
    """Application 122's Phone row: the contact block's newest row is the owner's web save, made long
    before the fill, and the assistant typed the number another way."""
    phone = {"question": "Phone", "answer": "+44 7442 564327", "source": "memory", "key": "contact.phone"}
    async with authenticated_async_context() as client:
        resp = await client.patch("/api/profile", json={"edits": [_e("user_info.contact", {"phone": "+447442564327"})]})
        assert resp.status_code == 200, resp.text
        app_id = await _bring(client)
        await _fill(client, app_id, [phone])
        card = await _card(client, app_id)
        assert _changed(card) == [("Phone", "+447442564327", "saved_answer")]
        assert _changed_texts(card) == ["Your saved answer will be typed: Phone"]
        # The owner edits it on the card AFTER the fill: now it is their change.
        fill = card["form_filled_event_id"]
        card = (await _confirm(client, app_id, 0, fill, "+44 7700 900123")).json()
        assert _changed(card) == [("Phone", "+44 7700 900123", "owner_edit")]
        assert _changed_texts(card)[0].startswith("You changed an answer: Phone")
