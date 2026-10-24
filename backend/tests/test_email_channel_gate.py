"""The email-channel gate (gap roadmap slice 31, G078).

Email applications used to bypass check_submit entirely and the receipt had no
recipient column (a hole in hard line 1). Now an email application records
``email_drafted`` {to, subject, attachments: [{artifact_id, sha256}]} before
check_submit (``form_url = mailto:<to>``); the gate stops ``email_not_drafted``
without one; the web Send yes is bound to the draft's recipient and attachments
(``approval_stale`` / ``email``); and the receipt stores the recipient and the
Gmail message id and freezes the draft's attachments as ``files``.
Real doors (HTTP + MCP), real values (rule #21).
"""
from __future__ import annotations

import json
import logging
from dataclasses import replace
from typing import Any

import pytest

from src.services.profile import assistant_settings as rules
from tests.test_application_kit import (
    NOW,
    _bearer_client,
    _bring,
    _cfg,
    _event,
    _events,
    _mcp_client,
    _mint_token,
    _payload,
    _save,
    _seed_profile,
    sha,
)

TO = "jobs@northwind.example"
MAILTO = f"mailto:{TO}"
FORM = "https://careers.northwind.example/apply/7"
CV = "Ada Lovelace\nSenior data engineer\nPython, dbt, Snowflake."
CV_2 = "Ada Lovelace\nStaff data engineer\nPython, dbt, Snowflake, Airflow."
LETTER = "Dear Northwind, I would love to join."
LETTER_2 = "Dear Northwind, I would really love to join the data team."
COUNTS = rules.SubmitCounts(submitted_today=0, applied_since_auto_on=1)

_AD_OTHER = {
    "title": "Platform Engineer", "company": "Southwind", "location": "Berlin", "country": "DE", "remote": False,
    "apply_url": "https://southwind.example/c/3", "description": "Platforms. Go, Kubernetes.",
}


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[dict[str, Any]] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(dict(record.__dict__))


@pytest.fixture
def log_capture():
    """Every record on the root logger, so a test can prove the full address never reaches a log."""
    root, audit = logging.getLogger(), logging.getLogger("job360.audit")
    handler, old, old_audit = _Capture(), root.level, audit.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    audit.setLevel(logging.INFO)
    yield handler
    root.removeHandler(handler)
    root.setLevel(old)
    audit.setLevel(old_audit)


def _draft(to: str = TO, attachments: list[dict[str, Any]] | None = None, **extra: Any) -> dict[str, Any]:
    return {"to": to, "subject": "Application: Data Engineer - Ada Lovelace", "attachments": attachments or [],
            **extra}


def _att(artifact: dict[str, Any], text: str) -> dict[str, Any]:
    return {"artifact_id": int(artifact["artifact_id"]), "sha256": sha(text)}


async def _gate_route(client, app_id: int, form_url: str = MAILTO) -> dict[str, Any]:
    resp = await client.get(f"/api/applications/{app_id}/submit-check", params={"form_url": form_url})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _drafted(client, app_id: int, payload: dict[str, Any]) -> int:
    resp = await _event(client, app_id, "email_drafted", payload)
    assert resp.status_code == 201, resp.text
    return int(resp.json()["event_id"])


# ═══════════════════════════════════════════════════════════════════════════
# Pure: the address reading and the gate
# ═══════════════════════════════════════════════════════════════════════════


def test_an_email_address_is_read_strictly_and_lower_cased():
    assert rules.clean_email_address(" Jobs@NorthWind.Example ") == TO
    assert rules.clean_email_address("first.last+cv@mail.example.co.uk") == "first.last+cv@mail.example.co.uk"
    for bad in ("", "jobs", "Ada <jobs@northwind.example>", "a@b.example, c@d.example", "a@b.example;c@d.example",
                "jobs@localhost", "jobs@xn--nrthwind-n2a.example", "jobs@north wind.example", "jobs@[10.0.0.1]",
                "jöbs@northwind.example", '"a b"@northwind.example', "jobs@@northwind.example", ".jobs@northwind.example",
                "jobs@northwind.example/x"):
        assert rules.clean_email_address(bad) is None, bad
    assert rules.mailto_address("mailto:Jobs@NorthWind.example?subject=Hi") == TO
    assert rules.mailto_address("MAILTO:") == "" and rules.mailto_address("mailto:not an address") is None
    assert rules.is_mailto(" mailto:x@y.example") and not rules.is_mailto(FORM)


def _facts(**kw: Any) -> rules.SubmitFacts:
    """An email application: NO form was filled or swept (the form rules must not fire)."""
    base: dict[str, Any] = {"status": "considering", "has_receipt": False, "submit_override": None, "fit_score": 80}
    base.update(kw)
    return rules.SubmitFacts(**base)


def _gate(cfg: rules.EffectiveSettings, facts: rules.SubmitFacts, url: str = MAILTO, counts=COUNTS):
    out = rules.may_submit(cfg, facts, url, counts, now=NOW)
    return out.decision, out.reason


def test_the_email_gate_needs_a_draft_to_the_mailto_address():
    confirm = _cfg()
    assert _gate(confirm, _facts()) == ("stop", "email_not_drafted")
    drafted = _facts(email_to=TO)
    # No form_filled / form_swept / form_checked, yet no form stop: an email has no form.
    assert _gate(confirm, drafted) == ("ask", "submit_mode_confirm")
    assert _gate(confirm, drafted, "mailto:") == ("stop", "email_not_drafted"), "a bare mailto: is refused"
    # A web-form application cannot dodge its form rules by passing a mailto: address.
    assert _gate(confirm, replace(drafted, form_filled=True)) == ("stop", "form_not_swept")
    assert _gate(confirm, drafted, "mailto:hr@southwind.example") == ("stop", "email_not_drafted")
    assert _gate(confirm, drafted, "mailto:Ada <jobs@northwind.example>") == ("stop", "email_not_drafted")
    # A yes never clears the missing draft (a stop), but clears the confirm ask once there is one.
    assert _gate(confirm, _facts(approved=True)) == ("stop", "email_not_drafted")
    assert _gate(confirm, replace(drafted, approved=True)) == ("submit", "user_approved")
    # Every earlier stop still wins.
    assert _gate(_cfg(paused_until="until_resumed"), drafted) == ("stop", "paused")
    assert _gate(confirm, replace(drafted, submit_unconfirmed=True)) == ("stop", "submit_unconfirmed")


def test_auto_mode_sends_an_email_application_without_the_site_asks():
    auto = _cfg(submit_mode="auto_when_sure")
    drafted = _facts(email_to=TO, cv_seen=True, new_site=True, guessed_unverified=True)
    # No unknown_site (a mailto is not a http(s) site), no new_site_first_time, no answer_guessed (no form).
    assert _gate(auto, drafted) == ("submit", "auto_when_sure")
    assert _gate(auto, replace(drafted, cv_seen=False)) == ("ask", "cv_not_seen")
    assert _gate(auto, drafted, counts=rules.SubmitCounts(0, 0)) == ("ask", "practice_run")
    assert _gate(auto, _facts(cv_seen=True)) == ("stop", "email_not_drafted")


def test_a_stale_yes_asks_approval_stale_and_names_the_email():
    confirm = _cfg()
    out = rules.may_submit(confirm, _facts(email_to=TO, approval_stale="email"), MAILTO, COUNTS, now=NOW)
    assert (out.decision, out.reason) == ("ask", "approval_stale")
    assert "recipient or attachment" in out.detail and "Submit mode is confirm" in out.detail
    assert {"approval_stale", "email_not_drafted"} <= set(rules.REASONS)


def test_a_form_application_is_unaffected():
    confirm = _cfg()
    form = _facts(email_to=TO)  # even with a draft on record, a form url reads the form rules
    assert _gate(confirm, form, FORM) == ("stop", "form_not_recorded")
    filled = _facts(form_filled=True, form_swept=True, form_host="careers.northwind.example")
    assert _gate(confirm, filled, FORM) == ("ask", "submit_mode_confirm")
    assert _gate(confirm, replace(filled, approved=True), FORM) == ("submit", "user_approved")


# ═══════════════════════════════════════════════════════════════════════════
# The event: shape, attachments, audit
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_email_drafted_stores_recipient_subject_and_versions_never_the_body(
    authenticated_async_context, fixture_user_id, log_capture
):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        cv = await _save(client, app_id, "cv", CV)
        other_app = await _bring(client, _AD_OTHER)
        other_cv = await _save(client, other_app, "cv", CV)

        body = await _event(client, app_id, "email_drafted", {**_draft(), "body": "Dear team, ..."})
        assert body.status_code == 422 and "the body is never stored" in body.json()["detail"]
        assert "Valid example payload:" in body.json()["detail"]
        bad = await _event(client, app_id, "email_drafted", _draft(to="Ada <jobs@northwind.example>"))
        assert bad.status_code == 422 and "ONE email address" in bad.json()["detail"]
        too_many = await _event(client, app_id, "email_drafted", _draft(attachments=[_att(cv, CV)] * 6))
        assert too_many.status_code == 422
        wrong_sha = await _event(client, app_id, "email_drafted", _draft(attachments=[
            {"artifact_id": cv["artifact_id"], "sha256": sha("another text")}]))
        assert wrong_sha.status_code == 409, wrong_sha.text
        foreign = await _event(client, app_id, "email_drafted", _draft(attachments=[_att(other_cv, CV)]))
        assert foreign.status_code == 404, "another application's CV is never an attachment of this one"

        event_id = await _drafted(client, app_id, _draft(
            to="Jobs@NorthWind.example", attachments=[_att(cv, CV)], gmail_draft_id="r-8812"))
        detail = (await client.get(f"/api/applications/{app_id}")).json()
        stored = next(e for e in detail["events"] if e["id"] == event_id)
    assert stored["event_type"] == "email_drafted"
    assert stored["payload"] == {
        "to": TO, "subject": "Application: Data Engineer - Ada Lovelace",
        "attachments": [{"artifact_id": cv["artifact_id"], "sha256": sha(CV), "kind": "cv", "version": 1}],
        "gmail_draft_id": "r-8812", "by": "web",
    }
    audit = [r for r in _events(log_capture, "email_drafted") if r.get("event_id") == event_id]
    assert audit and audit[0]["to_domain"] == "northwind.example" and audit[0]["attachments_count"] == 1
    assert audit[0]["application_id"] == app_id and audit[0]["user_id"] == fixture_user_id
    assert TO not in json.dumps(log_capture.records, default=str), "the full address never reaches a log"


# ═══════════════════════════════════════════════════════════════════════════
# The gate through the real doors
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_an_email_application_without_a_draft_stops_on_the_route_and_the_tool(
    authenticated_async_context, fixture_user_id
):
    from src.api.mcp_server import mcp_runtime

    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        cv = await _save(client, app_id, "cv", CV)
        token = await _mint_token(client)
        route = await _gate_route(client, app_id)
    assert (route["decision"], route["reason"]) == ("fill", "email_not_drafted")
    assert "email_drafted" in route["detail"]
    async with mcp_runtime():
        async with _mcp_client(token) as mcp:
            tool = _payload(await mcp.call_tool("check_submit", {"application_id": app_id, "form_url": MAILTO}))
            assert (tool["decision"], tool["reason"], tool["detail"]) == (
                route["decision"], route["reason"], route["detail"])
            _payload(await mcp.call_tool("record_event", {
                "event_type": "email_drafted", "application_id": app_id,
                "payload": _draft(attachments=[_att(cv, CV)])}))
            after = _payload(await mcp.call_tool("check_submit", {"application_id": app_id, "form_url": MAILTO}))
            other = _payload(await mcp.call_tool("check_submit", {
                "application_id": app_id, "form_url": "mailto:hr@southwind.example"}))
    assert (after["decision"], after["reason"]) == ("ask", "submit_mode_confirm")
    assert (other["decision"], other["reason"]) == ("fill", "email_not_drafted")
    async with authenticated_async_context() as client:
        again = await _gate_route(client, app_id)
    assert (again["decision"], again["reason"]) == (after["decision"], after["reason"])


@pytest.mark.asyncio
async def test_the_web_yes_is_bound_to_the_drafts_recipient_and_attachments(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        cv = await _save(client, app_id, "cv", CV)
        letter = await _save(client, app_id, "cover_letter", LETTER)
        # A yes given before any draft does not cover the draft recorded after it.
        assert (await client.post(f"/api/applications/{app_id}/send/approve")).status_code == 201
        await _drafted(client, app_id, _draft(attachments=[_att(cv, CV), _att(letter, LETTER)]))
        early = await _gate_route(client, app_id)
        assert (early["decision"], early["reason"]) == ("ask", "approval_stale"), early
        assert "email" in early["detail"]

        assert (await client.post(f"/api/applications/{app_id}/send/approve")).status_code == 201
        ok = await _gate_route(client, app_id)
        assert (ok["decision"], ok["reason"]) == ("submit", "user_approved")
        # Same recipient and files, attachments in another order and a new subject: the yes holds.
        await _drafted(client, app_id, {**_draft(attachments=[_att(letter, LETTER), _att(cv, CV)]),
                                        "subject": "Data Engineer application"})
        assert (await _gate_route(client, app_id))["reason"] == "user_approved"

        # Another recipient: the yes no longer counts.
        await _drafted(client, app_id, _draft(to="hr@northwind.example", attachments=[_att(cv, CV), _att(letter, LETTER)]))
        moved = await _gate_route(client, app_id, "mailto:hr@northwind.example")
        assert (moved["decision"], moved["reason"]) == ("ask", "approval_stale")
        assert (await client.get(f"/api/applications/{app_id}/controls")).json()["cv"]["approved"] is None

        # A fresh yes covers it; then a changed attachment (a new letter version = another id + sha) voids it.
        assert (await client.post(f"/api/applications/{app_id}/send/approve")).status_code == 201
        assert (await _gate_route(client, app_id, "mailto:hr@northwind.example"))["reason"] == "user_approved"
        letter_2 = await _save(client, app_id, "cover_letter", LETTER_2)
        await _drafted(client, app_id, _draft(to="hr@northwind.example",
                                              attachments=[_att(cv, CV), _att(letter_2, LETTER_2)]))
        swapped = await _gate_route(client, app_id, "mailto:hr@northwind.example")
        assert (swapped["decision"], swapped["reason"]) == ("ask", "approval_stale")
        detail = (await client.get(f"/api/applications/{app_id}")).json()
    stamp = [e for e in detail["events"] if e["event_type"] == "submit_approved"][-1]["payload"]["fingerprint"]
    assert stamp["email_to"] == "hr@northwind.example"
    assert sorted(a["artifact_id"] for a in stamp["attachments"]) == sorted([cv["artifact_id"], letter["artifact_id"]])


@pytest.mark.asyncio
async def test_a_chat_yes_and_a_form_application_keep_working(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        await _save(client, app_id, "cv", CV)
        # A form application, no draft: the form rules exactly as before, and the web yes still clears the ask.
        assert (await _gate_route(client, app_id, FORM))["reason"] == "form_not_recorded"
        assert (await _event(client, app_id, "form_filled", {"form_url": FORM, "fields_count": 1})).status_code == 201
        assert (await _event(client, app_id, "form_swept", {"fields_checked": 1, "fixed": 0})).status_code == 201
        assert (await _gate_route(client, app_id, FORM))["reason"] == "submit_mode_confirm"
        assert (await client.post(f"/api/applications/{app_id}/send/approve")).status_code == 201
        assert (await _gate_route(client, app_id, FORM))["reason"] == "user_approved"
        email_app = await _bring(client, _AD_OTHER)
        cv_2 = await _save(client, email_app, "cv", CV_2)
        await _drafted(client, email_app, _draft(to="jobs@southwind.example", attachments=[_att(cv_2, CV_2)]))
        token = await _mint_token(client)
    # A chat yes (the user said yes in chat; no fingerprint) is bound to the CV, as before slice 31.
    async with _bearer_client(token) as agent:
        yes = await _event(agent, email_app, "submit_approved",
                           {"artifact_id": cv_2["artifact_id"], "sha256": sha(CV_2), "where": "chat"})
        assert yes.status_code == 201, yes.text
        verdict = await _gate_route(agent, email_app, "mailto:jobs@southwind.example")
    assert (verdict["decision"], verdict["reason"]) == ("submit", "user_approved")


# ═══════════════════════════════════════════════════════════════════════════
# The receipt
# ═══════════════════════════════════════════════════════════════════════════


async def _receipt(client, app_id: int, **body: Any) -> dict[str, Any]:
    resp = await client.post(f"/api/applications/{app_id}/receipt", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_the_receipt_takes_the_recipient_and_files_from_the_draft(
    authenticated_async_context, fixture_user_id, log_capture
):
    from src.api.mcp_server import mcp_runtime

    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        cv = await _save(client, app_id, "cv", CV)
        letter = await _save(client, app_id, "cover_letter", LETTER)
        draft_id = await _drafted(client, app_id, _draft(attachments=[_att(cv, CV), _att(letter, LETTER)]))
        await _save(client, app_id, "cv", CV_2)  # saved AFTER the email: the receipt keeps the attached v1
        token = await _mint_token(client)
        reply = await _receipt(client, app_id, channel="email", gmail_message_id="18c2f9a0b1d2e3f4",
                               request_id="email-send-1")
        assert reply["recipient"] == TO and reply["gmail_message_id"] == "18c2f9a0b1d2e3f4"
        assert f"recipient from email_drafted#{draft_id}" in reply["warnings"]
        assert reply["cv_artifact_id"] == cv["artifact_id"] and reply["cv_version_no"] == 1
        assert reply["cover_letter_artifact_id"] == letter["artifact_id"] and reply["cover_letter_sent"] is True
        assert reply["files"] == [
            {"kind": "cv", "artifact_id": cv["artifact_id"], "sha256": sha(CV), "fmt": "", "event_id": draft_id},
            {"kind": "cover_letter", "artifact_id": letter["artifact_id"], "sha256": sha(LETTER), "fmt": "",
             "event_id": draft_id},
        ]
        # Replay: the first reply, nothing written; another recipient under the same key is a 409.
        again = await _receipt(client, app_id, channel="email", gmail_message_id="18c2f9a0b1d2e3f4",
                               request_id="email-send-1")
        assert again["already_existed"] is True and again["receipt_id"] == reply["receipt_id"]
        assert again["recipient"] == TO
        clash = await client.post(f"/api/applications/{app_id}/receipt", json={
            "channel": "email", "gmail_message_id": "18c2f9a0b1d2e3f4", "request_id": "email-send-1",
            "recipient": "hr@northwind.example"})
        assert clash.status_code == 409, clash.text
        read = (await client.get(f"/api/receipts/{reply['receipt_id']}")).json()
        listed = (await client.get(f"/api/applications/{app_id}")).json()
    assert read["recipient"] == TO and read["gmail_message_id"] == "18c2f9a0b1d2e3f4" and read["channel"] == "email"
    assert read["cv_text"] == CV and len(read["files"]) == 2
    assert [e["event_type"] for e in listed["events"]].count("applied") == 1
    async with mcp_runtime():
        async with _mcp_client(token) as mcp:
            got = _payload(await mcp.call_tool("get_receipt", {"receipt_id": reply["receipt_id"]}))
    assert got["recipient"] == TO and got["gmail_message_id"] == "18c2f9a0b1d2e3f4"
    email_logs = [r for r in _events(log_capture, "receipt_email") if r.get("receipt_id") == reply["receipt_id"]]
    assert email_logs and email_logs[0]["to_domain"] == "northwind.example"
    assert email_logs[0]["recipient_from"] == "draft" and email_logs[0]["files_from_draft"] is True
    assert TO not in json.dumps(log_capture.records, default=str)


@pytest.mark.asyncio
async def test_a_given_recipient_that_differs_warns_and_mcp_takes_the_new_params(
    authenticated_async_context, fixture_user_id
):
    from src.api.mcp_server import mcp_runtime

    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        cv = await _save(client, app_id, "cv", CV)
        draft_id = await _drafted(client, app_id, _draft(attachments=[_att(cv, CV)]))
        token = await _mint_token(client)
        bad = await client.post(f"/api/applications/{app_id}/receipt",
                                json={"channel": "email", "recipient": "two@a.example, three@b.example"})
        assert bad.status_code == 422 and "ONE email address" in bad.text
    async with mcp_runtime():
        async with _mcp_client(token) as mcp:
            reply = _payload(await mcp.call_tool("record_application", {
                "application_id": app_id, "recipient": "HR@Northwind.example", "gmail_message_id": "<abc@mail.gmail.com>",
                "cv_artifact_id": cv["artifact_id"]}))
    # No channel given and no form: the draft names it.
    assert reply["channel"] == "email"
    assert f"channel not given: stored 'email' from email_drafted#{draft_id}" in reply["warnings"]
    assert reply["recipient"] == "hr@northwind.example" and reply["gmail_message_id"] == "<abc@mail.gmail.com>"
    assert any(w.startswith("recipient_mismatch") and TO in w for w in reply["warnings"])
    assert reply["files"][0]["event_id"] == draft_id


@pytest.mark.asyncio
async def test_a_non_email_receipt_is_unchanged(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        await _save(client, app_id, "cv", CV)
        reply = await _receipt(client, app_id, channel="company_site")
        read = (await client.get(f"/api/receipts/{reply['receipt_id']}")).json()
    assert reply["recipient"] == "" and reply["gmail_message_id"] == "" and reply["files"] == []
    assert read["recipient"] == "" and read["gmail_message_id"] == "" and read["channel"] == "company_site"
    assert not any("recipient" in w for w in reply["warnings"])


@pytest.mark.asyncio
async def test_a_kit_link_download_names_the_cv_and_the_draft_names_the_attached_letter(
    authenticated_async_context, fixture_user_id
):
    """The usual email flow: the assistant downloads the PDFs through the kit's links (server-written
    ``document_uploaded``, no host) and attaches them. The served CV keeps its byte hash; the letter, which a
    download alone never proves sent, is proven by the draft that attached it."""
    import hashlib

    from tests.test_application_kit import _anon_client, _kit, _token_of

    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        cv = await _save(client, app_id, "cv", CV)
        letter = await _save(client, app_id, "cover_letter", LETTER)
        kit = (await _kit(client, app_id)).json()
        async with _anon_client() as anon:
            served = {doc: (await anon.get(f"/api/files/{_token_of(kit, doc)}")).content
                      for doc in ("cv", "cover_letter")}
        assert all(data[:5] == b"%PDF-" for data in served.values())
        draft_id = await _drafted(client, app_id, _draft(attachments=[_att(cv, CV), _att(letter, LETTER)]))
        reply = await _receipt(client, app_id, channel="email")
    by_kind = {f["kind"]: f for f in reply["files"]}
    assert by_kind["cv"]["sha256"] == hashlib.sha256(served["cv"]).hexdigest() and by_kind["cv"]["fmt"] == "pdf"
    assert by_kind["cv"]["event_id"] != draft_id
    assert by_kind["cover_letter"] == {"kind": "cover_letter", "artifact_id": letter["artifact_id"],
                                       "sha256": sha(LETTER), "fmt": "", "event_id": draft_id}
    assert reply["cover_letter_sent"] is True and reply["recipient"] == TO
    assert not any("no upload of it" in w for w in reply["warnings"])
