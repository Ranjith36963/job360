"""Owner 2026-11-09: an outreach message that goes out AUTOMATICALLY needs a strong ``outreach_reviewed`` first, the
same way a CV needs ``cv_reviewed``. A fresh reviewer quotes the profile for every line; Job360 runs no model, it
stores the verdict and VERIFIES it (services/applications/outreach_review.py). Real Postgres, real doors (HTTP and
the MCP tools), value asserts. Helpers are copied, never imported from another test module."""
from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from src.services.applications import outreach_review
from src.services.profile.models import CVData, UserPreferences, UserProfile
from src.services.profile.storage import save_profile

SEND = "assistant_settings.outreach_send"
_AD = {
    "title": "Data Engineer", "company": "Northwind", "location": "Remote",
    "apply_url": "https://northwind.example/careers/7",
    "description": "Build the pipelines. Python, dbt, Snowflake. Fully remote.",
}
RAW = ("Data engineer with 2 years of Python experience at Acme. "
       "Built dbt pipelines on Snowflake for the finance team.")
LINE_1 = "I am a data engineer with 2 years of Python experience at Acme."
LINE_2 = "I built dbt pipelines on Snowflake for the finance team."
QUOTE_1 = "Data engineer with 2 years of Python experience at Acme"
QUOTE_2 = "Built dbt pipelines on Snowflake for the finance team"
EMAIL = f"Hi Rita,\n{LINE_1}\n{LINE_2}\nBest regards,\nAda Lovelace"


def _seed_profile(user_id: str) -> None:
    save_profile(
        UserProfile(
            cv_data=CVData(raw_text=RAW, name="Ada Lovelace", skills=["Python"]),
            preferences=UserPreferences(target_job_titles=["Data Engineer"]),
        ),
        user_id,
        source_action="cv_upload",
    )


async def _auto_ready(client: AsyncClient, user_id: str) -> tuple[int, int]:
    """outreach_send auto, one job, one linked person with an email and a found_via: every other rule says send."""
    _seed_profile(user_id)
    resp = await client.patch("/api/profile", json={"edits": [{"path": SEND, "value": "auto"}]})
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


async def _draft(client: AsyncClient, app_id: int, cid: int, text: str = EMAIL, channel: str = "email") -> int:
    resp = await client.post(
        f"/api/applications/{app_id}/artifacts",
        json={"kind": "outreach", "contact_id": cid, "channel": channel, "text": text},
    )
    assert resp.status_code == 201, resp.text
    return int(resp.json()["artifact_id"])


async def _check(client: AsyncClient, cid: int, mid: int, session: str = "") -> dict[str, Any]:
    resp = await client.get(f"/api/contacts/{cid}/outreach-check", params={"message_id": mid, "session": session})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _review_block(client: AsyncClient, cid: int) -> dict[str, Any]:
    resp = await client.get("/api/people", params={"contact_id": cid})
    assert resp.status_code == 200, resp.text
    return resp.json()["person"]["review"]


def _rows(*pairs: tuple[str, str]) -> list[dict[str, str]]:
    return [{"msg_line": line, "profile_quote": quote, "field_path": "cv_data.raw_text"} for line, quote in pairs]


def _payload(message_id: int, sha: str, lines: list[dict[str, str]], confidence: str = "strong") -> dict[str, Any]:
    return {"message_id": message_id, "sha256": sha, "confidence": confidence, "lines": lines, "reviewer": "fresh-1"}


async def _record(client: AsyncClient, app_id: int, cid: int, payload: dict[str, Any]):
    return await client.post(
        f"/api/applications/{app_id}/events",
        json={"event_type": "outreach_reviewed", "contact_id": cid, "payload": payload},
    )


def _reviews(cid: int) -> list[dict[str, Any]]:
    import json

    from src.core import settings
    from src.repositories import pgsync

    conn = pgsync.connect(str(settings.DB_PATH))
    try:
        got = conn.execute(
            "SELECT text FROM contact_outreach WHERE contact_id = ? AND entry = ? ORDER BY id", (cid, "reviewed")
        ).fetchall()
    finally:
        conn.close()
    return [json.loads(r[0]) for r in got]


# ── the gate ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_auto_mode_email_with_no_review_asks_outreach_not_reviewed(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id, cid = await _auto_ready(client, fixture_user_id)
        mid = await _draft(client, app_id, cid)
        verdict = await _check(client, cid, mid)
    assert (verdict["decision"], verdict["reason"]) == ("ask", "outreach_not_reviewed")
    assert verdict["detail"].startswith("No strong outreach_reviewed of this exact draft (review: none).")
    assert "Save a Gmail draft" in verdict["detail"]


@pytest.mark.asyncio
async def test_a_strong_review_of_the_exact_draft_sends(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id, cid = await _auto_ready(client, fixture_user_id)
        mid = await _draft(client, app_id, cid)
        block = await _review_block(client, cid)
        import hashlib

        assert block == {
            "message_id": mid, "sha256": hashlib.sha256(EMAIL.encode("utf-8")).hexdigest(),
            "lines": [LINE_1, LINE_2], "state": "none",
        }  # greeting, sign-off and the user's own name need no quote
        resp = await _record(client, app_id, cid, _payload(mid, block["sha256"], _rows((LINE_1, QUOTE_1),
                                                                                         (LINE_2, QUOTE_2))))
        assert resp.status_code == 201, resp.text
        assert resp.json()["event_type"] == "outreach_reviewed" and resp.json()["warnings"] == []
        assert (await _review_block(client, cid))["state"] == "strong"
        verdict = await _check(client, cid, mid)
        events = (await client.get(f"/api/applications/{app_id}")).json()["events"]
    assert (verdict["decision"], verdict["reason"]) == ("send", "auto_mode")
    [stored] = _reviews(cid)
    assert (stored["confidence"], stored["downgraded"], stored["covered"], stored["content_lines"]) == (
        "strong", [], 2, 2)
    assert "Acme" not in str(stored), "codes and counts only, never message or profile text"
    [timeline] = [e for e in events if e["event_type"] == "outreach_reviewed"]
    assert (timeline["payload"]["contact_id"], timeline["payload"]["message_id"]) == (cid, mid)


@pytest.mark.asyncio
async def test_a_review_of_an_older_draft_or_a_wrong_sha_is_refused_and_never_clears(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        app_id, cid = await _auto_ready(client, fixture_user_id)
        old = await _draft(client, app_id, cid)
        old_sha = (await _review_block(client, cid))["sha256"]
        new = await _draft(client, app_id, cid, EMAIL + "\nThanks")
        lines = _rows((LINE_1, QUOTE_1), (LINE_2, QUOTE_2))
        stale = await _record(client, app_id, cid, _payload(old, old_sha, lines))
        wrong_sha = await _record(client, app_id, cid, _payload(new, old_sha, lines))
        verdict = await _check(client, cid, new)
    for resp in (stale, wrong_sha):
        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"].startswith("not_latest_draft:")
    assert _reviews(cid) == [], "a refused review is never stored"
    assert (verdict["decision"], verdict["reason"]) == ("ask", "outreach_not_reviewed")


@pytest.mark.asyncio
async def test_a_review_missing_a_line_is_stored_weak_and_does_not_send(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id, cid = await _auto_ready(client, fixture_user_id)
        mid = await _draft(client, app_id, cid)
        sha = (await _review_block(client, cid))["sha256"]
        resp = await _record(client, app_id, cid, _payload(mid, sha, _rows((LINE_1, QUOTE_1))))
        assert resp.status_code == 201, resp.text
        verdict = await _check(client, cid, mid)
        state = (await _review_block(client, cid))["state"]
    assert resp.json()["warnings"][0].startswith("outreach_reviewed stored weak: 1 of 2 required lines had no row")
    [stored] = _reviews(cid)
    assert (stored["claimed"], stored["confidence"], stored["downgraded"]) == ("strong", "weak", ["line_uncovered"])
    assert state == "weak"
    assert (verdict["decision"], verdict["reason"]) == ("ask", "outreach_not_reviewed")
    assert "(review: weak)" in verdict["detail"]


@pytest.mark.asyncio
async def test_a_fabricated_profile_quote_is_stored_weak(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id, cid = await _auto_ready(client, fixture_user_id)
        mid = await _draft(client, app_id, cid)
        sha = (await _review_block(client, cid))["sha256"]
        made_up = "Data engineer with 2 years of Python experience at Acme and Google"
        resp = await _record(client, app_id, cid, _payload(mid, sha, _rows((LINE_1, made_up), (LINE_2, QUOTE_2))))
        assert resp.status_code == 201, resp.text
        verdict = await _check(client, cid, mid)
    [stored] = _reviews(cid)
    assert (stored["confidence"], stored["downgraded"]) == ("weak", ["quote_not_in_profile"])
    assert (verdict["decision"], verdict["reason"]) == ("ask", "outreach_not_reviewed")


@pytest.mark.asyncio
async def test_a_quote_joining_two_profile_lines_is_not_in_profile(authenticated_async_context, fixture_user_id):
    """A quote lies inside ONE profile field or line (cv_review.quote_sources): the end of one line glued to the
    start of the next reads as one claim the profile never makes."""
    async with authenticated_async_context() as client:
        app_id, cid = await _auto_ready(client, fixture_user_id)
        save_profile(UserProfile(cv_data=CVData(raw_text="Data engineer at Acme for 2 years\nLed the Globex migration",
                                                name="Ada Lovelace")), fixture_user_id, source_action="cv_upload")
        line = "I spent 2 years and then led the Globex migration."
        mid = await _draft(client, app_id, cid, f"Hi Rita,\n{line}\nBest regards,\nAda Lovelace")
        sha = (await _review_block(client, cid))["sha256"]
        spans = "for 2 years Led the Globex migration"
        resp = await _record(client, app_id, cid, _payload(mid, sha, _rows((line, spans))))
        assert resp.status_code == 201, resp.text
        verdict = await _check(client, cid, mid)
    [stored] = _reviews(cid)
    assert (stored["confidence"], stored["downgraded"]) == ("weak", ["quote_not_in_profile"])
    assert (verdict["decision"], verdict["reason"]) == ("ask", "outreach_not_reviewed")


@pytest.mark.asyncio
async def test_outreach_rows_keep_their_own_shape_not_the_cv_rows(authenticated_async_context, fixture_user_id):
    """cv_reviewed rows became {n, verdict, evidence} (PR #1554); outreach_reviewed keeps {msg_line, profile_quote,
    field_path}: a CV-shaped row is a 422 naming the outreach shape, and nothing is stored."""
    async with authenticated_async_context() as client:
        app_id, cid = await _auto_ready(client, fixture_user_id)
        mid = await _draft(client, app_id, cid)
        sha = (await _review_block(client, cid))["sha256"]
        cv_row = {"n": 1, "verdict": "supported", "evidence": [QUOTE_1]}
        resp = await _record(client, app_id, cid, _payload(mid, sha, [cv_row]))
        long_quote = await _record(client, app_id, cid, _payload(mid, sha, _rows((LINE_1, "x" * 601))))
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"] == "lines[0] takes exactly {msg_line, profile_quote, field_path}"
    assert long_quote.status_code == 422, long_quote.text
    assert long_quote.json()["detail"] == "lines[0].profile_quote must be text of 1-600 characters"
    assert _reviews(cid) == []


@pytest.mark.asyncio
async def test_a_claim_the_quote_does_not_carry_is_weak(authenticated_async_context, fixture_user_id):
    """A real profile quote pinned to a line it does not back (the line's number is not in it) is weak."""
    async with authenticated_async_context() as client:
        app_id, cid = await _auto_ready(client, fixture_user_id)
        mid = await _draft(client, app_id, cid)
        sha = (await _review_block(client, cid))["sha256"]
        resp = await _record(client, app_id, cid, _payload(mid, sha, _rows((LINE_1, QUOTE_2), (LINE_2, QUOTE_2))))
        assert resp.status_code == 201, resp.text
    [stored] = _reviews(cid)
    assert (stored["confidence"], stored["downgraded"]) == ("weak", ["claim_not_in_quote"])


@pytest.mark.asyncio
async def test_after_three_weak_drafts_a_fourth_review_stays_weak(authenticated_async_context, fixture_user_id):
    """Owner: up to two rewrites, each re-reviewed (three reviews); then it waits for the user."""
    async with authenticated_async_context() as client:
        app_id, cid = await _auto_ready(client, fixture_user_id)
        for n in range(outreach_review.MAX_WEAK):
            mid = await _draft(client, app_id, cid, EMAIL + "\nThanks" * (n + 1))
            sha = (await _review_block(client, cid))["sha256"]
            weak = await _record(client, app_id, cid, _payload(mid, sha, _rows((LINE_1, QUOTE_1),
                                                                                 (LINE_2, QUOTE_2)), "weak"))
            assert weak.status_code == 201, weak.text
        mid = await _draft(client, app_id, cid)
        sha = (await _review_block(client, cid))["sha256"]
        resp = await _record(client, app_id, cid, _payload(mid, sha, _rows((LINE_1, QUOTE_1), (LINE_2, QUOTE_2))))
        assert resp.status_code == 201, resp.text
        verdict = await _check(client, cid, mid)
    assert (_reviews(cid)[-1]["confidence"], _reviews(cid)[-1]["downgraded"]) == ("weak", ["retry_limit"])
    assert (verdict["decision"], verdict["reason"]) == ("ask", "outreach_not_reviewed")


@pytest.mark.asyncio
async def test_owner_clicked_linkedin_still_sends_without_a_review(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id, cid = await _auto_ready(client, fixture_user_id)
        patched = await client.patch(
            f"/api/contacts/{cid}",
            json={"linkedin_url": "https://www.linkedin.com/in/rita", "linkedin_state": "connected"},
        )
        assert patched.status_code == 200, patched.text
        mid = await _draft(client, app_id, cid, f"Hi Rita, {LINE_2}", channel="linkedin")
        click = await client.post(f"/api/contacts/{cid}/own-browser-send", json={"message_id": mid,
                                                                                "decision": "approve"})
        assert click.status_code == 200, click.text
        took = await client.post("/api/own-browser/take", json={"session": "desk", "contact_id": cid})
        assert took.status_code == 200 and took.json()["status"] == "task", took.text
        verdict = await _check(client, cid, mid, "desk")
    assert (verdict["decision"], verdict["reason"]) == ("send", "owner_clicked")
    assert _reviews(cid) == []


@pytest.mark.asyncio
async def test_record_event_refuses_outreach_reviewed_without_a_contact_or_with_extras(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        app_id, cid = await _auto_ready(client, fixture_user_id)
        mid = await _draft(client, app_id, cid)
        sha = (await _review_block(client, cid))["sha256"]
        payload = _payload(mid, sha, _rows((LINE_1, QUOTE_1), (LINE_2, QUOTE_2)))
        no_contact = await client.post(
            f"/api/applications/{app_id}/events", json={"event_type": "outreach_reviewed", "payload": payload}
        )
        with_detail = await client.post(
            f"/api/applications/{app_id}/events",
            json={"event_type": "outreach_reviewed", "contact_id": cid, "payload": payload, "detail": "x"},
        )
        bad_shape = await _record(client, app_id, cid, {**payload, "artifact_id": mid})
    assert no_contact.status_code == 422 and "contact_id" in no_contact.json()["detail"]
    assert with_detail.status_code == 422 and with_detail.json()["detail"].startswith("outreach_reviewed takes only")
    assert bad_shape.status_code == 422
    assert bad_shape.json()["detail"].startswith(
        "outreach_reviewed takes exactly {message_id, sha256, confidence, lines, reviewer}")
    assert _reviews(cid) == []


@pytest.mark.asyncio
async def test_a_cold_contacts_review_goes_through_the_mcp_record_event_tool(
    authenticated_async_context, fixture_user_id
):
    """No job: the record_event tool (no application_id) reaches the contact door; another user's contact is 404."""
    from src.api import mcp_server

    user = mcp_server.CurrentUser(
        id=fixture_user_id, email="e2e@example.com", email_verified=True, auth_via="token", actor_name="cc",
    )
    tools = mcp_server.build_server()._tool_manager
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        made = await client.post(
            "/api/contacts", json={"name": "Cy Cold", "company": "Contoso", "email": "cy@contoso.example",
                                   "found_via": "linkedin"},
        )
        assert made.status_code == 201, made.text
        cid = int(made.json()["contact"]["id"])
        msg = await client.post(f"/api/contacts/{cid}/outreach",
                                json={"entry": "message", "channel": "email", "text": f"Hi Cy,\n{LINE_2}"})
        assert msg.status_code == 201, msg.text
        mid = int(msg.json()["outreach"]["id"])
        token = mcp_server._current_user.set(user)
        try:
            people = await tools.get_tool("list_people").fn(contact_id=cid)
            block = people["person"]["review"]
            assert (block["message_id"], block["lines"], block["state"]) == (mid, [LINE_2], "none")
            got = await tools.get_tool("record_event").fn(
                event_type="outreach_reviewed", contact_id=cid,
                payload=_payload(mid, block["sha256"], _rows((LINE_2, QUOTE_2))),
            )
        finally:
            mcp_server._current_user.reset(token)
        state = (await _review_block(client, cid))["state"]
    assert (got["event_type"], got["event_id"], got["warnings"]) == ("outreach_reviewed", None, [])
    assert state == "strong"


# ── which lines need a quote (pure) ───────────────────────────────────────────


def test_only_a_greeting_a_sign_off_the_users_name_and_contact_lines_are_exempt():
    profile = UserProfile(cv_data=CVData(raw_text=RAW, name="Ada Lovelace"))
    text = "\n".join([
        "Hi Rita,", LINE_1, "Hi Rita, I led the Acme migration.", "Best regards,", "Ada Lovelace",
        "ada@lovelace.example | https://github.com/ada", "Senior Staff Engineer at Google",
    ])
    assert outreach_review.content_lines(text, profile, "Rita Recruiter") == [
        LINE_1, "Hi Rita, I led the Acme migration.", "Senior Staff Engineer at Google",
    ]
    # A greeting is only the FIRST line; a greeting naming someone else is content.
    assert outreach_review.content_lines("Hi Bob,\nThanks", profile, "Rita Recruiter") == ["Hi Bob,"]


def test_auto_send_reasons_are_the_automatic_ones_with_text():
    """The user's own Send (owner_clicked, connect_no_note) and a no-note request (auto_connect) need no review."""
    from src.services.profile import assistant_settings as rules

    sends = {"auto_mode", "owner_clicked", *rules.LINKEDIN_AUTO_SEND_REASONS}
    assert set(outreach_review.AUTO_SEND_REASONS) == sends - {"owner_clicked", "connect_no_note", "auto_connect"}
