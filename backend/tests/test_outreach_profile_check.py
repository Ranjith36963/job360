"""Outreach drafts are checked against the owner's profile: a number the profile cannot back is flagged
on save (record_outreach) and makes check_outreach answer `ask`, never `send`.

Real Postgres, real route. Helpers are copied, never imported from another test module.
"""
from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from src.services.profile.models import CVData, UserPreferences, UserProfile
from src.services.profile.storage import save_profile

INBOX = "assistant_settings.outreach_send"
_AD = {
    "title": "Data Engineer", "company": "Northwind", "location": "Remote",
    "apply_url": "https://northwind.example/careers/7",
    "description": "Build the pipelines. Python, dbt, Snowflake. Fully remote.",
}
_GOOD = "Hi Rita, I have 2 years of Python data engineering behind me and would like to talk."
_BAD = "Hi Rita, I have 7 years of Python data engineering behind me and would like to talk."


def _seed_profile(user_id: str) -> None:
    save_profile(
        UserProfile(
            cv_data=CVData(
                raw_text="Data engineer with 2 years of Python experience at Acme.",
                name="Ada Lovelace", skills=["Python"],
            ),
            preferences=UserPreferences(target_job_titles=["Data Engineer"]),
        ),
        user_id,
        source_action="cv_upload",
    )


async def _auto_ready(client: AsyncClient, user_id: str) -> tuple[int, int]:
    _seed_profile(user_id)
    resp = await client.patch("/api/profile", json={"edits": [{"path": INBOX, "value": "auto"}]})
    assert resp.status_code == 200, resp.text
    brought = await client.post("/api/jobs/bring", json=_AD)
    assert brought.status_code == 200, brought.text
    app_id = int(brought.json()["application_id"])
    made = await client.post(
        "/api/contacts",
        json={"name": "Rita Recruiter", "role": "Recruiter", "email": "rita@northwind.example",
              "application_id": app_id, "found_via": "company_site"},
    )
    assert made.status_code == 201, made.text
    return app_id, int(made.json()["contact"]["id"])


async def _draft(client: AsyncClient, app_id: int, cid: int, text: str) -> dict[str, Any]:
    resp = await client.post(
        f"/api/applications/{app_id}/artifacts",
        json={"kind": "outreach", "contact_id": cid, "channel": "email", "text": text},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _check(client: AsyncClient, cid: int, mid: int) -> dict[str, Any]:
    resp = await client.get(f"/api/contacts/{cid}/outreach-check", params={"message_id": mid})
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_unsupported_years_make_check_outreach_ask(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id, cid = await _auto_ready(client, fixture_user_id)
        saved = await _draft(client, app_id, cid, _BAD)
        verdict = await _check(client, cid, int(saved["artifact_id"]))
        assert (verdict["decision"], verdict["reason"]) == ("ask", "unsupported_numbers")
        assert "7 years" in verdict["detail"]


@pytest.mark.asyncio
async def test_supported_numbers_keep_the_verdict(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id, cid = await _auto_ready(client, fixture_user_id)
        saved = await _draft(client, app_id, cid, _GOOD)
        verdict = await _check(client, cid, int(saved["artifact_id"]))
        assert (verdict["decision"], verdict["reason"]) == ("send", "auto_mode")


@pytest.mark.asyncio
async def test_record_outreach_flags_the_unsupported_number(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id, cid = await _auto_ready(client, fixture_user_id)
        bad = await _draft(client, app_id, cid, _BAD)
        codes = [c["code"] for c in bad["checks"]]
        assert "unsupported_years" in codes
        good = await _draft(client, app_id, cid, _GOOD)
        assert not {"unsupported_numbers", "unsupported_years"} & {c["code"] for c in good["checks"]}


@pytest.mark.asyncio
async def test_unsupported_percent_is_flagged_and_asks(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id, cid = await _auto_ready(client, fixture_user_id)
        saved = await _draft(client, app_id, cid, "Hi Rita, I cut pipeline cost by 40% at Acme.")
        assert "unsupported_numbers" in [c["code"] for c in saved["checks"]]
        verdict = await _check(client, cid, int(saved["artifact_id"]))
        assert (verdict["decision"], verdict["reason"]) == ("ask", "unsupported_numbers")
