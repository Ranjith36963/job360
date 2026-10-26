"""Owner 2026-10-25 - ``assistant_settings.pc_on_hours``: when the user's PC is on.

Tasks Job360's own browser could not finish wait for these hours (the own-browser queue). The
setting is always a "safer" change (a window frees nothing: every send still needs the user's own
click), null = any time (rule #29), and an overnight window belongs to its start day.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from httpx import ASGITransport, AsyncClient

from src.services.profile import assistant_settings as rules
from src.services.profile.edits import ProfileEditError, validate_edit
from src.services.profile.models import AssistantSettings, UserPreferences

PC = "assistant_settings.pc_on_hours"
NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
WORK = {"days": ["mon", "tue", "wed", "thu", "fri"], "from": "09:00", "to": "18:00"}
LON = ZoneInfo("Europe/London")


def _at(y: int, m: int, d: int, hh: int, mm: int = 0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=LON)


# ── Value rules ──────────────────────────────────────────────────────────────


def test_a_good_window_is_stored_in_week_order():
    got = rules.validate_setting(PC, {"days": ["fri", "mon"], "from": "09:00", "to": "17:30"}, now=NOW)
    assert got == {"days": ["mon", "fri"], "from": "09:00", "to": "17:30"}


@pytest.mark.parametrize(
    "value",
    [
        "09:00-17:00",
        {"from": "09:00", "to": "17:00"},  # no days
        {"days": [], "from": "09:00", "to": "17:00"},
        {"days": ["mon", "mon"], "from": "09:00", "to": "17:00"},
        {"days": ["monday"], "from": "09:00", "to": "17:00"},
        {"days": "mon", "from": "09:00", "to": "17:00"},
        {"days": [1], "from": "09:00", "to": "17:00"},
        {"days": ["mon"], "from": "09:00", "to": "09:00"},
        {"days": ["mon"], "from": "9am", "to": "17:00"},
        {"days": ["mon"], "from": "09:00", "to": "25:00"},
        {"days": ["mon"], "from": "09:00", "to": "17:00", "extra": 1},
    ],
)
def test_a_bad_window_is_refused_naming_the_path(value):
    with pytest.raises(ProfileEditError) as err:
        rules.validate_setting(PC, value, now=NOW)
    assert err.value.status_code == 422 and PC in err.value.detail


def test_a_clear_is_allowed_and_an_empty_shelf_stays_silent():
    assert validate_edit(PC, None) is None
    assert AssistantSettings().pc_on_hours is None
    assert rules.effective(AssistantSettings()).pc_on_hours is None


def test_the_edit_door_validates_it():
    assert validate_edit(PC, WORK) == WORK
    with pytest.raises(ProfileEditError):
        validate_edit(PC, {"days": ["mon"], "from": "09:00", "to": "09:00"})


def test_an_unreadable_stored_value_reads_as_any_time():
    assert rules.effective_path_value(PC, {"days": ["xyz"], "from": "09:00", "to": "17:00"}, NOW) is None
    assert rules.effective_path_value(PC, WORK, NOW) == WORK


# ── Always safer ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (None, WORK),
        (WORK, None),  # removed: any time
        (WORK, {"days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"], "from": "00:00", "to": "23:59"}),
        (WORK, WORK),
    ],
)
def test_it_is_always_a_safer_change(before, after):
    assert rules.classify_change(PC, before, after) == "safer"


def test_it_is_a_gated_setting_path():
    assert PC in rules.SETTING_PATHS and PC in rules.GATED_PATHS


# ── pc_on_state ──────────────────────────────────────────────────────────────


def test_no_window_is_open_any_time():
    assert rules.pc_on_state(None, _at(2026, 10, 25, 3)) == (True, None)


def test_inside_and_outside_a_same_day_window():
    assert rules.pc_on_state(WORK, _at(2026, 10, 26, 9, 0)) == (True, None)  # Monday 09:00 opens
    assert rules.pc_on_state(WORK, _at(2026, 10, 26, 17, 59)) == (True, None)
    is_open, opens = rules.pc_on_state(WORK, _at(2026, 10, 26, 18, 0))  # Monday 18:00 closed
    assert is_open is False and opens == _at(2026, 10, 27, 9, 0)
    is_open, opens = rules.pc_on_state(WORK, _at(2026, 10, 26, 8, 59))
    assert is_open is False and opens == _at(2026, 10, 26, 9, 0)


def test_a_weekend_waits_for_monday():
    is_open, opens = rules.pc_on_state(WORK, _at(2026, 10, 24, 12, 0))  # Saturday
    assert is_open is False and opens == _at(2026, 10, 26, 9, 0)


def test_an_overnight_window_belongs_to_its_start_day():
    fri = {"days": ["fri"], "from": "22:00", "to": "06:00"}
    assert rules.pc_on_state(fri, _at(2026, 10, 23, 23, 0))[0] is True  # Friday 23:00
    assert rules.pc_on_state(fri, _at(2026, 10, 24, 5, 59))[0] is True  # Saturday 05:59 is Friday's
    is_open, opens = rules.pc_on_state(fri, _at(2026, 10, 24, 22, 0))  # Saturday 22:00 is NOT
    assert is_open is False and opens == _at(2026, 10, 30, 22, 0)
    is_open, opens = rules.pc_on_state(fri, _at(2026, 10, 23, 12, 0))  # Friday noon: opens tonight
    assert is_open is False and opens == _at(2026, 10, 23, 22, 0)
    assert rules.pc_on_state(fri, _at(2026, 10, 23, 5, 0))[0] is False  # Friday 05:00 is Thursday's night


def test_the_read_model_carries_it():
    from src.services.profile.models import CVData, UserProfile

    profile = UserProfile(
        cv_data=CVData(raw_text="x"), preferences=UserPreferences(),
        assistant_settings=AssistantSettings(pc_on_hours=WORK),
    )
    view = rules.build_settings_view(profile, [], (None, 0), NOW)
    assert view["pc_on_hours"] == {"value": WORK, "effective": WORK}
    empty = rules.build_settings_view(UserProfile(cv_data=CVData(raw_text="x")), [], (None, 0), NOW)
    assert empty["pc_on_hours"] == {"value": None, "effective": None}


# ── Through the real doors ───────────────────────────────────────────────────


def _seed_profile(user_id: str) -> None:
    from src.services.profile.models import CVData, UserProfile
    from src.services.profile.storage import save_profile

    save_profile(
        UserProfile(
            cv_data=CVData(raw_text="Python data engineer.", name="Ada Lovelace", skills=["Python"]),
            preferences=UserPreferences(target_job_titles=["Data Engineer"]),
        ),
        user_id,
        source_action="cv_upload",
    )


async def _read(client: AsyncClient) -> dict[str, Any]:
    resp = await client.get("/api/assistant-settings")
    assert resp.status_code == 200, resp.text
    return resp.json()["pc_on_hours"]


@pytest.mark.asyncio
async def test_the_web_user_sets_and_clears_it(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        resp = await client.patch("/api/profile", json={"edits": [{"path": PC, "value": WORK}]})
        assert resp.status_code == 200, resp.text
        assert await _read(client) == {"value": WORK, "effective": WORK}
        resp = await client.patch("/api/profile", json={"edits": [{"path": PC, "value": None}]})
        assert resp.status_code == 200, resp.text
        assert await _read(client) == {"value": None, "effective": None}


@pytest.mark.asyncio
async def test_an_assistant_change_applies_at_once_with_no_waiting_request(
    authenticated_async_context, fixture_user_id
):
    from src.api.main import app

    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        token = (await client.post("/api/tokens", json={"name": "agent"})).json()["token"]
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test",
            headers={"Authorization": f"Bearer {token}"},
        ) as agent:
            resp = await agent.patch("/api/profile", json={"edits": [{"path": PC, "value": WORK}]})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["waiting"] == [] and [a["path"] for a in body["applied"]] == [PC]
        assert await _read(client) == {"value": WORK, "effective": WORK}
