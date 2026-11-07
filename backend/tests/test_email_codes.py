"""An emailed one-time code may be typed only under ``assistant_settings.use_email_codes`` (owner 2026-11-07).

Off by default; on only by the user's OK. With it on, ``email_code_used`` is stored only when the email is to the
user, from the form's own site or applicant system (exact host / registrable domain, never a look-alike), received
after the ask and inside 15 minutes, and is no account-security, payment or password-reset code. The code itself is
never a payload key and never stored. Real doors (HTTP), values asserted (rule #21).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.services.applications import email_codes as ec
from src.services.profile import assistant_settings as rules
from src.services.profile.models import AssistantSettings, CVData, UserProfile

NOW = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)
CODES = "assistant_settings.use_email_codes"
FORM = "https://job-boards.greenhouse.io/northwind/jobs/7"


def good(**over: Any) -> dict[str, Any]:
    base = {
        "to": "test@example.com", "sender": "Greenhouse <no-reply@greenhouse.io>",
        "subject": "Security code for your application to Northwind", "purpose": "apply",
        "form_host": "job-boards.greenhouse.io",
        "asked_at": (NOW - timedelta(minutes=3)).isoformat(), "received_at": (NOW - timedelta(minutes=2)).isoformat(),
    }
    return {**base, **over}


def _problems(payload: dict[str, Any], **kw: Any) -> list[str]:
    return ec.problems(payload, own_emails={"test@example.com"}, known_hosts={"job-boards.greenhouse.io",
                       "careers.acme.co.uk"}, now=kw.pop("now", NOW), **kw)


def test_the_setting_is_off_by_default_and_turning_it_on_is_riskier():
    assert AssistantSettings().use_email_codes is False
    assert rules.effective(AssistantSettings()).use_email_codes is False
    assert CODES in rules.SETTING_PATHS and CODES in rules.GATED_PATHS
    assert rules.classify_change(CODES, False, True) == "riskier"
    assert rules.classify_change(CODES, True, False) == "safer"


def test_a_code_from_the_form_site_inside_the_window_passes():
    assert _problems(good()) == []
    # the company's own site, another subdomain of the same registrable domain (a .co.uk is three labels)
    assert _problems(good(form_host="careers.acme.co.uk", sender="jobs@mail.acme.co.uk")) == []


@pytest.mark.parametrize("sender", [
    "no-reply@greenhouse.io.evil.com", "no-reply@notgreenhouse.io", "no-reply@greenhouse-io.com",
    "no-reply@other.co.uk", "no-reply@greenhouse.io@evil.com", "nobody", "x@GREENHOUSE.IO:443",
])
def test_look_alike_senders_are_refused(sender: str):
    assert "sender_not_site" in _problems(good(sender=sender))


def test_a_shared_country_suffix_is_not_a_registrable_domain():
    assert ec.registrable("acme.co.uk") == "acme.co.uk"
    assert ec.registrable("co.uk") == ""
    assert "sender_not_site" in _problems(good(form_host="careers.acme.co.uk", sender="a@evil.co.uk"))


@pytest.mark.parametrize("sender", [
    "no-reply@accounts.google.com", "account-security-noreply@accountprotection.microsoft.com",
    "noreply@email.apple.com", "security-noreply@linkedin.com", "service@paypal.com",
])
def test_account_security_and_payment_senders_are_refused(sender: str):
    host = ec.sender_domain(sender)
    found = ec.problems(good(sender=sender, form_host=host), own_emails={"test@example.com"},
                        known_hosts={host}, now=NOW)
    assert "sender_forbidden" in found


@pytest.mark.parametrize("subject", ["Reset your password", "Your bank card code", "Payment verification"])
def test_password_reset_and_payment_subjects_are_refused(subject: str):
    assert "not_an_application_code" in _problems(good(subject=subject))


def test_a_hosting_platform_subdomain_is_its_own_site():
    assert ec.registrable("acme.github.io") == "acme.github.io"
    assert not ec.sender_matches("evil.github.io", "acme.github.io")
    assert ec.sender_matches("mail.acme.github.io", "acme.github.io")


def test_the_fifteen_minute_window():
    asked = NOW - timedelta(minutes=20)
    assert "outside_window" in _problems(good(asked_at=asked.isoformat(),
                                              received_at=(asked + timedelta(minutes=16)).isoformat()))
    # received BEFORE the form asked: an older code, never this one
    assert "outside_window" in _problems(good(received_at=(NOW - timedelta(minutes=4)).isoformat()))
    # inside the window when it came, but too old now
    assert "outside_window" in _problems(good(), now=NOW + timedelta(minutes=14))
    assert _problems(good(), now=NOW + timedelta(minutes=12)) == []
    assert "time_unreadable" in _problems(good(received_at="2026-11-07T11:58:00"))  # no timezone


def test_other_rules_and_closed_keys():
    assert "not_to_user" in _problems(good(to="someone@else.com"))
    assert "form_host_unknown" in _problems(good(form_host="boards.greenhouse.io"))
    assert "purpose_not_allowed" in _problems(good(purpose="sign_in_reset"))
    assert "too_many_codes" in _problems(good(), used_last_hour=ec.MAX_PER_HOUR)
    assert _problems({**good(), "code": "123456"})[0].startswith("payload keys must be exactly")


def _seed(user_id: str) -> None:
    from src.services.profile.storage import save_profile

    save_profile(UserProfile(cv_data=CVData(raw_text="Ada Lovelace", name="Ada Lovelace")), user_id,
                 source_action="cv_upload")


def _agent(token: str) -> AsyncClient:
    from src.api.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"})


@pytest.mark.asyncio
async def test_the_route_refuses_while_off_and_stores_no_code_when_on(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        brought = await client.post("/api/jobs/bring", json={
            "title": "Data Engineer", "company": "Northwind", "location": "London", "apply_url": FORM,
            "description": "Build the pipelines.",
        })
        assert brought.status_code == 200, brought.text
        app_id = int(brought.json()["application_id"])
        now = datetime.now(timezone.utc)
        payload = good(asked_at=(now - timedelta(minutes=2)).isoformat(),
                       received_at=(now - timedelta(minutes=1)).isoformat())
        url = f"/api/applications/{app_id}/events"
        off = await client.post(url, json={"event_type": "email_code_used", "payload": payload})
        assert off.status_code == 403, off.text
        token = (await client.post("/api/tokens", json={"name": "claude-code"})).json()["token"]
        async with _agent(token) as agent:  # an assistant's ask waits for the user's OK
            assert (await agent.patch("/api/profile", json={"edits": [{"path": CODES, "value": True}]})).status_code == 200
        view = (await client.get("/api/assistant-settings")).json()
        assert view["use_email_codes"] == {"value": False, "effective": False}
        assert (await client.patch("/api/profile", json={"edits": [{"path": CODES, "value": True}]})).status_code == 200
        async with _agent(token) as agent:
            look = await agent.post(url, json={"event_type": "email_code_used",
                                               "payload": good(sender="a@greenhouse.io.evil.com")})
            assert look.status_code == 422 and "sender_not_site" in look.text
            for bad in ({"purpose": ["apply"]}, {"to": {"a": 1}}, {"received_at": 5}):
                odd = await agent.post(url, json={"event_type": "email_code_used", "payload": {**payload, **bad}})
                assert odd.status_code == 422 and f"{next(iter(bad))} must be a string" in odd.text, odd.text
            note = await agent.post(url, json={"event_type": "email_code_used", "payload": payload,
                                               "detail": "code 123456"})
            assert note.status_code == 422
            ok = await agent.post(url, json={"event_type": "email_code_used", "payload": payload})
            assert ok.status_code == 201, ok.text
        rows = (await client.get(f"/api/applications/{app_id}")).json()["events"]
        stored = next(e for e in rows if e["event_type"] == "email_code_used")
        assert stored["payload"] == {"host": "job-boards.greenhouse.io", "sender_host": "greenhouse.io",
                                     "received_at": payload["received_at"], "purpose": "apply"}
