"""Feature 9 - confirmed vs inferred facts (migration 0057_profile_edit_source).

``set_by`` says who WROTE a fact; ``source`` says whether the USER said it.
Helpers are copied, never imported from another test module (a cross-module
fixture import breaks schema isolation).
"""
from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

LOGI = "user_info.logistics"
NOTICE = {"notice_period": "1 month"}


def _seed_profile(user_id: str) -> None:
    from src.services.profile.models import CVData, UserPreferences, UserProfile
    from src.services.profile.storage import save_profile

    save_profile(
        UserProfile(
            cv_data=CVData(raw_text="Python data engineer.", name="Ada Lovelace", skills=["Python"]),
            preferences=UserPreferences(target_job_titles=["Data Engineer"]),
        ),
        user_id,
        source_action="cv_upload",
    )


async def _mint_token(client: AsyncClient) -> str:
    resp = await client.post("/api/tokens", json={"name": "claude-code"})
    assert resp.status_code == 201, resp.text
    return resp.json()["token"]


def _bearer_client(token: str) -> AsyncClient:
    from src.api.main import app

    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    )


def _edit(source: str | None = None, value: Any = NOTICE) -> dict[str, Any]:
    row: dict[str, Any] = {"path": LOGI, "value": value}
    if source is not None:
        row["source"] = source
    return row


@pytest.mark.asyncio
async def test_agent_edit_defaults_to_agent_inferred(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        token = await _mint_token(client)
    async with _bearer_client(token) as agent:
        resp = await agent.patch("/api/profile", json={"edits": [_edit()]})
        assert resp.status_code == 200, resp.text
        body = (await agent.get("/api/profile")).json()
    row = next(r for r in body["agent_edits"] if r["path"] == LOGI)
    assert row["source"] == "agent_inferred"
    assert row["confirmed"] is False


@pytest.mark.asyncio
async def test_agent_can_mark_user_said(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        token = await _mint_token(client)
    async with _bearer_client(token) as agent:
        resp = await agent.patch("/api/profile", json={"edits": [_edit("user_said")]})
        assert resp.status_code == 200, resp.text
        body = (await agent.get("/api/profile")).json()
    row = next(r for r in body["agent_edits"] if r["path"] == LOGI)
    assert row["source"] == "user_said"
    assert row["confirmed"] is True


@pytest.mark.asyncio
async def test_web_edit_is_user_said_and_refuses_inferred(authenticated_async_context, fixture_user_id):
    from src.services.profile import edits

    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        refused = await client.patch("/api/profile", json={"edits": [_edit("agent_inferred")]})
        assert refused.status_code == 422, refused.text
        assert edits.current_overlay(fixture_user_id) == [], "a refused call writes nothing"
        ok = await client.patch("/api/profile", json={"edits": [_edit()]})
        assert ok.status_code == 200, ok.text
        rows = edits.path_history(fixture_user_id, LOGI, 5)
    assert [r["source"] for r in rows] == ["user_said"]
    assert rows[0]["set_by"] == "web"


@pytest.mark.asyncio
async def test_get_profile_unconfirmed_absent_when_none(authenticated_async_context, fixture_user_id):
    pytest.importorskip("mcp")
    from src.api import mcp_server

    _seed_profile(fixture_user_id)
    async with authenticated_async_context():
        tool = mcp_server.build_server()._tool_manager.get_tool("get_profile")
        assert tool is not None
        mcp_server._current_user.set(mcp_server.CurrentUser(
                id=fixture_user_id, email="e2e@example.com", auth_via="token", actor_name="claude-code"
            ))
        try:
            bare = await tool.fn()
            update = mcp_server.build_server()._tool_manager.get_tool("update_profile")
            assert update is not None
            await update.fn([_edit("user_said")])
            said = await tool.fn()
            await update.fn([_edit(None, {"notice_period": "2 months"})])
            inferred = await tool.fn()
        finally:
            mcp_server._current_user.set(None)
    assert "unconfirmed" not in bare
    assert "unconfirmed" not in said
    assert inferred["unconfirmed"] == [LOGI]


@pytest.mark.asyncio
async def test_keep_confirms_inferred_fact(authenticated_async_context, fixture_user_id):
    from src.services.profile import edits

    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        token = await _mint_token(client)
    async with _bearer_client(token) as agent:
        assert (await agent.patch("/api/profile", json={"edits": [_edit()]})).status_code == 200
    async with authenticated_async_context() as client:
        kept = await client.post("/api/profile/edits/keep", json={"path": LOGI})
        assert kept.status_code == 200, kept.text
        newest = edits.path_history(fixture_user_id, LOGI, 1)[0]
        assert newest["set_by"] == "web"
        assert newest["source"] == "user_said"
        assert edits.is_confirmed(newest["set_by"], newest["source"])
        assert kept.json()["agent_edits"] == []


def test_kit_marks_inferred_answer_unconfirmed():
    from src.services.applications import kit
    from src.services.profile.models import CVData, UserInfo, UserProfile

    profile = UserProfile(cv_data=CVData(), user_info=UserInfo(logistics=dict(NOTICE)))

    def row(source: str, set_by: str = "agent:Claude") -> list[dict[str, Any]]:
        return [{"path": LOGI, "value": NOTICE, "set_by": set_by, "set_at": "2026-10-01T00:00:00Z", "source": source}]

    inferred, _ = kit.compute_answers(profile, row("agent_inferred"), "GB")
    said, _ = kit.compute_answers(profile, row("user_said"), "GB")
    web, _ = kit.compute_answers(profile, row("user_said", "web"), "GB")
    assert inferred["logistics"][0]["confirmed"] is False
    assert "confirmed" not in said["logistics"][0]
    assert "confirmed" not in web["logistics"][0]
    # Weak spot #6: the instruction now speaks in states, not in confirmed:false.
    assert any("state 'inferred'" in line and "source user_said" in line for line in kit.KIT_INSTRUCTIONS)
    assert not any("confirmed:false" in line for line in kit.KIT_INSTRUCTIONS)


# ── Weak spot #6: every kit answer carries a state ──────────────────────────


def _kit_rows(path: str, value: Any, source: str, set_by: str = "agent:Claude") -> dict[str, Any]:
    return {
        "path": path, "value": value, "set_by": set_by, "set_at": "2026-10-01T00:00:00Z", "source": source,
        "remember_consent_at": "2026-10-01T00:00:00Z",
    }


def _full_profile():
    from src.services.profile.models import CVData, UserInfo, UserProfile

    return UserProfile(
        cv_data=CVData(),
        user_info=UserInfo(
            logistics=dict(NOTICE),
            contact={"email": "ada@example.com"},
            right_to_work={"citizenship": "GB", "sanctions_country_citizen": "prefer not to say"},
            equality={"gender": "prefer not to say"},
            answers=[{"question": "Why us?", "answer": "Because.", "approved": True,
                      "recorded_at": "2026-10-01T00:00:00Z"}],
        ),
    )


def test_assistant_write_marked_user_said_is_relayed_until_web_confirm():
    from src.services.applications import kit

    answers, _ = kit.compute_answers(_full_profile(), [_kit_rows(LOGI, NOTICE, "user_said")], "GB")
    item = answers["logistics"][0]
    assert item["state"] == "relayed"
    assert "confirmed" not in item and "confirmed_at" not in item


def test_assistant_write_agent_inferred_is_inferred_and_keeps_confirmed_false():
    from src.services.applications import kit

    answers, _ = kit.compute_answers(_full_profile(), [_kit_rows(LOGI, NOTICE, "agent_inferred")], "GB")
    item = answers["logistics"][0]
    assert item["state"] == "inferred"
    assert item["confirmed"] is False


def test_approved_text_is_confirmed_even_when_an_assistant_wrote_it_inferred():
    from src.services.applications import kit

    rows = [_kit_rows("user_info.answers", [], "agent_inferred")]
    answers, _ = kit.compute_answers(_full_profile(), rows, "GB")
    item = answers["approved_text"][0]
    assert (item["key"], item["state"]) == ("Why us?", "confirmed")
    assert "confirmed" not in item


def test_equality_and_sanctions_are_sensitive_whoever_wrote_them():
    from src.services.applications import kit

    rows = [
        _kit_rows("user_info.equality", {}, "user_said"),
        _kit_rows("user_info.right_to_work", {}, "agent_inferred"),
    ]
    answers, _ = kit.compute_answers(_full_profile(), rows, "GB")
    assert [(i["key"], i["state"]) for i in answers["equality"]] == [("equality.gender", "sensitive")]
    rtw = {i["key"]: i for i in answers["right_to_work"]}
    assert rtw["right_to_work.sanctions_country_citizen"]["state"] == "sensitive"
    # confirmed:false stays where it was (back-compat); citizenship is plainly inferred.
    assert rtw["right_to_work.sanctions_country_citizen"]["confirmed"] is False
    assert rtw["right_to_work.citizenship"]["state"] == "inferred"


def test_answers_summary_counts_each_state():
    from src.services.applications import kit

    rows = [
        _kit_rows(LOGI, NOTICE, "agent_inferred"),  # logistics.notice_period -> inferred
        _kit_rows("user_info.contact", {}, "user_said", "web"),  # contact.email -> confirmed
        _kit_rows("user_info.right_to_work", {}, "agent_inferred"),  # citizenship inferred, sanctions sensitive
    ]
    answers, _ = kit.compute_answers(_full_profile(), rows, "GB")
    states = sorted(i["state"] for block in answers.values() for i in block)
    assert states == ["confirmed", "confirmed", "confirmed", "inferred", "inferred", "sensitive"]
    assert kit.answers_summary(answers)["relayed"] == 0
    # contact.email + approved_text + the equality answer nobody's assistant wrote = 3 confirmed; notice +
    # citizenship = 2 inferred; the inferred sanctions answer = 1 sensitive (owner 2026-10-26: only an
    # answer the user has not stood behind waits on the form).
    assert kit.answers_summary(answers) == {"confirmed": 3, "relayed": 0, "inferred": 2, "sensitive": 1}


def test_a_profile_item_an_assistant_inferred_is_inferred():
    from src.services.applications import kit
    from src.services.profile.models import CVData, UserProfile

    profile = UserProfile(cv_data=CVData(name="Ada Lovelace"))
    inferred, _ = kit.compute_answers(profile, [_kit_rows("cv_data.name", "Ada Lovelace", "agent_inferred")], "GB")
    parsed, _ = kit.compute_answers(profile, [], "GB")
    assert inferred["contact"][0]["state"] == "inferred" and "confirmed" not in inferred["contact"][0]
    assert parsed["contact"][0]["state"] == "confirmed"


def test_setup_recipe_tells_the_assistant_to_save_the_users_words_as_user_said():
    from src.api.routes.recipes import load_recipe

    assert "user_said" in load_recipe("setup").text
    assert "user_said" in load_recipe("rules").text


@pytest.mark.asyncio
async def test_kit_route_carries_state_and_answers_summary(authenticated_async_context, fixture_user_id):
    """End to end: a user_said write over MCP's route reads relayed, an inferred one inferred."""
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        token = await _mint_token(client)
        resp = await client.post("/api/jobs/bring", json={
            "title": "Data Engineer", "company": "Acme", "apply_url": "https://jobs.example.com/acme/1",
            "description": "Build pipelines.", "country": "GB",
        })
        assert resp.status_code in (200, 201), resp.text
        app_id = resp.json()["application_id"]
    async with _bearer_client(token) as agent:
        resp = await agent.patch("/api/profile", json={"edits": [
            _edit("user_said"),
            {"path": "user_info.contact", "value": {"email": "ada@example.com"}, "source": "agent_inferred"},
        ]})
        assert resp.status_code == 200, resp.text
        kit_body = (await agent.get(f"/api/applications/{app_id}/kit")).json()
    logi = kit_body["answers"]["logistics"][0]
    email = next(i for i in kit_body["answers"]["contact"] if i["key"] == "contact.email")
    assert (logi["key"], logi["state"]) == ("logistics.notice_period", "relayed")
    assert email["state"] == "inferred"
    assert kit_body["answers_summary"]["inferred"] >= 1 and kit_body["answers_summary"]["relayed"] >= 1


@pytest.mark.asyncio
async def test_legacy_agent_rows_read_unconfirmed(authenticated_async_context, fixture_user_id):
    """A row written before migration 0057 has source '' - nobody confirmed it."""
    import json

    from src.core.settings import DB_PATH
    from src.repositories import pgsync

    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        with pgsync.connect(str(DB_PATH)) as conn:
            conn.execute(
                "INSERT INTO profile_edits (user_id, path, value, set_by, set_at) VALUES (?, ?, ?, ?, ?)",
                (fixture_user_id, LOGI, json.dumps(NOTICE), "agent:Claude", "2026-09-01T00:00:00Z"),
            )
        body = (await client.get("/api/profile")).json()
    row = next(r for r in body["agent_edits"] if r["path"] == LOGI)
    assert row["source"] == ""
    assert row["confirmed"] is False


# ── Sensitive answers mirror the job-apply-plugin reference (re-confirmed before every use) ──


def test_visa_fields_are_sensitive_and_citizenship_is_not():
    from src.services.applications import kit
    from src.services.profile.models import CVData, UserInfo, UserProfile

    profile = UserProfile(
        cv_data=CVData(),
        user_info=UserInfo(right_to_work={
            "citizenship": "GB",
            "countries": [{"country": "gb", "work_authorization": "citizen", "needs_sponsorship": False}],
        }),
    )
    rows = [_kit_rows("user_info.right_to_work", {}, "user_said", set_by="web")]
    answers, _ = kit.compute_answers(profile, rows, "GB")
    rtw = {i["key"]: i for i in answers["right_to_work"]}
    # Owner 2026-10-26: the user's own web-saved visa answers are confirmed and filled like any fact.
    assert rtw["right_to_work.GB.work_authorization"]["state"] == "confirmed"
    assert rtw["right_to_work.GB.needs_sponsorship"]["state"] == "confirmed"
    assert rtw["right_to_work.GB.needs_sponsorship"]["value"] is False
    assert rtw["right_to_work.citizenship"]["state"] == "confirmed"
    # An assistant's inferred visa answer still waits for the user on the form.
    inferred, _ = kit.compute_answers(profile, [_kit_rows("user_info.right_to_work", {}, "agent_inferred")], "GB")
    rtw = {i["key"]: i for i in inferred["right_to_work"]}
    assert rtw["right_to_work.GB.work_authorization"]["state"] == "sensitive"
    assert "confirmed_at" not in rtw["right_to_work.GB.work_authorization"]


def test_instructions_say_confirm_sensitive_on_this_form_never_ask_again_is_gone():
    from src.services.applications import kit

    text = " ".join(kit.KIT_INSTRUCTIONS)
    assert "never ask again" not in text
    assert "is shown to the user and confirmed on THIS form before it is used; never fill it silently" in text
    assert "visa / right-to-work status" in text


def test_confirmed_items_carry_confirmed_at_and_inferred_do_not():
    from src.services.applications import kit

    rows = [
        _kit_rows(LOGI, NOTICE, "user_said", "web"),
        _kit_rows("user_info.contact", {}, "agent_inferred"),
    ]
    answers, _ = kit.compute_answers(_full_profile(), rows, "GB")
    notice = answers["logistics"][0]
    assert notice["state"] == "confirmed"
    assert notice["confirmed_at"] == "2026-10-01T00:00:00Z"
    email = answers["contact"][0]
    assert email["state"] == "inferred"
    assert "confirmed_at" not in email
    # No stored time (profile-sourced item): nothing to say.
    assert all("confirmed_at" not in i for i in answers["equality"])


def test_salary_is_sensitive_like_the_reference():
    """neonwatty answer-memory SKILL.md L131: salary is sensitive - ask before every use."""
    from src.services.applications import kit  # noqa: PLC0415
    assert kit._is_sensitive("salary", "salary.IN") is True
    assert kit._is_sensitive("logistics", "logistics.current_salary") is True
    assert kit._is_sensitive("logistics", "logistics.notice_period") is False
    text = " ".join(kit.KIT_INSTRUCTIONS)
    assert "may I use it on this form, and may I remember it for later" in text


# ── Weak spot #6 wave 2: remember consent (migration 0063) ───────────────────
# Mirrors job-apply-plugin scripts/job-apply-store.py L503-507 (refuse without consent), L541-544
# (the helper stamps rememberedWithConsentAt), L195-199 (no marker on read = refused) and
# skills/answer-memory/SKILL.md L151 (null placeholder allowed). NOT IN REFERENCE: the web actor.
EQ = "user_info.equality"
GENDER = {"gender": "Male"}


async def _agent_patch(authenticated_async_context, fixture_user_id, edits):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        token = await _mint_token(client)
    async with _bearer_client(token) as agent:
        resp = await agent.patch("/api/profile", json={"edits": edits})
        assert resp.status_code == 200, resp.text
        return resp.json()


def _overlay(user_id: str) -> dict[str, dict[str, Any]]:
    from src.services.profile import edits as profile_edits

    return {r["path"]: r for r in profile_edits.current_overlay(user_id)}


@pytest.mark.asyncio
async def test_sensitive_value_without_remember_not_stored(authenticated_async_context, fixture_user_id):
    body = await _agent_patch(authenticated_async_context, fixture_user_id, [{"path": EQ, "value": GENDER}])
    assert body["applied"] == []
    assert any(EQ in line and "remember: true" in line for line in body["not_saved"])
    assert EQ not in _overlay(fixture_user_id)


@pytest.mark.asyncio
async def test_sensitive_with_remember_stamps_server_marker(authenticated_async_context, fixture_user_id):
    from datetime import datetime, timezone

    body = await _agent_patch(
        authenticated_async_context, fixture_user_id,
        [{"path": EQ, "value": GENDER, "remember": True}],
    )
    assert [a["path"] for a in body["applied"]] == [EQ]
    stamp = _overlay(fixture_user_id)[EQ]["remember_consent_at"]
    assert abs((datetime.now(timezone.utc) - datetime.fromisoformat(stamp)).total_seconds()) < 5


@pytest.mark.asyncio
async def test_caller_cannot_supply_the_marker_time(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        token = await _mint_token(client)
    async with _bearer_client(token) as agent:
        resp = await agent.patch(
            "/api/profile",
            json={"edits": [{"path": EQ, "value": GENDER, "remember": True, "remember_consent_at": "2000-01-01"}]},
        )
    assert resp.status_code == 422  # extra="forbid": the time is never an input


@pytest.mark.asyncio
async def test_sensitive_null_placeholder_allowed(authenticated_async_context, fixture_user_id):
    body = await _agent_patch(authenticated_async_context, fixture_user_id, [{"path": EQ, "value": None}])
    assert [a["path"] for a in body["applied"]] == [EQ]
    assert body["not_saved"] == []


@pytest.mark.asyncio
async def test_nonsensitive_needs_no_remember(authenticated_async_context, fixture_user_id):
    body = await _agent_patch(authenticated_async_context, fixture_user_id, [_edit()])
    assert [a["path"] for a in body["applied"]] == [LOGI]
    assert _overlay(fixture_user_id)[LOGI]["remember_consent_at"] is None


@pytest.mark.asyncio
async def test_one_refused_sensitive_edit_does_not_block_the_others(authenticated_async_context, fixture_user_id):
    body = await _agent_patch(
        authenticated_async_context, fixture_user_id, [_edit(), {"path": EQ, "value": GENDER}]
    )
    assert [a["path"] for a in body["applied"]] == [LOGI]
    assert len(body["not_saved"]) == 1


@pytest.mark.asyncio
async def test_web_write_sensitive_always_stamps_marker(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        resp = await client.patch("/api/profile", json={"edits": [{"path": EQ, "value": GENDER}]})
        assert resp.status_code == 200, resp.text
    assert _overlay(fixture_user_id)[EQ]["remember_consent_at"]


def test_edit_is_sensitive_covers_visa_salary_and_ignores_empty():
    from src.services.applications.kit import edit_is_sensitive

    assert edit_is_sensitive("user_info.right_to_work", {"countries": [{"country": "GB", "needs_sponsorship": False}]})
    assert edit_is_sensitive("user_info.right_to_work", {"sanctions_country_citizen": False})
    assert not edit_is_sensitive("user_info.right_to_work", {"citizenship": "GB"})
    assert edit_is_sensitive("user_info.logistics", {"current_salary": {"amount": 1}})
    assert not edit_is_sensitive("user_info.logistics", NOTICE)
    assert edit_is_sensitive("preferences.salary_by_country", [{"country": "GB", "min": 1}])
    assert not edit_is_sensitive(EQ, {}) and not edit_is_sensitive(EQ, None)


def test_kit_sensitive_item_has_remembered_at_value():
    from src.services.applications import kit
    from src.services.profile.models import CVData, UserInfo, UserProfile

    profile = UserProfile(cv_data=CVData(), user_info=UserInfo(equality=dict(GENDER)))
    stamp = "2026-10-15T09:00:00+00:00"
    row = {"path": EQ, "value": GENDER, "set_by": "agent:Claude", "set_at": "2026-10-15T09:00:00+00:00",
           "source": "user_said", "remember_consent_at": stamp}
    answers, missing = kit.compute_answers(profile, [row], "GB")
    item = answers["equality"][0]
    assert (item["state"], item["remembered_at"]) == ("sensitive", stamp)
    assert all(m["key"] != EQ for m in missing)


def test_kit_legacy_sensitive_without_marker_moves_to_missing():
    from src.services.applications import kit
    from src.services.profile.models import CVData, UserInfo, UserProfile

    profile = UserProfile(cv_data=CVData(), user_info=UserInfo(equality=dict(GENDER)))
    row = {"path": EQ, "value": GENDER, "set_by": "agent:Claude", "set_at": "2026-10-01T00:00:00+00:00",
           "source": "user_said", "remember_consent_at": None}
    answers, missing = kit.compute_answers(profile, [row], "GB")
    assert answers["equality"] == []
    assert any(m["key"] == EQ and "remember" in m["why"] for m in missing)
    # The user's own web row is trusted without a marker.
    web = dict(row, set_by="web")
    answers, _ = kit.compute_answers(profile, [web], "GB")
    assert answers["equality"][0]["remembered_at"] == web["set_at"]


def test_kit_instructions_name_remember():
    from src.services.applications import kit

    assert any("remember: true" in line for line in kit.KIT_INSTRUCTIONS)


# ── Part B: a relayed chat word reads "relayed" until the user confirms on the web ──


def test_relayed_until_that_row_is_approved_on_the_web():
    """neonwatty job-apply/SKILL.md:118 - the user's own word is what makes an answer theirs.
    Per row: the profile-memory Keep writes a newer web row for THAT path; nothing else confirms it."""
    from src.services.applications import kit

    rows = [_kit_rows(LOGI, NOTICE, "user_said")]
    before, _ = kit.compute_answers(_full_profile(), rows, "GB")
    assert before["logistics"][0]["state"] == "relayed"
    assert kit.answers_summary(before)["relayed"] == 1
    kept = [_kit_rows(LOGI, NOTICE, "user_said", set_by="web") | {"set_at": "2026-10-02T00:00:00Z"}]
    after, _ = kit.compute_answers(_full_profile(), kept, "GB")
    assert after["logistics"][0]["state"] == "confirmed"
    assert after["logistics"][0]["confirmed_at"] == "2026-10-02T00:00:00Z"
    assert kit.answers_summary(after)["relayed"] == 0
    # Keeping a DIFFERENT path leaves this row relayed.
    other = rows + [_kit_rows("user_info.contact", {"phone": "1"}, "user_said", set_by="web")]
    still, _ = kit.compute_answers(_full_profile(), other, "GB")
    assert still["logistics"][0]["state"] == "relayed"


def test_sensitive_beats_relayed_and_inferred_stays_inferred():
    from src.services.applications import kit

    rows = [
        _kit_rows("user_info.equality", {}, "user_said"),
        _kit_rows("user_info.contact", {}, "agent_inferred"),
    ]
    answers, _ = kit.compute_answers(_full_profile(), rows, "GB")
    assert answers["equality"][0]["state"] == "sensitive"
    assert answers["contact"][0]["state"] == "inferred"
