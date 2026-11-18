"""An emailed one-time code from the EMPLOYER's own domain is accepted (owner, 2026-11-18).

Live run 2026-11-18 17:05 UTC: a WSP application form on an applicant-system host wanted a one-time code; the code
email came from wsp.com. ``sender_matches`` compared the sender only with the FORM's host and its applicant system,
so Job360 refused it (``sender_not_site``) and the run stalled. The employer's own domain is now a second accepted
sender: the registrable domain of the application's own job link when that link is the employer's own site (not an
applicant system, a job board or a shared host). Exact registrable-domain compares only; an unknown employer domain
keeps the old refusal; a third party stays refused.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.services.applications import email_codes as ec

NOW = datetime(2026, 11, 18, 17, 5, tzinfo=timezone.utc)
CODES = "assistant_settings.use_email_codes"
WSP_JOB = "https://www.wsp.com/en-gb/careers/job-opportunities/senior-engineer-123"
FORM_HOST = "careers-wsp.icims.com"
FORM = f"https://{FORM_HOST}/jobs/123/login"


def good(**over: Any) -> dict[str, Any]:
    base = {
        "to": "test@example.com", "sender": "WSP Careers <no-reply@wsp.com>",
        "subject": "Your verification code for your WSP application", "purpose": "verify_email",
        "form_host": FORM_HOST,
        "asked_at": (NOW - timedelta(minutes=3)).isoformat(), "received_at": (NOW - timedelta(minutes=2)).isoformat(),
    }
    return {**base, **over}


def _problems(payload: dict[str, Any], employer: str) -> list[str]:
    return ec.problems(payload, own_emails={"test@example.com"}, known_hosts={FORM_HOST, "www.wsp.com"}, now=NOW,
                       employer_domain=employer)


def test_the_employer_domain_is_the_job_links_own_site_only():
    assert ec.employer_domain(WSP_JOB) == "wsp.com"
    assert ec.employer_domain("https://careers.acme.co.uk/jobs/1") == "acme.co.uk"
    # an applicant system, a job board, a shared host or no link: the employer's domain is unknown
    for link in ("https://job-boards.greenhouse.io/northwind/jobs/7", "https://uk.indeed.com/viewjob?jk=1",
                 "https://www.linkedin.com/jobs/view/1", "", "not a link", "ftp://wsp.com/x"):
        assert ec.employer_domain(link) == "", link


def test_a_code_from_the_employers_own_domain_is_accepted_on_another_host():
    assert "sender_not_site" in _problems(good(), employer="")  # unknown employer domain: the old refusal
    assert _problems(good(), employer="wsp.com") == []
    assert _problems(good(sender="talent@mail.wsp.com"), employer="wsp.com") == []


@pytest.mark.parametrize("sender", [
    "no-reply@evil-wsp.com", "no-reply@wsp.com.attacker.io", "no-reply@notwsp.com", "no-reply@wsp.co",
    "no-reply@unrelated-recruiter.com", "no-reply@wsp.com@attacker.io", "nobody",
])
def test_look_alikes_and_third_parties_stay_refused(sender: str):
    assert "sender_not_site" in _problems(good(sender=sender), employer="wsp.com")


def test_a_forbidden_provider_is_never_the_employer():
    found = _problems(good(sender="no-reply@accounts.google.com"), employer="google.com")
    assert "sender_forbidden" in found


def test_the_match_names_its_reason():
    assert ec.sender_match("wsp.com", FORM_HOST, "wsp.com") == "employer_site"
    assert ec.sender_match("icims.com", FORM_HOST, "wsp.com") == "form_site"
    assert ec.sender_match("evil-wsp.com", FORM_HOST, "wsp.com") == "none"
    assert ec.sender_match("wsp.com", FORM_HOST, "") == "none"


def _agent(token: str) -> AsyncClient:
    from src.api.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"})


@pytest.mark.asyncio
async def test_the_route_accepts_the_employers_code_and_refuses_a_third_party(
    authenticated_async_context, fixture_user_id,
):
    from src.services.profile.models import CVData, UserProfile
    from src.services.profile.storage import save_profile

    async with authenticated_async_context() as client:
        save_profile(UserProfile(cv_data=CVData(raw_text="Ada Lovelace", name="Ada Lovelace")), fixture_user_id,
                     source_action="cv_upload")
        brought = await client.post("/api/jobs/bring", json={
            "title": "Senior Engineer", "company": "WSP", "location": "London", "apply_url": WSP_JOB,
            "description": "Design the bridges.",
        })
        assert brought.status_code == 200, brought.text
        app_id = int(brought.json()["application_id"])
        url = f"/api/applications/{app_id}/events"
        filled = await client.post(url, json={"event_type": "form_filled",
                                              "payload": {"form_url": FORM, "fields_count": 3}})
        assert filled.status_code == 201, filled.text
        assert (await client.patch("/api/profile", json={"edits": [{"path": CODES, "value": True}]})).status_code == 200
        now = datetime.now(timezone.utc)
        payload = good(asked_at=(now - timedelta(minutes=2)).isoformat(),
                       received_at=(now - timedelta(minutes=1)).isoformat())
        token = (await client.post("/api/tokens", json={"name": "claude-code"})).json()["token"]
        async with _agent(token) as agent:
            third = await agent.post(url, json={"event_type": "email_code_used",
                                                "payload": {**payload, "sender": "x@evil-wsp.com"}})
            assert third.status_code == 422 and "sender_not_site" in third.text, third.text
            ok = await agent.post(url, json={"event_type": "email_code_used", "payload": payload})
            assert ok.status_code == 201, ok.text
        rows = (await client.get(f"/api/applications/{app_id}")).json()["events"]
        stored = next(e for e in rows if e["event_type"] == "email_code_used")
        assert stored["payload"] == {"host": FORM_HOST, "sender_host": "wsp.com",
                                     "received_at": payload["received_at"], "purpose": "verify_email"}


@pytest.mark.asyncio
async def test_an_ats_job_link_gives_no_employer_domain_so_the_code_stays_refused(
    authenticated_async_context, fixture_user_id,
):
    from src.services.profile.models import CVData, UserProfile
    from src.services.profile.storage import save_profile

    async with authenticated_async_context() as client:
        save_profile(UserProfile(cv_data=CVData(raw_text="Ada Lovelace", name="Ada Lovelace")), fixture_user_id,
                     source_action="cv_upload")
        brought = await client.post("/api/jobs/bring", json={
            "title": "Senior Engineer", "company": "WSP", "location": "London", "apply_url": FORM,
            "description": "Design the bridges.",
        })
        assert brought.status_code == 200, brought.text
        app_id = int(brought.json()["application_id"])
        assert (await client.patch("/api/profile", json={"edits": [{"path": CODES, "value": True}]})).status_code == 200
        now = datetime.now(timezone.utc)
        payload = good(asked_at=(now - timedelta(minutes=2)).isoformat(),
                       received_at=(now - timedelta(minutes=1)).isoformat())
        token = (await client.post("/api/tokens", json={"name": "claude-code"})).json()["token"]
        async with _agent(token) as agent:
            refused = await agent.post(f"/api/applications/{app_id}/events",
                                       json={"event_type": "email_code_used", "payload": payload})
        assert refused.status_code == 422 and "sender_not_site" in refused.text, refused.text
