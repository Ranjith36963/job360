"""H1 - never lose an answer (owner's live setup, 2026-10-10).

One case per shape that 422'd in production (14 of 27 ``update_profile`` calls). Each is now either
(a) ACCEPTED with the user's fact kept, or (b) REFUSED with "Nothing was saved.", an Example and the
resend-all list. The tests go through the real MCP tool function (pydantic -> route -> validators ->
store), so the text an assistant reads is the text asserted here. Helpers are copied, never imported
from another test module (a cross-module fixture import breaks schema isolation).
"""
from __future__ import annotations

import json
import logging
from typing import Any

import pytest

pytest.importorskip("mcp")


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[dict[str, Any]] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(dict(record.__dict__))


@pytest.fixture
def audit_capture():
    logger = logging.getLogger("job360.audit")
    handler = _Capture()
    logger.addHandler(handler)
    old = logger.level
    logger.setLevel(logging.INFO)
    yield handler
    logger.removeHandler(handler)
    logger.setLevel(old)


def _seed(user_id: str) -> None:
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


@pytest.fixture
async def tools(authenticated_async_context, fixture_user_id):
    """``(update_profile, get_profile, save_fit)`` tool functions bound to the fixture user."""
    from src.api import mcp_server

    _seed(fixture_user_id)
    async with authenticated_async_context():
        manager = mcp_server.build_server()._tool_manager
        mcp_server._current_user.set(mcp_server.CurrentUser(id=fixture_user_id, email="e2e@example.com"))
        try:
            yield (
                manager.get_tool("update_profile").fn,
                manager.get_tool("get_profile").fn,
                manager.get_tool("save_fit").fn,
            )
        finally:
            mcp_server._current_user.set(None)


def _e(path: str, value: Any) -> dict[str, Any]:
    return {"path": path, "value": value}


async def _refused(update_profile, *edits: dict[str, Any]) -> str:
    from mcp.server.mcpserver.exceptions import ToolError

    with pytest.raises(ToolError) as info:
        await update_profile(list(edits))
    return str(info.value)


def _assert_self_correcting(text: str, bad_path: str, all_paths: list[str]) -> None:
    assert "Nothing was saved." in text
    assert f"{bad_path}:" in text
    assert "Example:" in text
    assert f"resend ALL {len(all_paths)} paths in one call: {', '.join(all_paths)}" in text


async def _fields(get_profile) -> dict[str, Any]:
    return (await get_profile())["fields"]


# ── (a) accepted, the fact is kept ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_contact_linkedin_key_and_country_name(tools):
    update_profile, get_profile, _ = tools
    out = await update_profile([_e("user_info.contact", {"linkedin": "https://linkedin.com/in/ada", "address_country": "United Kingdom"})])
    assert (await _fields(get_profile))["user_info.contact"] == {
        "linkedin_url": "https://linkedin.com/in/ada", "address_country": "GB",
    }
    rules = {(row["path"], row["rule"]) for row in out["normalized"]}
    assert ("user_info.contact.linkedin_url", "key_alias") in rules
    assert ("user_info.contact.address_country", "country_name_to_iso2") in rules


@pytest.mark.asyncio
async def test_right_to_work_keyed_by_country_keeps_note_and_any(tools):
    update_profile, get_profile, _ = tools
    out = await update_profile([_e("user_info.right_to_work", {
        "GB": {"work_authorization": "citizen", "note": "British citizen"},
        "ANY": {"remote_ok": True, "onsite": "only_if_sponsored", "note": "remote anywhere, on-site if sponsored"},
    })])
    stored = (await _fields(get_profile))["user_info.right_to_work"]["countries"]
    assert {"country": "GB", "work_authorization": "citizen", "note": "British citizen"} in stored
    assert {
        "country": "ANY", "remote_ok": True, "onsite": "only_if_sponsored",
        "note": "remote anywhere, on-site if sponsored",
    } in stored
    assert any(row["rule"] == "country_keyed_to_records" for row in out["normalized"])


@pytest.mark.asyncio
async def test_work_authorization_free_text_keeps_the_words_in_note(tools):
    update_profile, get_profile, _ = tools
    await update_profile([_e("user_info.right_to_work", {"countries": [
        {"country": "GB", "work_authorization": "Green card"},
    ]})])
    rec = (await _fields(get_profile))["user_info.right_to_work"]["countries"][0]
    assert rec["work_authorization"] == "permanent_resident"
    assert rec["note"] == "Green card"


@pytest.mark.asyncio
async def test_citizenship_string_becomes_a_list(tools):
    update_profile, get_profile, _ = tools
    out = await update_profile([_e("user_info.right_to_work", {"citizenship": "British"})])
    assert (await _fields(get_profile))["user_info.right_to_work"]["citizenship"] == ["GB"]
    assert any(row["rule"] == "string_to_list" for row in out["normalized"])


@pytest.mark.asyncio
async def test_logistics_start_abroad_relocate_and_provisional_licence_survive(tools):
    update_profile, get_profile, _ = tools
    await update_profile([_e("user_info.logistics", {
        "start_on_site_abroad": "yes, once sponsorship is agreed",
        "countries": [{"country": "UK", "relocate": True, "driving_licence": "provisional UK"}],
    })])
    stored = (await _fields(get_profile))["user_info.logistics"]
    assert stored["start_on_site_abroad"] == "yes, once sponsorship is agreed"
    assert stored["countries"] == [{
        "country": "GB", "willing_to_relocate": True, "driving_licence": True,
        "driving_licence_type": "provisional", "driving_licence_country": "GB",
    }]


@pytest.mark.asyncio
async def test_equality_false_is_a_real_answer_stored_as_no(tools):
    update_profile, get_profile, _ = tools
    out = await update_profile([_e("user_info.equality", {"disability": False, "veteran": True})])
    assert (await _fields(get_profile))["user_info.equality"] == {"disability": "No", "veteran": "Yes"}
    assert any(row["rule"] == "bool_to_yes_no" for row in out["normalized"])


@pytest.mark.asyncio
async def test_experience_level_and_work_arrangement_aliases(tools):
    update_profile, get_profile, _ = tools
    out = await update_profile([
        _e("preferences.experience_level", "graduate"), _e("preferences.work_arrangement", "on-site"),
    ])
    fields = await _fields(get_profile)
    assert fields["preferences.experience_level"] == "entry"
    assert fields["preferences.work_arrangement"] == "onsite"
    assert {row["rule"] for row in out["normalized"]} >= {"experience_level_alias", "work_arrangement_alias"}


@pytest.mark.asyncio
@pytest.mark.parametrize("given,stored", [("hourly", "1h"), ("1h", "1h"), ("every 3 hours", "3h")])
async def test_check_every_hourly_is_allowed(tools, given, stored):
    update_profile, get_profile, _ = tools
    await update_profile([_e("preferences.check_every", given)])
    assert (await _fields(get_profile))["preferences.check_every"] == stored


@pytest.mark.asyncio
async def test_work_authorization_countries_accepts_names(tools):
    update_profile, get_profile, _ = tools
    await update_profile([_e("preferences.work_authorization_countries", ["United Kingdom", "Ireland"])])
    assert (await _fields(get_profile))["preferences.work_authorization_countries"] == ["GB", "IE"]


# ── (b) refused: nothing saved, an example, the resend-all list ──────────────


@pytest.mark.asyncio
async def test_unknown_contact_key_is_refused_never_dropped(tools):
    update_profile, get_profile, _ = tools
    text = await _refused(update_profile, _e("user_info.contact", {"fax": "123"}))
    _assert_self_correcting(text, "user_info.contact", ["user_info.contact"])
    assert "fax" in text and "123" not in text.split("Example:")[0].replace("fax", "")
    assert "user_info.contact" not in (await get_profile())["agent_edits"].__repr__()


@pytest.mark.asyncio
async def test_unreadable_country_has_no_remote_hint_on_contact(tools):
    update_profile, _, _ = tools
    text = await _refused(update_profile, _e("user_info.contact", {"address_country": "Narnia"}))
    _assert_self_correcting(text, "user_info.contact", ["user_info.contact"])
    assert "remote" not in text.split("Example:")[0].lower()


@pytest.mark.asyncio
async def test_experience_level_with_no_safe_mapping_is_refused_with_allowed_values(tools):
    update_profile, _, _ = tools
    text = await _refused(update_profile, _e("preferences.experience_level", "graduate_to_three_years"))
    _assert_self_correcting(text, "preferences.experience_level", ["preferences.experience_level"])
    assert "entry" in text and "senior" in text
    assert "graduate_to_three_years" not in text


@pytest.mark.asyncio
async def test_work_arrangement_nuance_is_refused_and_points_at_the_note(tools):
    update_profile, _, _ = tools
    text = await _refused(update_profile, _e("preferences.work_arrangement", "remote_or_onsite_if_sponsored"))
    _assert_self_correcting(text, "preferences.work_arrangement", ["preferences.work_arrangement"])
    assert "right_to_work" in text


@pytest.mark.asyncio
async def test_batch_with_bad_setup_progress_saves_nothing_and_lists_daily_check(tools):
    update_profile, get_profile, _ = tools
    before = (await _fields(get_profile)).get("preferences.daily_check")
    text = await _refused(
        update_profile,
        _e("assistant_settings.setup_progress", {"targets": {"done_at": "2026-10-10T16:00:00+00:00"}}),
        _e("preferences.daily_check", "ask"),
    )
    paths = ["assistant_settings.setup_progress", "preferences.daily_check"]
    _assert_self_correcting(text, "assistant_settings.setup_progress", paths)
    assert (await _fields(get_profile)).get("preferences.daily_check") == before
    assert not before  # the daily check was NOT turned on by the half-failed call
    # the corrected call (the SAME paths, all of them) goes through
    await update_profile([
        _e("preferences.daily_check", "ask"),
        _e("assistant_settings.setup_progress", {}),
    ])
    assert (await _fields(get_profile))["preferences.daily_check"] == "ask"


@pytest.mark.asyncio
async def test_every_bad_path_is_named_not_just_the_first(tools):
    update_profile, _, _ = tools
    text = await _refused(
        update_profile,
        _e("preferences.check_every", "fortnightly"),
        _e("preferences.work_arrangement", "moon"),
        _e("preferences.about_me", "fine"),
    )
    assert "preferences.check_every:" in text and "preferences.work_arrangement:" in text
    assert "resend ALL 3 paths in one call: preferences.check_every, preferences.work_arrangement, preferences.about_me" in text


@pytest.mark.asyncio
async def test_malformed_edit_object_is_self_correcting(tools):
    update_profile, _, _ = tools
    text = await _refused(update_profile, {"path": "preferences.about_me", "val": "x"})
    assert "Nothing was saved." in text and "Example:" in text and "resend ALL 1 edits" in text


@pytest.mark.asyncio
async def test_save_fit_refusal_states_the_limits_with_an_example(authenticated_async_context, tools):
    from mcp.server.mcpserver.exceptions import ToolError

    _, _, save_fit = tools
    from src.api import mcp_server

    manager = mcp_server.build_server()._tool_manager
    bring = manager.get_tool("bring_job").fn
    job = await bring(title="Data Engineer", company="Acme", description="Build pipelines.", country="GB", remote=True)
    app_id = job["application_id"]
    long_name = "x" * 60
    axes = [{"name": long_name, "role": 50, "you": 50}, {"name": "a", "role": 1, "you": 1}, {"name": "b", "role": 1, "you": 1}]
    with pytest.raises(ToolError) as info:
        await save_fit(application_id=app_id, score=70, axes=axes)
    text = str(info.value)
    assert "Nothing was saved." in text and "at most 40 chars" in text and "Example axes:" in text
    assert long_name not in text


# ── the audit trail carries a closed reason code, never a value ──────────────


def _rows(capture: _Capture, **match: Any) -> list[dict[str, Any]]:
    return [r for r in capture.records if all(r.get(k) == v for k, v in match.items())]


@pytest.mark.asyncio
async def test_refused_update_profile_audit_rows_carry_tool_path_and_reason(tools, audit_capture):
    update_profile, _, _ = tools
    await _refused(update_profile, _e("preferences.check_every", "fortnightly"), _e("preferences.about_me", "SECRET-VALUE"))
    route = _rows(audit_capture, event="profile_edit_rejected")
    assert route and route[0]["tool"] == "update_profile" and route[0]["reason"] == "bad_value"
    assert route[0]["failed_path"] == "preferences.check_every" and route[0]["http_status"] == 422
    mcp_row = _rows(audit_capture, event="mcp_tool_call", tool="update_profile", status="error")
    assert mcp_row and mcp_row[0]["reason"] == "bad_value" and mcp_row[0]["http_status"] == 422
    assert "SECRET-VALUE" not in json.dumps(audit_capture.records, default=str)


@pytest.mark.asyncio
@pytest.mark.parametrize("edit,reason", [
    (_e("preferences.nope", "x"), "unknown_key"),
    (_e("assistant_settings.apply_mode", "ask_each"), "retired_path"),
    (_e("preferences.experience_years_min", 99), "out_of_range"),
    (_e("preferences.about_me", "x" * 5000), "too_long"),
    (_e("preferences.about_me", 5), "bad_type"),
])
async def test_reason_codes_per_kind_of_refusal(tools, audit_capture, edit, reason):
    update_profile, _, _ = tools
    await _refused(update_profile, edit)
    assert _rows(audit_capture, event="profile_edit_rejected")[0]["reason"] == reason


@pytest.mark.asyncio
async def test_years_order_and_missing_setup_answers_reason_codes(tools, audit_capture):
    update_profile, _, _ = tools
    await _refused(update_profile, _e("preferences.experience_years_min", 9), _e("preferences.experience_years_max", 2))
    await _refused(update_profile, _e("assistant_settings.setup_progress", {"targets": {"done_at": "2026-10-10T16:00:00+00:00"}}))
    reasons = [r["reason"] for r in _rows(audit_capture, event="profile_edit_rejected")]
    assert reasons == ["years_order", "setup_round_missing_answers"]


@pytest.mark.asyncio
async def test_pydantic_failure_is_audited_for_update_profile_and_record_event(tools, audit_capture):
    from mcp.server.mcpserver.exceptions import ToolError

    update_profile, _, _ = tools
    await _refused(update_profile, {"path": "preferences.about_me", "val": "x"})
    rows = _rows(audit_capture, event="mcp_tool_call", tool="update_profile", status="error")
    assert rows and rows[0]["reason"] == "unknown_key" and rows[0]["http_status"] == 422

    from src.api import mcp_server

    record_event = mcp_server.build_server()._tool_manager.get_tool("record_event").fn
    with pytest.raises(ToolError):
        await record_event(event_type="note", application_id=1, source={"nope": 1})
    rec = _rows(audit_capture, event="mcp_tool_call", tool="record_event", status="error")
    assert rec and rec[0]["reason"] in {"unknown_key", "validation_error", "bad_type"}


@pytest.mark.asyncio
async def test_save_fit_pydantic_failure_is_audited(tools, audit_capture):
    from mcp.server.mcpserver.exceptions import ToolError

    _, _, save_fit = tools
    with pytest.raises(ToolError):
        await save_fit(application_id=1, score=500)
    rows = _rows(audit_capture, event="mcp_tool_call", tool="save_fit", status="error")
    assert rows and rows[0]["reason"] in {"out_of_range", "validation_error"}


# ── get_profile: shapes + include_raw ────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_profile_has_shapes_and_raw_only_when_asked(tools):
    _, get_profile, _ = tools
    default = await get_profile()
    assert default["raw_available"] is True and "raw" not in default
    assert "user_info.right_to_work" in default["shapes"]
    assert default["shapes"]["user_info.right_to_work"]["example"]["countries"]
    asked = await get_profile(include_raw=True)
    assert asked["raw"]["cv"].startswith("Python data engineer")


def test_every_shape_example_passes_its_own_validator():
    """A drift guard: the table that teaches assistants can never teach an invalid value."""
    from src.services.profile import edits, shapes

    for path, shape in shapes.SHAPES.items():
        assert path in edits.editable_paths(), path
        edits.validate_edit(path, shape["example"])


# ── review fixes (Opus, 2026-10-11) ──────────────────────────────────────────


@pytest.mark.parametrize("text", [
    "Indian citizen on Graduate visa", "will need a visa", "needs a visa", "visa required",
    "applying for citizenship", "citizenship pending", "EU citizen with settled status",
])
def test_work_authorization_wordy_text_is_refused_not_guessed(text):
    from src.services.profile.edits import ProfileEditError, validate_edit

    with pytest.raises(ProfileEditError):
        validate_edit("user_info.right_to_work", {"countries": [{"country": "GB", "work_authorization": text}]})


def test_work_authorization_exact_phrase_still_maps():
    from src.services.profile.edits import validate_edit

    rec = validate_edit(
        "user_info.right_to_work", {"countries": [{"country": "GB", "work_authorization": "Green card"}]}
    )["countries"][0]
    assert rec["work_authorization"] == "permanent_resident" and rec["note"] == "Green card"


@pytest.mark.parametrize("name,code", [
    ("Trinidad and Tobago", "TT"), ("Bosnia and Herzegovina", "BA"), ("Antigua and Barbuda", "AG"),
])
def test_citizenship_string_with_and_in_a_country_name_is_one_country(name, code):
    from src.services.profile.edits import validate_edit

    assert validate_edit("user_info.right_to_work", {"citizenship": name})["citizenship"] == [code]


def test_citizenship_string_list_still_splits():
    from src.services.profile.edits import validate_edit

    got = validate_edit("user_info.right_to_work", {"citizenship": "British and Indian"})["citizenship"]
    assert got == ["GB", "IN"]


@pytest.mark.parametrize("name", ["Congo", "Korea", "Korean", "Georgia", "Sudan"])
def test_ambiguous_country_names_are_refused(name):
    from src.services.profile.edits import ProfileEditError, validate_edit

    with pytest.raises(ProfileEditError):
        validate_edit("user_info.contact", {"address_country": name})


@pytest.mark.asyncio
async def test_save_fit_refusal_reads_live_limits(monkeypatch, tools):
    from mcp.server.mcpserver.exceptions import ToolError

    from src.core import settings

    monkeypatch.setattr(settings, "APPLICATION_FIT_AXIS_NAME_MAX_CHARS", 33)
    _, _, save_fit = tools
    with pytest.raises(ToolError) as info:
        await save_fit(application_id=1, score=500)
    assert "at most 33 chars" in str(info.value)


def test_any_record_answers_a_country_with_no_record():
    from src.services.applications import kit
    from src.services.profile.models import UserInfo, UserProfile

    profile = UserProfile(user_info=UserInfo(right_to_work={"countries": [
        {"country": "ANY", "remote_ok": True, "onsite": "only_if_sponsored", "note": "remote anywhere"},
    ]}))
    blocks, missing = kit.compute_answers(profile, [], "DE")
    keys = {i["key"] for i in blocks["right_to_work"]}
    assert "right_to_work.DE.onsite" in keys and "right_to_work.DE.remote_ok" in keys
    # ANY holds no work_authorization / needs_sponsorship: those stay missing for DE (no answer, no ask = bad)
    assert {m["key"] for m in missing if m["key"].startswith("right_to_work.")} == {
        "right_to_work.DE.work_authorization", "right_to_work.DE.needs_sponsorship",
    }
    full = UserProfile(user_info=UserInfo(right_to_work={"countries": [
        {"country": "ANY", "needs_sponsorship": True, "work_authorization": "needs_sponsorship"},
    ]}))
    blocks2, missing2 = kit.compute_answers(full, [], "DE")
    keys2 = {i["key"] for i in blocks2["right_to_work"]}
    assert {"right_to_work.DE.needs_sponsorship", "right_to_work.DE.work_authorization"} <= keys2
    assert not [m for m in missing2 if m["key"].startswith("right_to_work.")]
    _, missing_fr = kit.compute_answers(UserProfile(), [], "DE")
    assert [m for m in missing_fr if m["key"].startswith("right_to_work.")]
