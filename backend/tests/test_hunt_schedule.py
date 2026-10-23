"""Owner 2026-10-23 - ``assistant_settings.hunt_every_hours``: hunt for jobs every N hours.

The user's own scheduled task (at the AI vendor) asks Job360 what is due; Job360 runs
nothing itself. This file covers the setting: its value rules, that turning it on or
hunting more often is a riskier change (an assistant's such change waits for the user's
OK on the website), that turning it off applies at once, and that an empty value stays
silent (rule #29). Helpers are copied, never imported from another test module.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.services.profile import assistant_settings as rules
from src.services.profile.edits import ProfileEditError, validate_edit
from src.services.profile.models import AssistantSettings, UserPreferences

HUNT = "assistant_settings.hunt_every_hours"
NOW = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)


# ── Pure rules ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("value", [0, 1, 6, 24, 168])
def test_hunt_every_takes_whole_hours_up_to_a_week(value):
    assert rules.validate_setting(HUNT, value, now=NOW) == value


@pytest.mark.parametrize("value", [-1, 169, 1.5, "six", "6h", True, [6]])
def test_hunt_every_refuses_anything_else_and_never_echoes_the_value(value):
    with pytest.raises(ProfileEditError) as err:
        rules.validate_setting(HUNT, value, now=NOW)
    assert err.value.status_code == 422
    assert HUNT in err.value.detail and "whole number from 0 to 168" in err.value.detail
    if isinstance(value, str):
        assert value not in err.value.detail


def test_a_clear_is_allowed_through_the_edit_door():
    assert validate_edit(HUNT, None) is None


@pytest.mark.parametrize(("stored", "effective"), [(None, None), (0, None), (6, 6), (168, 168), (999, None)])
def test_empty_or_zero_reads_as_off(stored, effective):
    assert rules.effective(AssistantSettings(hunt_every_hours=stored)).hunt_every_hours == effective
    assert rules.effective_path_value(HUNT, stored, NOW) == effective


@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [
        (None, 6, "riskier"),  # turned on
        (24, 6, "riskier"),  # more often
        (6, 24, "safer"),  # less often
        (6, None, "safer"),  # turned off
        (6, 6, "safer"),  # unchanged
        (None, None, "safer"),
    ],
)
def test_turning_it_on_or_hunting_more_often_is_riskier(before, after, expected):
    assert rules.classify_change(HUNT, before, after) == expected


def test_it_is_a_gated_setting():
    assert HUNT in rules.SETTING_PATHS and HUNT in rules.GATED_PATHS


def test_a_new_profile_has_no_hunt_schedule():
    """Rule #29: nothing is ever set for the user."""
    assert AssistantSettings().hunt_every_hours is None
    view = rules.build_settings_view(_profile(), [], (None, 0), NOW)
    assert view["hunt_every_hours"] == {"value": None, "effective": None}


def _profile():
    from src.services.profile.models import CVData, UserProfile

    return UserProfile(cv_data=CVData(raw_text="x"), preferences=UserPreferences())


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


async def _hunt(client: AsyncClient) -> dict[str, Any]:
    resp = await client.get("/api/assistant-settings")
    assert resp.status_code == 200, resp.text
    return resp.json()["hunt_every_hours"]


@pytest.mark.asyncio
async def test_the_web_user_sets_and_clears_it_at_once(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        resp = await client.patch("/api/profile", json={"edits": [{"path": HUNT, "value": 6}]})
        assert resp.status_code == 200, resp.text
        assert resp.json()["waiting"] == []
        assert await _hunt(client) == {"value": 6, "effective": 6}
        resp = await client.patch("/api/profile", json={"edits": [{"path": HUNT, "value": None}]})
        assert resp.status_code == 200, resp.text
        assert await _hunt(client) == {"value": None, "effective": None}


@pytest.mark.asyncio
async def test_an_assistant_cannot_turn_it_on_or_shorten_it_until_the_user_confirms(
    authenticated_async_context, fixture_user_id
):
    from src.api.main import app

    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        token = (await client.post("/api/tokens", json={"name": "claude-code"})).json()["token"]
        agent_headers = {"Authorization": f"Bearer {token}"}

        async def agent_set(value: Any) -> dict[str, Any]:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test", headers=agent_headers
            ) as agent:
                resp = await agent.patch("/api/profile", json={"edits": [{"path": HUNT, "value": value}]})
            assert resp.status_code == 200, resp.text
            return resp.json()

        body = await agent_set(6)
        assert body["applied"] == [] and [w["path"] for w in body["waiting"]] == [HUNT]
        assert (await _hunt(client))["effective"] is None
        confirmed = await client.post(f"/api/assistant-settings/requests/{body['waiting'][0]['id']}/confirm")
        assert confirmed.status_code == 200, confirmed.text
        assert (await _hunt(client))["effective"] == 6

        sooner = await agent_set(1)  # more often: waits again
        assert sooner["applied"] == [] and [w["path"] for w in sooner["waiting"]] == [HUNT]
        assert (await _hunt(client))["effective"] == 6

        off = await agent_set(None)  # off: safer, applies at once
        assert off["waiting"] == [] and [a["path"] for a in off["applied"]] == [HUNT]
        assert (await _hunt(client))["effective"] is None
