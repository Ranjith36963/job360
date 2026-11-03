"""Country pages (owner 2026-11-02): "For every country there should be instructions."

Real HTTP doors, VALUE asserts (rule #21): the user's instructions per country, the page with each value's
source, an assistant-created country stored as a draft (guesses, never approved) until the user confirms
it on the web, the kit carrying the job country's instructions, and nothing shown for an unset place.
"""
from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

DE_LINES = ["hold the visa question until they ask", "open to on-site, not relocating for a contract"]
EU_LINES = ["mention the EU Blue Card only if asked"]
GB = {"country": "GB", "work_authorization": "citizen", "needs_sponsorship": False}
EUROPE = {"country": "EUROPE", "work_authorization": "needs_sponsorship", "needs_sponsorship": True,
          "instructions": EU_LINES}
ANY = {"country": "ANY", "remote_ok": True, "work_authorization": "needs_sponsorship", "needs_sponsorship": True}
SPONSOR_Q = {"question": "Will you need visa sponsorship?", "answer": "Yes", "source": "memory"}


def _rtw(*rows: dict[str, Any]) -> dict[str, Any]:
    return {"path": "user_info.right_to_work", "value": {"countries": list(rows)}}


async def _patch(client: AsyncClient, *edits: dict[str, Any]) -> dict[str, Any]:
    resp = await client.patch("/api/profile", json={"edits": list(edits)})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _bring(client: AsyncClient, country: str) -> int:
    ad = {"title": "Data Engineer", "company": f"Acme {country}", "location": "", "country": country,
          "apply_url": f"https://acme-{country.lower()}.example/careers/1", "description": "Build the pipelines."}
    resp = await client.post("/api/jobs/bring", json=ad)
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _kit(client: AsyncClient, app_id: int) -> dict[str, Any]:
    resp = await client.get(f"/api/applications/{app_id}/kit")
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _page(client: AsyncClient, place: str) -> dict[str, Any]:
    resp = await client.get(f"/api/profile/countries/{place}")
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _sensitive_unconfirmed(client: AsyncClient, user_id: str, app_id: int, key: str) -> bool:
    payload = {"form_url": "https://careers.example/apply/1", "fields_count": 1, "answers": [{**SPONSOR_Q, "key": key}]}
    resp = await client.post(f"/api/applications/{app_id}/events", json={"event_type": "form_filled", "payload": payload})
    assert resp.status_code == 201, resp.text
    from src.api import dependencies as api_deps
    from src.services.applications import kit, spine

    db = await api_deps.get_db()
    row = await spine.get_owned_application(db, user_id, app_id)
    return (await kit.gate_facts(db, user_id, row, email_verified=True)).sensitive_unconfirmed


def _rows(user_id: str) -> dict[str, dict[str, Any]]:
    from src.services.profile.storage import load_profile

    return {r["country"]: r for r in load_profile(user_id).user_info.right_to_work["countries"]}


@pytest.mark.asyncio
async def test_instructions_on_a_country_row_are_saved_and_read_back(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        await _patch(client, _rtw({"country": "DE", "instructions": [DE_LINES[0] + "\x07", "  ", DE_LINES[1]]}))
        assert _rows(fixture_user_id)["DE"]["instructions"] == DE_LINES, "control chars stripped, blanks dropped"
        too_many = await client.patch("/api/profile", json={"edits": [_rtw({"country": "DE", "instructions": ["x"] * 6})]})
        assert too_many.status_code == 422 and "5-line limit" in too_many.text
        too_long = await client.patch("/api/profile", json={"edits": [_rtw({"country": "DE", "instructions": ["y" * 201]})]})
        assert too_long.status_code == 422 and "200-character limit" in too_long.text
        forged = await client.patch("/api/profile", json={"edits": [_rtw({"country": "DE", "set_by": "web"})]})
        assert forged.status_code == 422, "the draft mark is Job360's, never the caller's"


@pytest.mark.asyncio
async def test_page_and_kit_carry_each_value_with_its_source(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        await _patch(
            client, _rtw(GB, EUROPE, ANY, {"country": "FR", "onsite": "yes", "instructions": ["French CV first"]}),
            {"path": "preferences.salary_by_country",
             "value": [{"country": "EUROPE", "min": 70000, "max": 90000, "currency": "EUR", "period": "year"}]},
            {"path": "user_info.logistics", "value": {"countries": [{"country": "DE", "willing_to_relocate": False}]}},
        )
        page = await _page(client, "Germany")
        assert page == {
            "country": "DE", "region_fallback": "EUROPE",
            "instructions": {"from": "EUROPE", "lines": EU_LINES},
            "right_to_work": {"from": "EUROPE", "value": {"work_authorization": "needs_sponsorship",
                                                          "needs_sponsorship": True}},
            "salary": {"from": "EUROPE", "value": {"min": 70000, "max": 90000, "currency": "EUR", "period": "year"}},
            "logistics": {"from": "DE", "value": {"willing_to_relocate": False}},
        }
        # A DE job with no DE row follows EUROPE's instructions; FR's own row wins over its region's.
        de_kit = await _kit(client, await _bring(client, "DE"))
        assert de_kit["country_instructions"] == {"country": "DE", "from": "EUROPE", "lines": EU_LINES}
        assert any(line.startswith("country_instructions:") for line in de_kit["instructions"])
        fr_kit = await _kit(client, await _bring(client, "FR"))
        assert fr_kit["country_instructions"] == {"country": "FR", "from": "FR", "lines": ["French CV first"]}


@pytest.mark.asyncio
async def test_an_assistant_created_country_is_a_draft_until_the_user_confirms_it(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        await _patch(client, _rtw(GB, ANY))
        token = (await client.post("/api/tokens", json={"name": "claude-code"})).json()["token"]
        ae_app, gb_app = await _bring(client, "AE"), await _bring(client, "GB")
    from src.api.main import app

    ae = {"country": "AE", "work_authorization": "needs_sponsorship", "needs_sponsorship": True, "set_by": "web"}
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    ) as agent:
        out = await _patch(agent, _rtw(GB, ANY, {k: v for k, v in ae.items() if k != "set_by"}))
        assert out["not_saved"] == [], "a drafts-only write remembers nothing new as the user's"
        rows = _rows(fixture_user_id)
        assert rows["AE"]["set_by"] == "assistant" and "set_by" not in rows["GB"]
        assert (await _page(agent, "AE"))["assistant_drafted"] is True
        kit = await _kit(agent, ae_app)
        states = {i["key"]: i["state"] for i in kit["answers"]["right_to_work"]}
        assert states["right_to_work.AE.needs_sponsorship"] == "sensitive", "a guess, never approved"
        assert await _sensitive_unconfirmed(agent, fixture_user_id, ae_app, "right_to_work.AE.needs_sponsorship")
        gb_kit = await _kit(agent, gb_app)
        assert {i["state"] for i in gb_kit["answers"]["right_to_work"]} == {"confirmed"}, "GB stays the user's"
        assert not await _sensitive_unconfirmed(agent, fixture_user_id, gb_app, "right_to_work.GB.needs_sponsorship")
    async with authenticated_async_context() as client:
        page = await _page(client, "AE")
        assert (page["assistant_drafted"], page["confirmed"]) == (True, False)
        assert page["to_confirm"] == ["right_to_work.AE.work_authorization", "right_to_work.AE.needs_sponsorship"]
        # Sent back unchanged with its mark (a GB edit): still a draft. The user's own edit of AE confirms it.
        drafted = _rows(fixture_user_id)["AE"]
        await _patch(client, _rtw({**GB, "remote_ok": True}, ANY, drafted))
        assert _rows(fixture_user_id)["AE"]["set_by"] == "assistant"
        await _patch(client, _rtw({**GB, "remote_ok": True}, ANY, {**drafted, "set_by": None, "needs_sponsorship": False,
                                                                     "work_authorization": "citizen"}))
        assert "set_by" not in _rows(fixture_user_id)["AE"]
        page = await _page(client, "AE")
        assert (page["assistant_drafted"], page["confirmed"]) == (False, True) and "to_confirm" not in page
        assert not await _sensitive_unconfirmed(client, fixture_user_id, ae_app, "right_to_work.AE.needs_sponsorship")


@pytest.mark.asyncio
async def test_a_drafted_citizen_row_is_not_a_work_country_until_the_web_confirms_it(
    authenticated_async_context, fixture_user_id
):
    """An assistant-drafted AE row (citizen, no sponsorship needed) is a guess: it stays out of the visa work
    countries, the visa badge and the search brief until the user confirms it."""
    from src.api import dependencies as api_deps
    from src.services.applications import visa
    from src.services.profile import edits as profile_edits
    from src.services.profile.search_brief import build_search_brief
    from src.services.profile.storage import load_profile

    ae = {"country": "AE", "work_authorization": "citizen", "needs_sponsorship": False, "remote_ok": True}
    async with authenticated_async_context() as client:
        await _patch(client, _rtw(GB))
        app_id = await _bring(client, "AE")
        put = await client.put(f"/api/applications/{app_id}/visa", json={"visa_signal": "no_sponsorship", "visa_country": "AE"})
        assert put.status_code == 200, put.text
    # A drafted row as stored (the server's mark): how it got there is the stamp tests' job.
    drafted_rtw = {"countries": [GB, {**ae, "set_by": "assistant"}]}
    profile_edits.record_edits(fixture_user_id, "token:claude-code", [("user_info.right_to_work", drafted_rtw)],
                               consent_paths=["user_info.right_to_work"])
    assert _rows(fixture_user_id)["AE"]["set_by"] == "assistant"
    db = await api_deps.get_db()
    assert await visa.user_work_countries(db, fixture_user_id) == ["GB"]
    rtw = load_profile(fixture_user_id).user_info.right_to_work
    assert build_search_brief(rtw)["work_from"] == ["GB"] and [r["country"] for r in build_search_brief(rtw)["rows"]] == []
    async with authenticated_async_context() as client:
        badge = (await client.get(f"/api/applications/{app_id}")).json()["visa"]
        assert badge["needs_sponsorship"] is True, "AE is not a country the user said they can work in"
        drafted = _rows(fixture_user_id)["AE"]
        await _patch(client, _rtw(GB, {**drafted, "set_by": None}))
        assert "set_by" not in _rows(fixture_user_id)["AE"]
        assert await visa.user_work_countries(db, fixture_user_id) == ["GB", "AE"]
        assert (await client.get(f"/api/applications/{app_id}")).json()["visa"]["needs_sponsorship"] is False
        confirmed = load_profile(fixture_user_id).user_info.right_to_work
        assert [r["country"] for r in build_search_brief(confirmed)["rows"]] == ["AE"]


@pytest.mark.asyncio
async def test_a_row_the_user_said_to_remember_is_theirs_not_a_draft(authenticated_async_context, fixture_user_id):
    """The setup interview: the assistant saves the user's own places with remember: true. Those rows are the
    user's (they count for the search brief); a drafted row sent back unchanged in the same write stays drafted."""
    from src.api.main import app
    from src.services.profile.search_brief import build_search_brief
    from src.services.profile.storage import load_profile

    async with authenticated_async_context() as client:
        await _patch(client, _rtw(GB, ANY))
        token = (await client.post("/api/tokens", json={"name": "claude-code"})).json()["token"]
    ae = {"country": "AE", "work_authorization": "needs_sponsorship", "needs_sponsorship": True}
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    ) as agent:
        await _patch(agent, _rtw(GB, ANY, ae))  # no remember: a draft
        drafted = _rows(fixture_user_id)["AE"]
        assert drafted["set_by"] == "assistant"
        fr = {"country": "FR", "work_authorization": "citizen", "needs_sponsorship": False, "onsite": "yes"}
        out = await _patch(agent, {**_rtw(GB, ANY, drafted, fr), "remember": True})
        assert out["not_saved"] == []
    rows = _rows(fixture_user_id)
    assert "set_by" not in rows["FR"] and "set_by" not in rows["GB"], "the user's own answer, remembered"
    assert rows["AE"]["set_by"] == "assistant", "unchanged draft stays a draft"
    brief = build_search_brief(load_profile(fixture_user_id).user_info.right_to_work)
    assert "FR" in [r["country"] for r in brief["rows"]] and "AE" not in [r["country"] for r in brief["rows"]]


@pytest.mark.asyncio
async def test_no_row_means_nothing_is_shown(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        await _patch(client, _rtw(GB))
        assert await _page(client, "FR") == {"country": "FR"}, "rule #29: no row, no fallback, nothing shown"
        assert (await _kit(client, await _bring(client, "FR"))).get("country_instructions") is None
        assert (await client.get("/api/profile/countries/Atlantis")).status_code == 422


def test_a_fact_the_user_remembers_for_the_country_replaces_the_drafted_guess():
    """The questionnaire's remember (an approved answer bound to AE) confirms that one fact; the rest stay guesses."""
    from src.services.applications.kit import compute_answers
    from src.services.profile.models import UserProfile

    profile = UserProfile()
    profile.user_info.right_to_work = {"countries": [
        {"country": "AE", "work_authorization": "needs_sponsorship", "needs_sponsorship": True, "set_by": "assistant"}]}
    profile.user_info.answers = [{"question": "Need sponsorship in AE?", "answer": "No", "approved": True,
                                  "topic": "right_to_work.AE.needs_sponsorship", "country": "AE"}]
    items = {i["key"]: i for i in compute_answers(profile, [], "AE")[0]["right_to_work"]}
    assert (items["right_to_work.AE.needs_sponsorship"]["value"], items["right_to_work.AE.needs_sponsorship"]["state"]) \
        == ("No", "confirmed")
    assert items["right_to_work.AE.work_authorization"]["state"] == "sensitive"
