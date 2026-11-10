"""A tailored CV goes without the owner's look only after two checks (owner decision 2026-11-05).

The code check, then a SEPARATE reviewer's ``cv_reviewed`` verdict, which Job360 verifies: ``strong`` is stored
only when (a) it names the current CV exactly, (b) every content line is quoted, (c) every quote is in the profile,
(d) every number and name of a line is in its own quote, and (e) the CV's code checks find nothing. With
``assistant_settings.cv_auto_approve`` on (web only to turn on), in auto mode, a stored strong review of the exact
current CV clears ``cv_not_seen`` and nothing else. Real doors (HTTP + MCP), values asserted (rule #21).
Helpers are copied, never imported from another test module.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.services.applications import cv_review
from src.services.profile import assistant_settings as rules
from src.services.profile.models import AssistantSettings, CVData, UserProfile

FORM = "https://careers.northwind.example/apply/7"
RAW = (
    "Ada Lovelace\nEXPERIENCE\nData engineer at Northwind since 2019.\n"
    "Built ETL pipelines in Python and Snowflake, cutting warehouse costs 30%.\n"
    "Worked on the warehouse migration and cut storage costs in half.\n"
    "CERTIFICATIONS\nGoogle Cloud Professional Data Engineer, 2022."
)
CV = (
    "Ada Lovelace\nada@example.com | +44 7700 900123 | linkedin.com/in/ada\nEXPERIENCE\n"
    "- Data engineer at Northwind since 2019.\n"
    "- Built ETL pipelines in Python and Snowflake, cutting warehouse costs 30%.\n"
)
L1 = {"cv_line": "- Data engineer at Northwind since 2019.", "profile_quote": "data engineer at  Northwind since 2019.",
      "field_path": "cv_data.raw_text"}
L2 = {"cv_line": "Built ETL pipelines in Python and Snowflake, cutting warehouse costs 30%.",
      "profile_quote": "Built ETL pipelines in Python and Snowflake, cutting warehouse costs 30%.",
      "field_path": "cv_data.raw_text"}
_AD = {
    "title": "Data Engineer", "company": "Northwind", "location": "London", "country": "GB", "remote": False,
    "apply_url": "https://northwind.example/careers/7", "description": "Build the pipelines. Python, Snowflake.",
}
CV_AUTO = "assistant_settings.cv_auto_approve"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _seed(user_id: str) -> None:
    from src.services.profile.storage import save_assistant_settings, save_profile

    save_profile(UserProfile(cv_data=CVData(raw_text=RAW, name="Ada Lovelace")), user_id, source_action="cv_upload")
    save_assistant_settings(user_id, AssistantSettings(apply_min_score=50, above_line="auto", below_line="auto"), "seed")


def _agent(token: str) -> AsyncClient:
    from src.api.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"})


async def _setup(client: AsyncClient, *, auto: bool = True, cv_auto: bool = True, cv: str = CV) -> tuple[int, int]:
    edits = [{"path": "assistant_settings.submit_mode", "value": "auto_when_sure"}] if auto else []
    edits += [{"path": CV_AUTO, "value": True}] if cv_auto else []
    if edits:
        assert (await client.patch("/api/profile", json={"edits": edits})).status_code == 200
    tag = sha(cv)[:8]  # one application per CV text
    ad = {**_AD, "title": f"Data Engineer ({tag})", "apply_url": f"{_AD['apply_url']}/{tag}"}
    brought = await client.post("/api/jobs/bring", json=ad)
    assert brought.status_code == 200, brought.text
    app_id = int(brought.json()["application_id"])
    fit = await client.put(f"/api/applications/{app_id}/fit", json={"score": 80, "verdict": "ok", "score_source": "full_ad"})
    assert fit.status_code == 200, fit.text
    return app_id, await _save_cv(client, app_id, cv)


async def _save_cv(client: AsyncClient, app_id: int, text: str) -> int:
    """Save a CV version, then fill and sweep the form with it (a new CV needs a new fill)."""
    resp = await client.post(f"/api/applications/{app_id}/artifacts", json={"kind": "cv", "text": text})
    assert resp.status_code == 201, resp.text
    for event_type, payload in (("form_filled", {"form_url": FORM, "fields_count": 1}),
                                ("form_swept", {"fields_checked": 1, "fixed": 0})):
        assert (await _event(client, app_id, event_type, payload)).status_code == 201
    return int(resp.json()["artifact_id"])


async def _event(client: AsyncClient, app_id: int, event_type: str, payload: dict[str, Any]):
    return await client.post(f"/api/applications/{app_id}/events", json={"event_type": event_type, "payload": payload})


def rows(text: str, pairs: list[dict[str, Any]], profile: Any = None) -> list[dict[str, Any]]:
    """``{cv_line, profile_quote}`` pairs as rows ``{n, verdict, evidence}``: ``n`` = that line's number in
    review.lines (rules 5). A pair whose line is not in the review is left as it is (a shape error)."""
    keys = [cv_review.line_key(x) for x in cv_review.content_lines(
        text, profile or UserProfile(cv_data=CVData(raw_text=RAW, name="Ada Lovelace")))]
    return [{"n": keys.index(cv_review.line_key(p["cv_line"])) + 1, "verdict": "supported",
             "evidence": [p["profile_quote"]]} if "cv_line" in p and cv_review.line_key(p["cv_line"]) in keys else p
            for p in pairs]


def _review(cv_id: int, text: str = CV, lines: Any = None, confidence: str = "strong") -> dict[str, Any]:
    return {"artifact_id": cv_id, "sha256": sha(text), "confidence": confidence,
            "lines": rows(text, [L1, L2] if lines is None else lines), "reviewer": "fresh helper"}


async def _stored(client: AsyncClient, app_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    resp = await _event(client, app_id, "cv_reviewed", payload)
    assert resp.status_code == 201, resp.text
    event_id = resp.json()["event_id"]
    events = (await client.get(f"/api/applications/{app_id}")).json()["events"]
    return next(e for e in events if e["id"] == event_id)["payload"]


def _mcp_client(token: str):
    import httpx2
    from mcp.client import Client
    from mcp.client.streamable_http import streamable_http_client

    from src.api.main import app

    http = httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://test",
                              headers={"Authorization": f"Bearer {token}"})
    return Client(streamable_http_client("http://test/api/mcp", http_client=http))


def _payload(result: Any) -> dict[str, Any]:
    assert not result.is_error, result.content[0].text
    return json.loads(result.content[0].text)


async def _gate(client: AsyncClient, app_id: int) -> dict[str, Any]:
    resp = await client.get(f"/api/applications/{app_id}/submit-check", params={"form_url": FORM})
    assert resp.status_code == 200, resp.text
    return resp.json()


# ── Pure: the narrow exemptions and the gate rule ────────────────────────────


def test_only_the_name_contact_lines_and_headings_are_exempt():
    profile = UserProfile(cv_data=CVData(name="Ada Lovelace", raw_text="Experience, projects and skills."))
    text = ("Ada Lovelace\nada@example.com | github.com/ada\n+44 7700 900123\nEXPERIENCE\n"
            "# Projects\nSkills:\nSummary: passionate data engineer\nLondon, UK\nData engineer\n2019 2020 2021\n"
            "AWS\nAda Lovelace")
    assert cv_review.content_lines(text, profile) == [
        "Summary: passionate data engineer", "London, UK", "Data engineer", "2019 2020 2021", "AWS", "Ada Lovelace",
    ]
    # A heading is exempt only when the profile already says every word of it; a label word is content.
    claims = ["AWS SOLUTIONS ARCHITECT", "# Google Cloud Certified", "Kubernetes Administrator:",
              "CTO: ada@example.com", "Phone: 07700 900123"]
    assert cv_review.content_lines("\n".join(["Ada Lovelace", *claims]), profile) == claims
    # A first line is the name only when it IS the profile's name; a headline is a content line.
    assert cv_review.content_lines("Senior Data Engineer\nPython", profile) == ["Senior Data Engineer", "Python"]
    assert cv_review.content_lines("Ada Lovelace", UserProfile()) == ["Ada Lovelace"]


def test_a_strong_review_clears_only_cv_not_seen_and_only_with_the_setting_in_auto_mode():
    cfg = rules.effective(AssistantSettings(apply_min_score=50, above_line="auto", below_line="auto",
                                            submit_mode="auto_when_sure", cv_auto_approve=True))
    base = rules.SubmitFacts(status="considering", has_receipt=False, submit_override=None, fit_score=80,
                             form_filled=True, form_swept=True, form_host="careers.northwind.example")
    counts = rules.SubmitCounts(submitted_today=0, applied_since_auto_on=1)
    now = datetime(2026, 11, 5, 12, tzinfo=timezone.utc)

    def gate(c: rules.EffectiveSettings, **kw: Any) -> tuple[str, str]:
        out = rules.may_submit(c, rules.SubmitFacts(**{**base.__dict__, **kw}), FORM, counts, now=now)
        return out.decision, out.reason

    assert gate(cfg) == ("ask", "cv_not_seen")
    assert gate(cfg, cv_review="strong") == ("submit", "auto_when_sure")
    assert gate(cfg, cv_review="weak") == ("ask", "cv_not_seen")
    assert gate(cfg, cv_review="strong", cv_unsupported=True) == ("ask", "cv_unsupported_claims")
    off = rules.effective(AssistantSettings(apply_min_score=50, above_line="auto", below_line="auto",
                                            submit_mode="auto_when_sure"))
    assert gate(off, cv_review="strong") == ("ask", "cv_not_seen")
    confirm = rules.effective(AssistantSettings(apply_min_score=50, above_line="auto", below_line="auto",
                                                cv_auto_approve=True))
    assert gate(confirm, cv_review="strong") == ("ask", "submit_mode_confirm")
    assert rules.classify_change(CV_AUTO, False, True) == "riskier"
    assert rules.classify_change(CV_AUTO, True, False) == "safer"
    assert rules.effective(AssistantSettings()).cv_auto_approve is False


def test_cv_not_seen_message_names_the_review_when_auto_approve_is_on():
    """With ``cv_auto_approve`` on, the ask tells the agent what clears it; the reason code never changes."""
    on = rules.effective(AssistantSettings(apply_min_score=50, above_line="auto", below_line="auto",
                                           submit_mode="auto_when_sure", cv_auto_approve=True))
    off = rules.effective(AssistantSettings(apply_min_score=50, above_line="auto", below_line="auto",
                                            submit_mode="auto_when_sure"))
    base = rules.SubmitFacts(status="considering", has_receipt=False, submit_override=None, fit_score=80,
                             form_filled=True, form_swept=True, form_host="careers.northwind.example")
    counts = rules.SubmitCounts(submitted_today=0, applied_since_auto_on=1)
    now = datetime(2026, 11, 5, 12, tzinfo=timezone.utc)

    def ask(c: rules.EffectiveSettings, **kw: Any) -> rules.SubmitDecision:
        return rules.may_submit(c, rules.SubmitFacts(**{**base.__dict__, **kw}), FORM, counts, now=now)

    none = ask(on, cv_review="none")
    assert (none.decision, none.reason) == ("ask", "cv_not_seen")
    for part in ('get_recipe("doctor")', "CV review", "FRESH reviewer", "record_event cv_reviewed",
                 "{artifact_id, sha256, confidence, lines, reviewer}", "weak after the second rewrite waits for the user"):
        assert part in none.detail, part
    weak = ask(on, cv_review="weak")
    assert (weak.decision, weak.reason) == ("ask", "cv_not_seen")
    assert "waits for the user" in weak.detail and "show the CV" in weak.detail
    assert "at most two rewrites" in weak.detail and "retry_limit" in weak.detail and "waits for the user" in weak.detail
    assert "record_event cv_reviewed" not in weak.detail
    old = "The user has not seen the latest CV. Show it, get the user's OK, then fill the form and ask yes."
    for review in ("none", "weak"):
        out = ask(off, cv_review=review)
        assert (out.decision, out.reason, out.detail) == ("ask", "cv_not_seen", old)
    strong = ask(on, cv_review="strong")
    assert (strong.decision, strong.reason) == ("submit", "auto_when_sure")


# ── Through the real doors ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_verified_strong_review_clears_cv_not_seen_through_http_and_mcp(
    authenticated_async_context, fixture_user_id
):
    from src.api.mcp_server import mcp_runtime

    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        app_id, cv_id = await _setup(client)
        assert (await _gate(client, app_id))["reason"] == "cv_not_seen"
        token = (await client.post("/api/tokens", json={"name": "claude-code"})).json()["token"]
    async with mcp_runtime():
        async with _mcp_client(token) as mcp:
            got = _payload(await mcp.call_tool("record_event", {
                "event_type": "cv_reviewed", "application_id": app_id, "payload": _review(cv_id)}))
    async with authenticated_async_context() as client:
        events = (await client.get(f"/api/applications/{app_id}")).json()["events"]
        stored = next(e for e in events if e["id"] == got["event_id"])["payload"]
        body = await _gate(client, app_id)
    assert stored == {
        "artifact_id": cv_id, "sha256": sha(CV), "claimed": "strong", "confidence": "strong", "downgraded": [],
        "where": {}, "lines": 2, "content_lines": 2, "covered": 2, "reviewer": "fresh helper", "by": "token:claude-code",
        "rules": cv_review.RULES,
    }
    assert "Snowflake" not in json.dumps(stored), "no CV or profile text is stored"
    # cv_not_seen is cleared; the next ask is the practice run (nothing else is cleared).
    assert (body["decision"], body["reason"]) == ("ask", "practice_run")
    assert body["autonomy_shadow"]["cv_review"] == "strong"


@pytest.mark.asyncio
async def test_each_check_forces_weak_with_its_code(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        app_id, cv_id = await _setup(client)
        summary = CV + "Passionate engineer who loves data.\n"
        other_app, other_cv = await _setup(client, cv=summary)
        invented = CV + "- Led a team of 12 engineers.\n"
        third_app, third_cv = await _setup(client, cv=invented)

        async def downgraded(app: int, payload: dict[str, Any]) -> list[str]:
            got = await _stored(client, app, payload)
            assert got["confidence"] == "weak" and got["claimed"] == "strong"
            return got["downgraded"]

        # (a) not this CV's text or id
        # (a) is refused with 422 and stored nowhere (stricter than the old stored-weak; see test_cv_review_inputs)
        for bad in ({**_review(cv_id), "sha256": sha("another text")}, _review(other_cv, summary)):
            refused = await _event(client, app_id, "cv_reviewed", bad)
            assert refused.status_code == 422 and "review.sha256" in refused.text
        # (b) a line with no quote: an uncovered bullet, and the owner's case, an unquoted summary line
        assert await downgraded(app_id, _review(cv_id, lines=[L1])) == ["line_uncovered"]
        assert await downgraded(other_app, _review(other_cv, summary)) == ["line_uncovered"]
        # a row naming no review line is refused, never stored (rules 5: rows name lines by number)
        stray = {"n": 3, "verdict": "supported", "evidence": ["Data engineer at Northwind since 2019."]}
        assert (await _event(client, app_id, "cv_reviewed", _review(cv_id, lines=[L1, L2, stray]))).status_code == 422
        # (c) a fabricated quote
        fake = {**L2, "profile_quote": "Built ETL pipelines in Python and Snowflake, cutting warehouse costs 60%."}
        assert await downgraded(app_id, _review(cv_id, lines=[L1, fake])) == ["quote_not_in_profile", "claim_not_in_quote"]
        # (d) a real quote that does not hold the line's number or names
        short = {**L2, "profile_quote": "Built ETL pipelines in Python"}
        assert await downgraded(app_id, _review(cv_id, lines=[L1, short])) == ["claim_not_in_quote"]
        # (e) the CV's code checks find a number the profile never says
        led = {"cv_line": "- Led a team of 12 engineers.", "profile_quote": "Data engineer", "field_path": "cv_data.raw_text"}
        got = await downgraded(third_app, _review(third_cv, invented, lines=[L1, L2, led]))
        assert got == ["quote_too_thin", "claim_not_in_quote", "cv_checks_found"]
        # the reviewer's own weak is stored weak with nothing downgraded
        own = await _stored(client, other_app, _review(other_cv, summary, lines=[L1, L2, {
            **L1, "cv_line": "Passionate engineer who loves data.", "profile_quote": "Data engineer"}], confidence="weak"))
        assert (own["confidence"], own["claimed"], own["downgraded"]) == ("weak", "weak", ["quote_too_thin"])
        # a malformed payload is refused, never stored
        for bad in ({**_review(cv_id), "confidence": "sure"}, {**_review(cv_id), "lines": []},
                    {**_review(cv_id), "cv_text": CV}, _review(cv_id, lines=[{**L1, "profile_quote": ""}])):
            assert (await _event(client, app_id, "cv_reviewed", bad)).status_code == 422, bad
        for gated in (app_id, other_app, third_app):
            assert (await _gate(client, gated))["reason"] in ("cv_not_seen", "cv_unsupported_claims")
        assert (await _gate(client, third_app))["reason"] == "cv_unsupported_claims"


@pytest.mark.asyncio
async def test_a_new_cv_version_needs_a_new_review(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        app_id, cv_id = await _setup(client)
        assert (await _stored(client, app_id, _review(cv_id)))["confidence"] == "strong"
        assert (await _gate(client, app_id))["reason"] == "practice_run"
        v2 = CV.replace("- Data engineer", "* Data engineer")
        v2_id = await _save_cv(client, app_id, v2)
        body = await _gate(client, app_id)
        assert body["reason"] == "cv_not_seen" and body["autonomy_shadow"]["cv_review"] == "none"
        # the old sha's review is no review of this one; a review of v2 clears it again
        assert (await _event(client, app_id, "cv_reviewed", _review(cv_id))).status_code == 422
        assert (await _stored(client, app_id, _review(v2_id, v2)))["confidence"] == "strong"
        assert (await _gate(client, app_id))["reason"] == "practice_run"


@pytest.mark.asyncio
async def test_the_setting_off_or_confirm_mode_never_clears(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        app_id, cv_id = await _setup(client, cv_auto=False)
        assert (await _stored(client, app_id, _review(cv_id)))["confidence"] == "strong"
        assert (await _gate(client, app_id))["reason"] == "cv_not_seen"
        # Back to confirm mode with the setting on: the strong review is no yes.
        assert (await client.patch("/api/profile", json={"edits": [
            {"path": "assistant_settings.submit_mode", "value": "confirm"}, {"path": CV_AUTO, "value": True},
        ]})).status_code == 200
        body = await _gate(client, app_id)
        assert (body["decision"], body["reason"]) == ("ask", "submit_mode_confirm")


@pytest.mark.asyncio
async def test_the_receipt_rule_takes_the_strong_review_only_with_the_setting(
    authenticated_async_context, fixture_user_id
):
    from src.api import dependencies as api_deps
    from src.services.applications import kit

    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        app_id, cv_id = await _setup(client, cv_auto=False)
        await _stored(client, app_id, _review(cv_id))
        db = await api_deps.get_db()
        assert await kit._cv_not_approved(db, fixture_user_id, app_id, None) is True
        assert (await client.patch("/api/profile", json={"edits": [{"path": CV_AUTO, "value": True}]})).status_code == 200
        assert await kit._cv_not_approved(db, fixture_user_id, app_id, None) is False


@pytest.mark.asyncio
async def test_after_three_weak_reviews_a_fourth_is_weak(authenticated_async_context, fixture_user_id):
    # owner 2026-11-09: two rewrites, each re-reviewed; the review of a 4th version is forced weak
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        app_id, cv_id = await _setup(client)
        assert (await _stored(client, app_id, _review(cv_id, confidence="weak")))["downgraded"] == []
        v2 = CV.replace("- Data engineer", "* Data engineer")
        v2_id = await _save_cv(client, app_id, v2)
        assert (await _stored(client, app_id, _review(v2_id, v2, confidence="weak")))["confidence"] == "weak"
        v3 = CV.replace("- Built", "* Built")
        v3_id = await _save_cv(client, app_id, v3)
        assert (await _stored(client, app_id, _review(v3_id, v3, confidence="weak")))["downgraded"] == []
        v4 = CV.replace("- Built", "** Built")
        v4_id = await _save_cv(client, app_id, v4)
        fourth = await _stored(client, app_id, _review(v4_id, v4))
        assert (fourth["confidence"], fourth["downgraded"]) == ("weak", ["retry_limit"])
        assert (await _gate(client, app_id))["reason"] == "cv_not_seen"


@pytest.mark.asyncio
async def test_the_second_rewrite_is_judged_on_its_merits(authenticated_async_context, fixture_user_id):
    # owner 2026-11-09: two rewrites. v1 weak, v2 weak, a strong v3 is accepted (the v4 case: test above).
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        app_id, cv_id = await _setup(client)
        assert (await _stored(client, app_id, _review(cv_id, confidence="weak")))["downgraded"] == []
        v2 = CV.replace("- Data engineer", "* Data engineer")
        v2_id = await _save_cv(client, app_id, v2)
        assert (await _stored(client, app_id, _review(v2_id, v2, confidence="weak")))["downgraded"] == []
        v3 = CV.replace("- Built", "* Built")
        v3_id = await _save_cv(client, app_id, v3)
        third = await _stored(client, app_id, _review(v3_id, v3))
        assert (third["confidence"], third["downgraded"]) == ("strong", [])


@pytest.mark.asyncio
async def test_an_assistant_cannot_turn_the_setting_on(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        token = (await client.post("/api/tokens", json={"name": "claude-code"})).json()["token"]
        async with _agent(token) as agent:
            asked = await agent.patch("/api/profile", json={"edits": [{"path": CV_AUTO, "value": True}]})
            assert asked.status_code == 200, asked.text
        view = (await client.get("/api/assistant-settings")).json()
        assert view["cv_auto_approve"] == {"value": False, "effective": False}
        assert [w["path"] for w in view["waiting"]] == [CV_AUTO]
        assert (await client.patch("/api/profile", json={"edits": [{"path": CV_AUTO, "value": "yes"}]})).status_code == 422
        assert (await client.patch("/api/profile", json={"edits": [{"path": CV_AUTO, "value": True}]})).status_code == 200
        assert (await client.get("/api/assistant-settings")).json()["cv_auto_approve"]["effective"] is True
        async with _agent(token) as agent:  # off is safer: it applies at once
            assert (await agent.patch("/api/profile", json={"edits": [{"path": CV_AUTO, "value": False}]})).status_code == 200
        assert (await client.get("/api/assistant-settings")).json()["cv_auto_approve"]["effective"] is False


@pytest.mark.asyncio
async def test_thin_quotes_unchecked_first_words_and_stretches_are_weak(authenticated_async_context, fixture_user_id):
    """The independent check's attacks: each was stored strong before the quote had to carry the line."""
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)

        async def verdict(line: str, quote: str) -> tuple[str, list[str]]:
            cv = CV + line + "\n"
            app_id, cv_id = await _setup(client, cv=cv)
            got = await _stored(client, app_id, _review(cv_id, cv, lines=[
                L1, L2, {"cv_line": line, "profile_quote": quote, "field_path": "cv_data.raw_text"}]))
            return got["confidence"], got["downgraded"]

        # thin quotes: one word of the profile cannot vouch for a line
        for line, quote in (("- Led the migration and cut costs in half", "the"),
                            ("- Mentored engineers across the platform team", "engineers"),
                            ("- Built the reporting layer from scratch", "built"),
                            ("A passionate leader who loves mentoring teams", "Data engineer")):
            got = await verdict(line, quote)
            assert got[0] == "weak" and "quote_too_thin" in got[1], (line, got)
        # the first word is a claim too
        for line in ("Google Cloud certified data engineer", "Data engineer at Microsoft and Northwind",
                     "Data engineer at Northwind with Kubernetes", "Snowflake data engineer at Northwind"):
            got = await verdict(line, "data engineer at Northwind since 2019.")
            assert got[0] == "weak" and {"claim_not_in_quote", "cv_checks_found"} & set(got[1]), (line, got)
        # a stretch: "worked on" is not "led" (a role verb is a fact, never a free opener)
        assert await verdict("- Led the warehouse migration and cut storage costs in half.",
                             "Worked on the warehouse migration and cut storage costs in half.") == (
            "weak", ["claim_not_in_quote"])
        # a heading that carries a claim is a content line: with no quote it is uncovered
        # owner 2026-11-07: a capitalised name inside a line the profile lacks ("Cloud Certified", "Administrator")
        # is also a save-time check finding now.
        for heading, want in (("AWS SOLUTIONS ARCHITECT", ["line_uncovered"]),
                              ("# Google Cloud Certified", ["line_uncovered", "cv_checks_found"]),
                              ("Kubernetes Administrator:", ["line_uncovered", "cv_checks_found"]),
                              ("CTO: ada@example.com", ["line_uncovered"])):
            cv = CV + heading + "\n"
            app_id, cv_id = await _setup(client, cv=cv)
            assert (await _stored(client, app_id, _review(cv_id, cv)))["downgraded"] == want, heading


@pytest.mark.asyncio
async def test_the_cv_text_decides_case_and_a_word_the_profile_never_says_is_weak(
    authenticated_async_context, fixture_user_id,
):
    """The second check's attacks: a lowercased copy of the line hid "Google"; "twelve" and "kubernetes" are not
    capitalised claims, yet the profile never says them; a heading of profile words is still a claim."""
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)

        async def verdict(line: str, entry_line: str, quote: str) -> tuple[str, list[str]]:
            cv = CV + line + "\n"
            app_id, cv_id = await _setup(client, cv=cv)
            got = await _stored(client, app_id, _review(cv_id, cv, lines=[
                L1, L2, {"cv_line": entry_line, "profile_quote": quote, "field_path": "cv_data.raw_text"}]))
            return got["confidence"], got["downgraded"]

        line = "- Built ETL pipelines in Python and Snowflake at Google"
        got = await verdict(line, line.lower(), "Built ETL pipelines in Python and Snowflake")
        assert got[0] == "weak" and "claim_not_in_quote" in got[1], got
        for line, quote in (("- Built ETL pipelines in Python and kubernetes", "Built ETL pipelines in Python"),
                            ("- Worked on the warehouse migration with twelve engineers",
                             "Worked on the warehouse migration")):
            got = await verdict(line, line, quote)
            assert got[0] == "weak" and "claim_not_in_quote" in got[1], (line, got)
        cv = CV + "DATA ENGINEER\n"  # every word is in the profile, but it is a title, not a section name
        app_id, cv_id = await _setup(client, cv=cv)
        assert (await _stored(client, app_id, _review(cv_id, cv)))["downgraded"] == ["line_uncovered"]


@pytest.mark.asyncio
async def test_honest_lines_stay_strong(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        cv = ("Ada Lovelace\nEXPERIENCE\n- Built ETL pipelines in Python and Snowflake, cutting warehouse costs 30%.\n"
              "- In Python and Snowflake, built ETL pipelines, cutting warehouse costs 30%.\n"
              "- Built ETL pipelines in Python and Snowflake.\nCERTIFICATIONS\n"
              "Google Cloud Professional Data Engineer, 2022\n")
        app_id, cv_id = await _setup(client, cv=cv)
        full = L2["profile_quote"]  # one quote may vouch for two lines; the third quotes a shorter span of it
        lines = [{**L2, "cv_line": ln, "profile_quote": full} for ln in cv.splitlines()[2:4]] + [
            {**L2, "cv_line": cv.splitlines()[4], "profile_quote": "Built ETL pipelines in Python and Snowflake"}] + [
            {"cv_line": "Google Cloud Professional Data Engineer, 2022",
             "profile_quote": "Google Cloud Professional Data Engineer, 2022.", "field_path": "cv_data.raw_text"}]
        got = await _stored(client, app_id, _review(cv_id, cv, lines=lines))
        assert (got["confidence"], got["downgraded"], got["covered"], got["content_lines"]) == ("strong", [], 4, 4)
