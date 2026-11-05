"""Memory first when a form is filled (owner 2026-11-05, plan S1).

A question in Memory's families (right to work, sponsorship, on-site, salary, notice, relocation, location,
equality, languages, contact) is answered from Memory: the job country's page, else its region's, else ANY's.
A saved answer in those families is never used, and saved answers that disagree no longer block it. Nothing
in Memory = a gap naming the page to fill. The user's Instructions come first in check_form's result.
Helpers are defined here, never imported.
"""
from __future__ import annotations

from typing import Any

import pytest

from src.services.applications import kit

SPONSOR_Q = "Will you now or in the future require visa sponsorship?"
US_AUTH_Q = "Are you legally authorized to work in the United States?"


def _profile(countries: list[dict[str, Any]], answers: list[dict[str, Any]], notes: Any = None) -> Any:
    from src.services.profile.models import UserInfo, UserPreferences, UserProfile

    return UserProfile(user_info=UserInfo(answers=answers, right_to_work={"countries": countries}),
                       preferences=UserPreferences(assistant_notes=notes or []))


def _saved(question: str, answer: str, topic: str, **extra: Any) -> dict[str, Any]:
    return {"question": question, "answer": answer, "approved": True, "topic": topic, **extra}


def _form(profile: Any, country: str, questions: list[dict[str, Any]]) -> dict[str, Any]:
    """check_form's pure core over a REAL kit, exactly as check_form builds it."""
    answers, _missing = kit.compute_answers(profile, [], country, None)
    return kit.form_check(answers, questions, records=profile.user_info.answers, country=country,
                          instructions=kit.user_instructions(profile, country))


def test_gb_sponsorship_comes_from_memory_and_the_disagreeing_saved_twins_never_block_it():
    twins = [_saved("Visa sponsorship (UK)", "Yes", "sponsorship.GB.yes_no"),
             _saved(SPONSOR_Q, "No", "sponsorship.gb.yes_no")]
    profile = _profile([{"country": "GB", "work_authorization": "citizen", "needs_sponsorship": False}], twins)
    out = _form(profile, "GB", [{"question": SPONSOR_Q}, {"question": "Visa sponsorship (UK)",
                                                          "key": "sponsorship.GB.yes_no"}])
    assert out["missing"] == []
    assert [(a["value"], a["source"], a["key"], a["matched_by"]) for a in out["answered"]] == [
        ("No", "memory", "right_to_work.GB.needs_sponsorship", "wording"),
        ("No", "memory", "right_to_work.GB.needs_sponsorship", "key")]
    answers, missing = kit.compute_answers(profile, [], "GB", None)
    assert answers["approved_text"] == []
    assert [m for m in missing if m["why"] == kit.AMBIGUOUS] == []


def test_us_work_authorisation_comes_from_north_america_and_the_saved_us_answer_is_ignored():
    saved = [_saved(US_AUTH_Q, "I have US work authorization", "right_to_work.US.authorized", country="US")]
    profile = _profile([{"country": "NORTH_AMERICA", "work_authorization": "needs_sponsorship",
                         "needs_sponsorship": True}], saved)
    out = _form(profile, "US", [{"question": US_AUTH_Q}, {"question": SPONSOR_Q}])
    assert out["missing"] == []
    assert [(a["key"], a["value"], a["source"]) for a in out["answered"]] == [
        ("right_to_work.US.work_authorization", "No", "memory"),
        ("right_to_work.US.needs_sponsorship", "Yes", "memory"),
    ]


def test_poland_is_answered_from_the_europe_page_field_by_field():
    europe = {"country": "EUROPE", "needs_sponsorship": True}
    for countries in ([europe], [europe, {"country": "PL", "onsite": "yes"}]):
        profile = _profile(countries, [_saved(SPONSOR_Q, "No", "sponsorship.PL.yes_no", country="PL")])
        out = _form(profile, "PL", [{"question": SPONSOR_Q}])
        assert [(a["key"], a["value"], a["source"]) for a in out["answered"]] == [
            ("right_to_work.PL.needs_sponsorship", "Yes", "memory")], countries
        items = kit.compute_answers(profile, [], "PL", None)[0]["right_to_work"]
        assert [i["applies_via"] for i in items if i["key"] == "right_to_work.PL.needs_sponsorship"] == ["EUROPE"]


def test_a_question_outside_memory_still_uses_the_saved_answer():
    essay = "Tell us about a project you are proud of."
    profile = _profile([{"country": "GB", "needs_sponsorship": False}], [_saved(essay, "The pipeline rebuild.",
                                                                                 "essay.proud_project")])
    out = _form(profile, "GB", [{"question": essay}])
    assert [(a["value"], a["source"], a["matched_by"]) for a in out["answered"]] == [
        ("The pipeline rebuild.", "approved_text", "wording")]


def test_nothing_in_memory_is_a_gap_naming_the_country_page_never_the_saved_answer():
    profile = _profile([{"country": "IN", "needs_sponsorship": False}],
                       [_saved(SPONSOR_Q, "No", "sponsorship.GB.yes_no", country="GB")])
    out = _form(profile, "GB", [{"question": SPONSOR_Q}])
    assert out["answered"] == []
    assert [m["why"] for m in out["missing"]] == [
        "right_to_work.GB.needs_sponsorship is not in Memory: ask the user once, then add it on your GB page "
        "(or its region's page); a saved form answer is not used"]


def test_the_users_instructions_come_first_and_win_over_memory():
    remote = "Remote anywhere: automatic yes, no visa needed."
    profile = _profile([{"country": "EUROPE", "needs_sponsorship": True, "instructions": [remote]}], [],
                       notes=["On-site: UK and India always yes."])
    out = _form(profile, "DE", [{"question": "Do you require visa sponsorship for this remote role?"}])
    assert list(out)[0] == "user_instructions"
    assert out["user_instructions"] == {
        "lines": ["On-site: UK and India always yes."],
        "country": {"for": "DE", "from": "EUROPE", "lines": [remote]},
    }
    assert out["next"].startswith(kit.FORM_NEXT_INSTRUCTIONS)
    assert [a["value"] for a in out["answered"]] == ["Yes"]  # Memory's value; the Instructions line overrides it


@pytest.mark.parametrize("question,expected", [
    ("Do you have the right to work in the UK?", "right_to_work.{cc}.work_authorization"),
    ("Visa sponsorship within 18 months (UK)", "right_to_work.{cc}.needs_sponsorship"),
    ("What is your notice period?", "logistics.notice_period"),
    ("Do you need a visa?", None),  # no single Memory fact: a gap, never a guess
    ("Are you authorized to work in the US and will you require sponsorship?", None),
])
def test_memory_question_maps_wording_to_one_memory_fact_or_none(question, expected):
    from src.services.profile.ask_once import memory_question

    assert memory_question(None, question) == (True, expected)


def test_the_ready_card_never_turns_a_memory_answer_into_a_saved_one():
    from src.services.applications import ready
    from src.services.profile.edits import WEB_ACTOR

    key = "right_to_work.GB.needs_sponsorship"
    profile = _profile([{"country": "GB", "needs_sponsorship": False}],
                       [_saved(SPONSOR_Q, "Yes", "sponsorship.GB.yes_no", country="GB")])
    have = {i["key"]: i for i in kit.compute_answers(profile, [], "GB", None)[0]["right_to_work"]}
    typed = {"question": SPONSOR_Q, "answer": "No", "key": key, "source": "memory"}
    saved = {"user_info.right_to_work": (WEB_ACTOR, "2026-11-01T09:00:00+00:00")}
    assert ready._user_check(typed, profile, saved, None, have, None, "GB")[:2] == (True, None)


# ── Plan S2 (owner 2026-11-05): no NEW saved answer for a Memory fact; Memory is set by the user in Job360 ──


@pytest.mark.parametrize("op", ["append", "upsert"])
def test_a_new_saved_answer_in_memorys_families_is_refused_naming_the_country_page(op):
    from src.services.profile import edits
    from src.services.profile.edits import ProfileEditError

    new = _saved("Do you need visa sponsorship?", "No", "sponsorship.GB.yes_no")
    with pytest.raises(ProfileEditError) as info:
        edits.resolve_ops([("user_info.answers", op, new, None)], {"user_info.answers": []})
    assert info.value.status_code == 422
    assert "user_info.right_to_work" in info.value.detail and "your GB page" in info.value.detail
    essay = _saved("Why do you want to join us?", "The mission.", "why_us")
    [(_, value)] = edits.resolve_ops([("user_info.answers", op, essay, None)], {"user_info.answers": []})
    assert value == [essay], "outside Memory: saved as before"


@pytest.mark.asyncio
async def test_update_profile_refuses_a_sponsorship_upsert_and_saves_nothing(
    authenticated_async_context, fixture_user_id
):
    from src.services.profile.edits import current_edit_id

    async with authenticated_async_context() as client:
        before = current_edit_id(fixture_user_id)
        resp = await client.patch("/api/profile", json={"edits": [{"path": "user_info.answers", "op": "upsert",
            "value": _saved("Do you need visa sponsorship?", "No", "sponsorship.GB.yes_no")}]})
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert "Nothing was saved." in detail and "user_info.right_to_work" in detail and "your GB page" in detail
        assert current_edit_id(fixture_user_id) == before, "nothing written"


# ── Plan S2b (owner 2026-11-05): Memory is filled once from chat, then only the user changes it in Job360 ──

RTW_PATH = "user_info.right_to_work"
GB = {"country": "GB", "work_authorization": "visa", "needs_sponsorship": False}


async def _token(client: Any) -> str:
    resp = await client.post("/api/tokens", json={"name": "claude-code"})
    assert resp.status_code == 201, resp.text
    return resp.json()["token"]


def _assistant(token: str) -> Any:
    from httpx import ASGITransport, AsyncClient

    from src.api.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"})


def _rtw_edit(*rows: dict[str, Any]) -> dict[str, Any]:
    return {"path": RTW_PATH, "value": {"countries": list(rows)}, "source": "user_said", "remember": True}


def _rows(user_id: str) -> dict[str, dict[str, Any]]:
    from src.services.profile.storage import load_profile

    countries = (load_profile(user_id).user_info.right_to_work or {}).get("countries") or []
    return {r["country"]: {k: v for k, v in r.items() if k != "set_by"} for r in countries}


async def _setup_gb(client: Any, user_id: str) -> str:
    """(a) A setup-like assistant write on an empty profile fills GB's right to work; returns the token."""
    token = await _token(client)
    async with _assistant(token) as agent:
        resp = await agent.patch("/api/profile", json={"edits": [_rtw_edit(GB)]})
    assert resp.status_code == 200, resp.text
    assert _rows(user_id) == {"GB": GB}
    return token


@pytest.mark.asyncio
async def test_setup_fills_an_empty_profile_then_a_changed_gb_fact_is_refused_and_nothing_saved(
    authenticated_async_context, fixture_user_id
):
    from src.services.profile.edits import current_edit_id

    async with authenticated_async_context() as client:
        token = await _setup_gb(client, fixture_user_id)
        before = current_edit_id(fixture_user_id)
        async with _assistant(token) as agent:  # (b) a later assistant write changing GB needs_sponsorship
            resp = await agent.patch("/api/profile", json={"edits": [_rtw_edit({**GB, "needs_sponsorship": True})]})
        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"] == (
            "user_info.right_to_work.countries GB needs_sponsorship is already set; the user changes it on their "
            "GB page in Job360. Only empty fields can be filled from chat. Nothing was saved."
        )
        assert current_edit_id(fixture_user_id) == before, "nothing written"
        assert _rows(fixture_user_id) == {"GB": GB}


@pytest.mark.asyncio
async def test_an_assistant_may_add_a_new_country_row_and_fill_an_empty_field(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        token = await _setup_gb(client, fixture_user_id)
        pl = {"country": "PL", "work_authorization": "needs_sponsorship", "needs_sponsorship": True}
        async with _assistant(token) as agent:  # (c) a PL row (no row yet) plus GB's empty remote_ok
            resp = await agent.patch("/api/profile", json={"edits": [_rtw_edit({**GB, "remote_ok": True}, pl)]})
        assert resp.status_code == 200, resp.text
        assert _rows(fixture_user_id) == {"GB": {**GB, "remote_ok": True}, "PL": pl}


@pytest.mark.asyncio
async def test_the_owners_web_edit_still_changes_gb_sponsorship(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        await _setup_gb(client, fixture_user_id)
        changed = {**GB, "work_authorization": "needs_sponsorship", "needs_sponsorship": True}
        resp = await client.patch("/api/profile", json={"edits": [{"path": RTW_PATH, "value": {"countries": [changed]}}]})
        assert resp.status_code == 200, resp.text  # (d) the session-cookie web actor
        assert _rows(fixture_user_id) == {"GB": changed}


@pytest.mark.asyncio
async def test_instructions_stay_writable_from_chat(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        token = await _setup_gb(client, fixture_user_id)
        lines = ["Hold the visa question until they ask."]
        async with _assistant(token) as agent:  # (e) the GB page's instructions, and the user's own rules (held for Confirm, as before)
            resp = await agent.patch("/api/profile", json={"edits": [_rtw_edit({**GB, "instructions": lines})]})
            assert resp.status_code == 200, resp.text
            notes = await agent.patch("/api/profile", json={"edits": [
                {"path": "preferences.assistant_notes", "value": ["Never apply on Fridays."], "source": "user_said"}]})
        assert notes.status_code == 200, notes.text
        assert _rows(fixture_user_id) == {"GB": {**GB, "instructions": lines}}


@pytest.mark.asyncio
async def test_a_mixed_edit_with_one_empty_and_one_set_field_saves_nothing(
    authenticated_async_context, fixture_user_id
):
    from src.services.profile.edits import current_edit_id

    async with authenticated_async_context() as client:
        token = await _setup_gb(client, fixture_user_id)
        before = current_edit_id(fixture_user_id)
        async with _assistant(token) as agent:  # (f) remote_ok is empty, work_authorization is set: all or nothing
            resp = await agent.patch("/api/profile", json={"edits": [
                _rtw_edit({**GB, "remote_ok": True, "work_authorization": "permanent_resident"}),
                {"path": "user_info.contact", "value": {"preferred_name": "Ada"}, "source": "user_said"}]})
        assert resp.status_code == 422, resp.text
        assert "GB work_authorization is already set" in resp.json()["detail"]
        assert current_edit_id(fixture_user_id) == before, "nothing written"
        assert _rows(fixture_user_id) == {"GB": GB}


@pytest.mark.parametrize(("path", "old", "new", "expected"), [
    ("user_info.contact", {"email": "a@b.co"}, {"email": "a@b.co", "phone": "+44 1"}, []),
    ("user_info.contact", {"email": "a@b.co"}, {"email": "c@d.co"}, [("user_info.contact email", None)]),
    ("user_info.equality", {"veteran": False}, {"veteran": True}, [("user_info.equality veteran", None)]),
    ("user_info.equality", {}, {"veteran": False}, []),
    ("user_info.languages", [{"language": "English", "level": "native"}],
     [{"language": "english", "level": "native"}, {"language": "Polish", "level": "basic"}], []),
    ("user_info.languages", [{"language": "Polish", "level": "basic"}], [],
     [("user_info.languages polish row", None)]),
    ("preferences.salary_by_country", [{"country": "GB", "min": 1, "max": 2, "currency": "GBP", "period": "year"}],
     [{"country": "GB", "min": 3, "max": 3, "currency": "GBP", "period": "year"}],
     [("preferences.salary_by_country GB min", "GB"), ("preferences.salary_by_country GB max", "GB")]),
    ("user_info.logistics", {"notice_period": "1 month"}, None, [("user_info.logistics notice_period", None)]),
    ("user_info.answers", [{"question": "q"}], [], []),
])
def test_overwrites_names_only_set_fields(path, old, new, expected):
    from src.services.profile import memory_fill

    assert memory_fill.overwrites(path, old, new) == expected
