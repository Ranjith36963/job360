"""An outreach EMAIL gets the same code checks a LinkedIn message gets (owner 2026-11-05: "everything should have
one"): a name the profile lacks (``unsupported_claims``) or a private topic (``sensitive_topic``) makes check_outreach
answer `ask` in auto mode, never `send`. Real Postgres, real route, and the MCP tool. Helpers are copied, never
imported from another test module.
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
async def test_an_email_naming_an_employer_the_profile_lacks_asks(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id, cid = await _auto_ready(client, fixture_user_id)
        saved = await _draft(client, app_id, cid, "Hi Rita, I worked at Globex on Python pipelines and would like to talk.")
        verdict = await _check(client, cid, int(saved["artifact_id"]))
        assert (verdict["decision"], verdict["reason"]) == ("ask", "unsupported_claims")


@pytest.mark.asyncio
async def test_an_email_naming_the_hiring_company_as_a_past_employer_asks(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id, cid = await _auto_ready(client, fixture_user_id)
        saved = await _draft(client, app_id, cid, "Hi Rita, I worked at Northwind on Python pipelines.")
        verdict = await _check(client, cid, int(saved["artifact_id"]))
        assert (verdict["decision"], verdict["reason"]) == ("ask", "unsupported_claims")


@pytest.mark.parametrize("words", ["Do you sponsor a visa for this role?", "What salary range is it?",
                                   "I would need relocation support."])
@pytest.mark.asyncio
async def test_an_email_raising_a_private_topic_asks(authenticated_async_context, fixture_user_id, words):
    async with authenticated_async_context() as client:
        app_id, cid = await _auto_ready(client, fixture_user_id)
        saved = await _draft(client, app_id, cid, f"Hi Rita, I have 2 years of Python behind me. {words}")
        verdict = await _check(client, cid, int(saved["artifact_id"]))
        assert (verdict["decision"], verdict["reason"]) == ("ask", "sensitive_topic")


@pytest.mark.asyncio
async def test_a_true_plain_email_still_sends(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id, cid = await _auto_ready(client, fixture_user_id)
        saved = await _draft(client, app_id, cid, "Hi Rita, I have 2 years of Python at Acme and would like to talk.")
        verdict = await _check(client, cid, int(saved["artifact_id"]))
        assert (verdict["decision"], verdict["reason"]) == ("send", "auto_mode")


@pytest.mark.asyncio
async def test_an_email_with_no_profile_text_to_check_against_asks(authenticated_async_context, fixture_user_id):
    """Fail closed: with nothing to vouch for the email, it never goes on its own."""
    async with authenticated_async_context() as client:
        app_id, cid = await _auto_ready(client, fixture_user_id)
        save_profile(UserProfile(cv_data=CVData(raw_text="", name="Ada Lovelace")), fixture_user_id,
                     source_action="cv_upload")
        saved = await _draft(client, app_id, cid, "Hi Rita, I would like to talk about the data team.")
        verdict = await _check(client, cid, int(saved["artifact_id"]))
        assert (verdict["decision"], verdict["reason"]) == ("ask", "unsupported_claims")
