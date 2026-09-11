"""Slice 7 (#514) — the visa / sponsorship signal, country-agnostic.

docs/plans/2026-09-11-visa-signal/spec.md. Two facts, one comparison, no
country knowledge in Job360: the agent's reading of the ad (per
application) against the candidate's own list of countries where they need
no sponsorship (profile). ``unknown`` shows nothing (rule #29).
"""
from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from src.core import settings
from src.services.applications.visa import needs_sponsorship, normalize_country_codes

_AD = {
    "title": "Platform Engineer",
    "company": "Nordwind GmbH",
    "location": "Berlin",
    "apply_url": "https://nordwind.example/jobs/7",
    "description": "Kubernetes, Go. We cannot offer visa sponsorship for this role.",
}


def _seed_countries(user_id: str, countries: list[str]) -> None:
    from src.services.profile.models import CVData, UserPreferences, UserProfile
    from src.services.profile.storage import save_profile

    save_profile(
        UserProfile(
            cv_data=CVData(raw_text="Jane Doe"),
            preferences=UserPreferences(work_authorization_countries=countries),
        ),
        user_id,
        source_action="user_edit",
    )


async def _bring(client: AsyncClient, **extra: Any) -> int:
    resp = await client.post("/api/jobs/bring", json={**_AD, **extra})
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _detail(client: AsyncClient, app_id: int) -> dict[str, Any]:
    resp = await client.get(f"/api/applications/{app_id}")
    assert resp.status_code == 200, resp.text
    return resp.json()


# ── the pure rules ─────────────────────────────────────────────────────────────


def test_needs_sponsorship_is_silent_when_either_side_is_silent():
    assert needs_sponsorship("unknown", "DE", ["GB"]) is None
    assert needs_sponsorship("no_sponsorship", "", ["GB"]) is None
    assert needs_sponsorship("no_sponsorship", "DE", []) is None


def test_needs_sponsorship_is_just_list_membership_case_insensitive():
    assert needs_sponsorship("no_sponsorship", "DE", ["GB", "IN"]) is True
    assert needs_sponsorship("no_sponsorship", "gb", ["GB"]) is False
    assert needs_sponsorship("sponsors", "DE", ["GB"]) is True  # the comparison never reads the signal's meaning


def test_country_codes_normalise_and_reject_junk():
    assert normalize_country_codes([" gb", "in", "GB", "de"]) == ["GB", "IN", "DE"]
    assert normalize_country_codes(None) == []
    with pytest.raises(ValueError):
        normalize_country_codes(["United Kingdom"])
    with pytest.raises(ValueError):
        normalize_country_codes("GB")


# ── the doors ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bring_job_stores_the_signal_and_the_web_reads_it(authenticated_async_context, fixture_user_id):
    _seed_countries(fixture_user_id, ["GB", "IN"])
    async with authenticated_async_context() as client:
        app_id = await _bring(
            client, visa_signal="no_sponsorship", visa_country="de",
            visa_detail="We cannot offer visa sponsorship for this role.",
        )
        detail = await _detail(client, app_id)
        listed = await client.get("/api/applications")
    assert detail["visa"] == {
        "signal": "no_sponsorship",
        "detail": "We cannot offer visa sponsorship for this role.",
        "country": "DE",
        "recorded_by": detail["visa"]["recorded_by"],
        "recorded_at": detail["visa"]["recorded_at"],
        "needs_sponsorship": True,
    }
    row = next(a for a in listed.json()["applications"] if a["id"] == app_id)
    assert (row["visa_signal"], row["visa_country"], row["needs_sponsorship"]) == ("no_sponsorship", "DE", True)


@pytest.mark.asyncio
async def test_unknown_is_the_default_and_reads_as_nothing_to_say(authenticated_async_context, fixture_user_id):
    _seed_countries(fixture_user_id, ["GB"])
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        detail = await _detail(client, app_id)
    assert detail["visa"]["signal"] == "unknown"
    assert detail["visa"]["needs_sponsorship"] is None


@pytest.mark.asyncio
async def test_covered_country_reads_false_and_no_list_reads_none(authenticated_async_context, fixture_user_id):
    _seed_countries(fixture_user_id, ["DE"])
    async with authenticated_async_context() as client:
        app_id = await _bring(client, visa_signal="no_sponsorship", visa_country="DE")
        covered = await _detail(client, app_id)
        # the candidate clears their list → nothing to compare against
        cleared = await client.post(
            "/api/profile", files={"preferences": (None, '{"work_authorization_countries": []}')}
        )
        assert cleared.status_code == 200, cleared.text
        silent = await _detail(client, app_id)
    assert covered["visa"]["needs_sponsorship"] is False
    assert silent["visa"]["needs_sponsorship"] is None


@pytest.mark.asyncio
async def test_save_fit_changes_the_slot_and_the_event_carries_it(authenticated_async_context, fixture_user_id):
    _seed_countries(fixture_user_id, ["GB"])
    async with authenticated_async_context() as client:
        app_id = await _bring(client, visa_signal="no_sponsorship", visa_country="DE")
        fit = await client.put(
            f"/api/applications/{app_id}/fit",
            json={"score": 70, "verdict": "good", "visa_signal": "sponsors", "visa_country": "DE",
                  "visa_detail": "Visa sponsorship available."},
        )
        assert fit.status_code == 200, fit.text
        detail = await _detail(client, app_id)
    assert fit.json()["visa"]["signal"] == "sponsors"
    assert detail["visa"]["signal"] == "sponsors"
    assert detail["visa"]["detail"] == "Visa sponsorship available."
    judged = [e for e in detail["events"] if e["event_type"] == "fit_judged"]
    assert judged[-1]["payload"]["visa"] == {
        "signal": "sponsors", "detail": "Visa sponsorship available.", "country": "DE",
    }


@pytest.mark.asyncio
async def test_save_fit_without_visa_leaves_the_slot_alone(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client, visa_signal="no_sponsorship", visa_country="DE")
        fit = await client.put(f"/api/applications/{app_id}/fit", json={"score": 40})
        assert fit.status_code == 200, fit.text
        detail = await _detail(client, app_id)
    assert fit.json()["visa"] is None
    assert detail["visa"]["signal"] == "no_sponsorship"
    assert "visa" not in [e for e in detail["events"] if e["event_type"] == "fit_judged"][-1]["payload"]


@pytest.mark.asyncio
async def test_the_web_dropdown_door_sets_it_too(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        put = await client.put(f"/api/applications/{app_id}/visa", json={"visa_signal": "sponsors"})
        assert put.status_code == 200, put.text
        detail = await _detail(client, app_id)
    assert put.json()["visa"]["signal"] == "sponsors"
    assert detail["visa"]["signal"] == "sponsors"


@pytest.mark.asyncio
async def test_junk_is_422_on_every_door(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        bad_signal = await client.post("/api/jobs/bring", json={**_AD, "visa_signal": "maybe"})
        bad_country = await client.put(f"/api/applications/{app_id}/fit", json={"visa_signal": "sponsors", "visa_country": "Germany"})
        long_detail = await client.put(
            f"/api/applications/{app_id}/visa",
            json={"visa_signal": "sponsors", "visa_detail": "x" * (settings.APPLICATION_VISA_DETAIL_MAX_CHARS + 1)},
        )
        bad_list = await client.post(
            "/api/profile", files={"preferences": (None, '{"work_authorization_countries": ["United Kingdom"]}')}
        )
    assert bad_signal.status_code == 422
    assert bad_country.status_code == 422
    assert long_detail.status_code == 422
    assert bad_list.status_code == 422


@pytest.mark.asyncio
async def test_update_profile_can_set_the_countries(authenticated_async_context, fixture_user_id):
    _seed_countries(fixture_user_id, [])
    async with authenticated_async_context() as client:
        patched = await client.patch(
            "/api/profile",
            json={"edits": [{"path": "preferences.work_authorization_countries", "value": ["gb", "IN"]}]},
        )
        assert patched.status_code == 200, patched.text
        profile = await client.get("/api/profile")
        app_id = await _bring(client, visa_signal="no_sponsorship", visa_country="IN")
        detail = await _detail(client, app_id)
    assert profile.json()["preferences"]["work_authorization_countries"] == ["gb", "IN"]
    assert detail["visa"]["needs_sponsorship"] is False


@pytest.mark.asyncio
async def test_a_second_user_cannot_set_or_see_it(authenticated_async_context):
    from tests.test_application_spine import _second_user_session_cookie, _session_client

    async with authenticated_async_context() as client:
        app_id = await _bring(client, visa_signal="no_sponsorship", visa_country="DE")
        cookie = await _second_user_session_cookie("visa-second@example.com")
        async with _session_client(cookie) as other:
            put = await other.put(f"/api/applications/{app_id}/visa", json={"visa_signal": "sponsors"})
            get = await other.get(f"/api/applications/{app_id}")
    assert put.status_code == 404
    assert get.status_code == 404


def test_no_country_rule_lives_in_the_code():
    """The product rule, pinned: Job360 knows no visa rule for any country.
    The whole visa module names no country and no visa vocabulary beyond the
    three signals — the only comparison is list membership."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "src" / "services" / "applications" / "visa.py").read_text(
        encoding="utf-8"
    )
    for word in ("skilled worker", "h-1b", "h1b", "blue card", "tier 2", "ilr", "green card"):
        assert word not in src.lower()
    assert "@mcp.tool" not in src
