"""Facts from the user's OWN records on a brought job (posting_history.py), the CV
truth ask in check_submit, and the stock-phrase writing check (2026-10-17).

Borrowed: career-ops fingerprint-core.mjs (SimHash) and company-history.mjs,
freehire ghost/classify.go + jobreality (posting signs), trackly-cli
application-text.js (stock phrases). Helpers are copied, never imported from
another test module.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from httpx import AsyncClient

from src.services.applications import cv_checks, posting_history
from src.services.profile import assistant_settings as rules
from src.services.profile.models import AssistantSettings

_LONG = (
    "We are hiring a data engineer to build and run the pipelines behind our analytics platform. "
    "You will write Python and SQL every day, own dbt models, keep Snowflake tidy and fast, and work "
    "with product teams on clean data contracts. You have shipped production pipelines before. "
) * 3


def _ago(days: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


async def _bring(client: AsyncClient, **over: Any) -> dict[str, Any]:
    body = {"title": "Data Engineer", "company": "Acme", "location": "London",
            "apply_url": "https://acme.example/jobs/10101", "description": _LONG, **over}
    resp = await client.post("/api/jobs/bring", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _event(client: AsyncClient, app_id: int, event_type: str, **extra: Any) -> None:
    resp = await client.post(f"/api/applications/{app_id}/events", json={"event_type": event_type, **extra})
    assert resp.status_code == 201, resp.text


def _row(checks: list[dict[str, Any]], code: str) -> dict[str, Any]:
    return next(c for c in checks if c["code"] == code)


# ── pure: the fingerprint is career-ops' bit for bit ─────────────────────────

def test_fingerprint_matches_career_ops():
    # The same text through career-ops fingerprint-core.mjs fingerprintText() (node,
    # 2026-10-17) printed 783338025e0a0e43.
    text = ("We are hiring a senior data engineer to build pipelines in Python & SQL for our "
            "<b>analytics</b> team. Visit https://x.y/z ÉTÉ café ") * 10
    assert format(posting_history.fingerprint(text), "016x") == "783338025e0a0e43"


def test_short_text_never_matches():
    assert posting_history.fingerprint("Python, SQL") is None
    assert posting_history.similarity(None, posting_history.fingerprint(_LONG)) == 0.0
    a = posting_history.fingerprint(_LONG)
    assert posting_history.similarity(a, a) == 1.0


def test_posting_signs_count_and_converge():
    base = [{"code": "ad_age_days", "value": 120}, {"code": "apply_host", "value": "job_board"}]
    two = posting_history.posting_signs(base, "Join our talent pool.", None, None, None)
    assert two["value"] == 3 and two["of"] == 5 and two["ok"] is False
    assert [s["code"] for s in two["signs"]] == ["old_ad", "talent_pool_wording", "not_on_employer_site"]
    assert "never skip" in two["reason"] and "ghost" not in two["reason"].lower()
    one = posting_history.posting_signs([{"code": "ad_age_days", "value": 120}], "", None, None, None)
    assert one["value"] == 1 and one["ok"] is None
    none = posting_history.posting_signs([], "", {"label": "responded-before"}, None, None)
    assert none["value"] == 0 and none["signs"] == []
    silent = posting_history.posting_signs([], "", {"label": "silent-on-you"}, None, None)
    assert [s["code"] for s in silent["signs"]] == ["company_silent"]


# ── through the bring route ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_first_bring_has_no_history_and_no_similar_ad(authenticated_async_context):
    async with authenticated_async_context() as client:
        body = await _bring(client)
        hist = _row(body["checks"], "company_history")
        assert hist["value"] == "no-history" and hist["applications"] == 0
        assert _row(body["checks"], "similar_ad")["value"] is False
        assert _row(body["checks"], "posting_signs")["value"] == 0


@pytest.mark.asyncio
async def test_company_history_answered_and_silent(authenticated_async_context):
    async with authenticated_async_context() as client:
        first = int((await _bring(client, apply_url="https://acme.example/jobs/1"))["application_id"])
        await _event(client, first, "applied", occurred_at=_ago(60))
        await _event(client, first, "rejected", occurred_at=_ago(50))
        second = int((await _bring(client, title="Analytics Engineer", apply_url="https://acme.example/jobs/2",
                                   description="Model the warehouse for the analytics team."))["application_id"])
        await _event(client, second, "applied", occurred_at=_ago(40))
        third = await _bring(client, title="Platform Engineer", apply_url="https://acme.example/jobs/3",
                             description="Run the platform.")
        hist = _row(third["checks"], "company_history")
        assert hist["value"] == "mixed" and hist["applications"] == 2
        assert "rejected" in hist["reason"] and "no answer after 28+ days" in hist["reason"]
        signs = _row(third["checks"], "posting_signs")
        assert "company_silent" in [s["code"] for s in signs["signs"]]


@pytest.mark.asyncio
async def test_similar_ad_under_another_company(authenticated_async_context):
    async with authenticated_async_context() as client:
        first = await _bring(client)
        other = await _bring(client, title="Data Engineer (client)", company="Talent Agency Ltd",
                             apply_url="https://agency.example/jobs/555")
        row = _row(other["checks"], "similar_ad")
        assert row["value"] is True and row["same_company"] is False
        assert row["matches"][0]["application_id"] == int(first["application_id"])
        assert row["matches"][0]["similarity"] >= 0.92


@pytest.mark.asyncio
async def test_kit_and_get_job_carry_the_history_rows(authenticated_async_context, fixture_user_id):
    pytest.importorskip("mcp")
    from src.api import mcp_server

    tools = mcp_server.build_server()._tool_manager
    async with authenticated_async_context() as client:
        body = await _bring(client)
    ctx = mcp_server._current_user.set(mcp_server.CurrentUser(
        id=fixture_user_id, email="e2e@example.com", email_verified=True, auth_via="token", actor_name="cc"))
    try:
        kit = await tools.get_tool("get_application_kit").fn(application_id=int(body["application_id"]))
        job = await tools.get_tool("get_job").fn(job_id=int(body["job"]["id"]))
    finally:
        mcp_server._current_user.reset(ctx)
    want = {"company_history", "similar_ad", "posting_signs"}
    assert want <= {c["code"] for c in kit["job"]["checks"]}
    assert want <= {c["code"] for c in job["checks"]}


# ── check_submit: the CV truth ask ──────────────────────────────────────────

def _facts(**kit: Any) -> rules.SubmitFacts:
    kit.setdefault("form_filled", True)
    kit.setdefault("form_swept", True)
    kit.setdefault("fit_score", 80)
    kit.setdefault("form_host", "careers.northwind.example")
    return rules.SubmitFacts(status="considering", has_receipt=False, submit_override=None, cv_seen=True, **kit)


def test_unsupported_cv_claims_ask_until_the_user_says_yes():
    cfg = rules.effective(AssistantSettings(apply_min_score=50, above_line="auto", below_line="auto",
                                            submit_mode="auto_when_sure"))
    counts = rules.SubmitCounts(submitted_today=0, applied_since_auto_on=1)
    now = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
    form = "https://careers.northwind.example/apply/7"
    asked = rules.may_submit(cfg, _facts(cv_unsupported=True), form, counts, now=now)
    assert (asked.decision, asked.reason) == ("ask", "cv_unsupported_claims")
    yes = rules.may_submit(cfg, _facts(cv_unsupported=True, approved=True), form, counts, now=now)
    assert (yes.decision, yes.reason) == ("submit", "user_approved")
    clean = rules.may_submit(cfg, _facts(), form, counts, now=now)
    assert clean.reason == "auto_when_sure"


# ── writing: stock phrases ──────────────────────────────────────────────────

def test_stock_phrases_warn_on_letters_and_messages_only():
    text = "I am thrilled to apply. Just checking in. It is not just code but also people."
    hit = cv_checks.stock_phrases(text)
    assert hit is not None and hit["severity"] == "warn"
    assert '"thrilled to apply"' in hit["detail"] and '"not just ... but also"' in hit["detail"]
    assert cv_checks.stock_phrases("I built the billing pipeline in Python.") is None
    codes = [c["code"] for c in cv_checks.check_text("outreach", text, None)]
    assert "stock_phrases" in codes
    assert "stock_phrases" not in [c["code"] for c in cv_checks.check_text("cv", text, None)]
