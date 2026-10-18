"""Bulk-apply build list 2026-10-18, items D1, D2, D3 and R2 - the submit gate's new safety rules.

D1 ``submit_attempted`` / web-only ``submit_resolved`` and the ``submit_unconfirmed`` STOP (rule 4d2).
D2 ``company_avoided`` STOP from ``preferences.avoid_companies`` (rule 4a2, exact normalized match).
D3 ``duplicate_role`` ASK (same company + same title inside ``DUPLICATE_ROLE_WINDOW_DAYS``).
R2 ``new_site_first_time`` ASK in auto mode (a yes clears it).

Pure tests need no database; the rest go through the real doors on the real Postgres.
Helpers are copied, never imported from another test module (schema isolation).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.core import settings
from src.services.profile import assistant_settings as rules
from src.services.profile.models import AssistantSettings, UserPreferences

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)
FORM = "https://careers.northwind.example/apply/7"
HOST = "careers.northwind.example"
AVOID = "preferences.avoid_companies"
# With no score line / fit saved, the first ordinary ask after the stops and duplicate asks.
BASE_ASK = "score_line_unknown"


# ═══════════════════════════════════════════════════════════════════════════
# The pure gate
# ═══════════════════════════════════════════════════════════════════════════


def _eff(**kw: Any) -> rules.EffectiveSettings:
    kw.setdefault("apply_min_score", 50)
    kw.setdefault("above_line", "auto")
    kw.setdefault("below_line", "auto")
    kw.setdefault("submit_mode", "auto_when_sure")
    return rules.effective(AssistantSettings(**kw))


def _counts(applied_since_auto_on: int = 1) -> rules.SubmitCounts:
    return rules.SubmitCounts(submitted_today=0, applied_since_auto_on=applied_since_auto_on)


def _facts(**kw: Any) -> rules.SubmitFacts:
    base: dict[str, Any] = {
        "status": "considering", "has_receipt": False, "submit_override": None, "cv_seen": True,
        "form_filled": True, "form_swept": True, "fit_score": 80, "form_host": HOST,
    }
    base.update(kw)
    return rules.SubmitFacts(**base)


def _verdict(facts: rules.SubmitFacts, cfg: rules.EffectiveSettings | None = None) -> rules.SubmitDecision:
    return rules.may_submit(cfg or _eff(), facts, FORM, _counts(), now=NOW)


def test_the_four_new_reasons_are_closed_codes():
    for code in ("submit_unconfirmed", "company_avoided", "duplicate_role", "new_site_first_time"):
        assert code in rules.REASONS


def test_a_clean_application_still_submits():
    assert _verdict(_facts()).reason == "auto_when_sure"


def test_submit_unconfirmed_stops_and_a_yes_never_clears_it():
    stop = _verdict(_facts(submit_unconfirmed=True))
    assert (stop.decision, stop.reason) == ("stop", "submit_unconfirmed")
    assert "Do not submit again" in stop.detail
    yes = _verdict(_facts(submit_unconfirmed=True, approved=True))
    assert (yes.decision, yes.reason) == ("stop", "submit_unconfirmed")
    confirm = _verdict(_facts(submit_unconfirmed=True, approved=True), _eff(submit_mode="confirm"))
    assert (confirm.decision, confirm.reason) == ("stop", "submit_unconfirmed")


def test_submit_unconfirmed_sits_after_the_form_stops_and_before_the_asks():
    facts = _facts(submit_unconfirmed=True)
    assert _verdict(_facts(submit_unconfirmed=True, form_swept=False)).reason == "form_not_swept"
    assert _verdict(_facts(submit_unconfirmed=True, form_check_missing=True)).reason == "form_check_missing"
    # a stop is never softened to the email ask
    assert _verdict(_facts(submit_unconfirmed=True, email_verified=False)).reason == "submit_unconfirmed"
    # paused and already-applied still win
    assert _verdict(facts, _eff(paused_until="until_resumed")).reason == "paused"
    assert _verdict(_facts(submit_unconfirmed=True, has_receipt=True)).reason == "already_applied"


def test_company_avoided_stops_before_the_form_is_even_filled_and_a_yes_never_clears_it():
    stop = _verdict(_facts(company_avoided=True, form_filled=False))
    assert (stop.decision, stop.reason) == ("stop", "company_avoided")
    assert "never-apply list" in stop.detail
    yes = _verdict(_facts(company_avoided=True, approved=True))
    assert (yes.decision, yes.reason) == ("stop", "company_avoided")
    assert _verdict(_facts(company_avoided=True), _eff(daily_cap=1)).reason == "company_avoided"


def test_duplicate_role_asks_in_auto_and_in_confirm_mode_and_a_yes_clears_it():
    auto = _verdict(_facts(duplicate_role=True))
    assert (auto.decision, auto.reason) == ("ask", "duplicate_role")
    confirm = _verdict(_facts(duplicate_role=True), _eff(submit_mode="confirm"))
    assert (confirm.decision, confirm.reason) == ("ask", "duplicate_role")
    yes = _verdict(_facts(duplicate_role=True, approved=True))
    assert (yes.decision, yes.reason) == ("submit", "user_approved")


def test_duplicate_job_still_beats_duplicate_role():
    assert _verdict(_facts(duplicate_job=True, duplicate_role=True)).reason == "duplicate_job"
    ask = _verdict(_facts(duplicate_job=True, duplicate_role=True), _eff(submit_mode="confirm"))
    assert ask.reason == "duplicate_job"


def test_new_site_first_time_asks_in_auto_after_the_practice_run_and_a_yes_clears_it():
    ask = _verdict(_facts(new_site=True))
    assert (ask.decision, ask.reason) == ("ask", "new_site_first_time")
    yes = _verdict(_facts(new_site=True, approved=True))
    assert (yes.decision, yes.reason) == ("submit", "user_approved")
    # the practice run still comes first, then this site ask
    first = rules.may_submit(_eff(), _facts(new_site=True), FORM, _counts(0), now=NOW)
    assert first.reason == "practice_run"


def test_new_site_first_time_does_not_change_confirm_mode_or_a_known_site():
    confirm = _verdict(_facts(new_site=True), _eff(submit_mode="confirm"))
    assert confirm.reason == "submit_mode_confirm"
    assert _verdict(_facts(new_site=False)).reason == "auto_when_sure"


# ═══════════════════════════════════════════════════════════════════════════
# Title normalisation (D3)
# ═══════════════════════════════════════════════════════════════════════════


def test_normalized_title_is_casefold_punctuation_free_and_exact():
    from src.services.applications.kit import normalized_title

    assert normalized_title("Data Engineer (Remote)") == normalized_title("data engineer - REMOTE")
    assert normalized_title("  Senior   Data-Engineer ") == "senior data engineer"
    assert normalized_title("Data Engineer") != normalized_title("Senior Data Engineer")
    assert normalized_title("") == ""
    assert normalized_title("!!!") == ""


def test_the_role_window_default_is_180_days():
    assert settings.DUPLICATE_ROLE_WINDOW_DAYS == 180


# ═══════════════════════════════════════════════════════════════════════════
# Real-database helpers
# ═══════════════════════════════════════════════════════════════════════════


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


def _ad(n: int, company: str = "Northwind", title: str | None = None) -> dict[str, Any]:
    return {
        "title": title or f"Data Engineer {n}",
        "company": company,
        "location": "Remote",
        "apply_url": f"https://northwind.example/careers/{n}",
        "description": "Build the pipelines. Python, dbt, Snowflake. Fully remote.",
    }


async def _bring(client: AsyncClient, ad: dict[str, Any]) -> int:
    resp = await client.post("/api/jobs/bring", json=ad)
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _event(client: AsyncClient, app_id: int, event_type: str, **body: Any):
    return await client.post(f"/api/applications/{app_id}/events", json={"event_type": event_type, **body})


async def _fill(client: AsyncClient, app_id: int, form: str = FORM) -> None:
    checked = await _event(
        client, app_id, "form_checked", payload={"questions_count": 1, "answered": 1, "missing": 0}
    )
    assert checked.status_code == 201, checked.text
    assert (await _event(client, app_id, "form_filled", payload={"form_url": form, "fields_count": 1})).status_code == 201
    assert (await _event(client, app_id, "form_swept", payload={"fields_checked": 1, "fixed": 0})).status_code == 201


async def _applied_at(client: AsyncClient, app_id: int, when: datetime) -> None:
    resp = await _event(client, app_id, "applied", occurred_at=when.isoformat())
    assert resp.status_code == 201, resp.text


async def _check(client: AsyncClient, app_id: int, form: str = FORM) -> dict[str, Any]:
    resp = await client.get(f"/api/applications/{app_id}/submit-check", params={"form_url": form})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _mint_token(client: AsyncClient) -> str:
    resp = await client.post("/api/tokens", json={"name": "claude-code"})
    assert resp.status_code == 201, resp.text
    return resp.json()["token"]


def _bearer(token: str) -> AsyncClient:
    from src.api.main import app

    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    )


async def _db():
    from src.api import dependencies as api_deps

    return await api_deps.get_db()


# ═══════════════════════════════════════════════════════════════════════════
# D1: submit_attempted / submit_resolved / submit_unconfirmed
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_attempt_without_an_outcome_stops_the_gate_and_a_yes_does_not_clear_it(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client, _ad(1))
        await _fill(client, app_id)
        assert (await _check(client, app_id))["reason"] == BASE_ASK
        attempt = await _event(client, app_id, "submit_attempted")
        assert attempt.status_code == 201, attempt.text
        body = await _check(client, app_id)
        assert (body["decision"], body["reason"]) == ("stop", "submit_unconfirmed")
        # a yes for this exact CV (no CV saved: artifact null) never clears it
        yes = await _event(
            client, app_id, "submit_approved", payload={"artifact_id": None, "sha256": "", "where": "web"}
        )
        assert yes.status_code in (201, 409), yes.text
        assert (await _check(client, app_id))["reason"] == "submit_unconfirmed"


@pytest.mark.asyncio
async def test_the_stop_is_cleared_by_applied_or_a_web_resolve_never_by_blocked(authenticated_async_context, fixture_user_id):
    from src.services.applications import kit

    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        a, b, c = await _bring(client, _ad(1)), await _bring(client, _ad(2)), await _bring(client, _ad(3))
        db = await _db()
        for app_id in (a, b, c):
            assert (await _event(client, app_id, "submit_attempted")).status_code == 201
            assert await kit.submit_unconfirmed(db, fixture_user_id, app_id) is True
        assert (await _event(client, a, "applied")).status_code == 201
        assert await kit.submit_unconfirmed(db, fixture_user_id, a) is False
        blocked = await _event(
            client, b, "blocked",
            payload={"reason": "captcha", "step": "final submit", "page_host": HOST, "detail": "CAPTCHA"},
        )
        assert blocked.status_code == 201, blocked.text
        # a block after the click cannot prove nothing was sent (review 2026-10-18)
        assert await kit.submit_unconfirmed(db, fixture_user_id, b) is True
        resolved = await client.post(f"/api/applications/{c}/submit/resolve")
        assert resolved.status_code == 201, resolved.text
        assert await kit.submit_unconfirmed(db, fixture_user_id, c) is False
        # a NEW attempt after the resolve starts the stop again
        assert (await _event(client, c, "submit_attempted")).status_code == 201
        assert await kit.submit_unconfirmed(db, fixture_user_id, c) is True


@pytest.mark.asyncio
async def test_a_correction_cannot_retract_an_attempt(authenticated_async_context, fixture_user_id):
    from src.services.applications import kit

    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client, _ad(1))
        first = await _event(client, app_id, "submit_attempted")
        await _event(client, app_id, "note", detail="nothing happened", corrects_event_id=first.json()["event_id"])
        assert await kit.submit_unconfirmed(await _db(), fixture_user_id, app_id) is True


@pytest.mark.asyncio
async def test_submit_resolved_is_web_only_and_needs_an_unconfirmed_submit(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client, _ad(1))
        await _fill(client, app_id)
        # nothing to resolve yet
        assert (await client.post(f"/api/applications/{app_id}/submit/resolve")).status_code == 409
        assert (await _event(client, app_id, "submit_attempted")).status_code == 201
        token = await _mint_token(client)
        async with _bearer(token) as agent:
            refused = await _event(agent, app_id, "submit_resolved", payload={"outcome": "not_sent"})
            assert refused.status_code == 403
            # a web session through the generic event door is the user's own click too
            bad = await _event(client, app_id, "submit_resolved", payload={"outcome": "sent"})
            assert bad.status_code == 422
            wrong_keys = await _event(client, app_id, "submit_resolved", payload={"outcome": "not_sent", "x": 1})
            assert wrong_keys.status_code == 422
        # the agent could not clear it
        assert (await _check(client, app_id, FORM))["reason"] == "submit_unconfirmed"
        ok = await _event(client, app_id, "submit_resolved", payload={"outcome": "not_sent"})
        assert ok.status_code == 201, ok.text
        assert (await _check(client, app_id))["reason"] != "submit_unconfirmed"
        # resolving twice: nothing left
        assert (await client.post(f"/api/applications/{app_id}/submit/resolve")).status_code == 409


@pytest.mark.asyncio
async def test_an_assistant_may_record_the_attempt_but_not_with_a_payload(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client, _ad(1))
        token = await _mint_token(client)
        async with _bearer(token) as agent:
            bad = await _event(agent, app_id, "submit_attempted", payload={"why": "x"})
            assert bad.status_code == 422
            assert "Valid example payload" in bad.text
            ok = await _event(agent, app_id, "submit_attempted")
            assert ok.status_code == 201, ok.text


@pytest.mark.asyncio
async def test_morning_check_counts_an_unconfirmed_submit_as_failed(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        pending, done = await _bring(client, _ad(1)), await _bring(client, _ad(2))
        assert (await _event(client, pending, "submit_attempted")).status_code == 201
        assert (await _event(client, done, "submit_attempted")).status_code == 201
        assert (await _event(client, done, "applied")).status_code == 201
        body = (await client.get("/api/morning-check", params={"since": "7d"})).json()
        failed = body["tally"]["failed"]
        assert failed["count"] == 1
        assert [i["application_id"] for i in failed["items"]] == [pending]


# ═══════════════════════════════════════════════════════════════════════════
# D2: company_avoided
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_company_avoided_matches_exactly_after_normalising_and_stops_the_route(
    authenticated_async_context, fixture_user_id
):
    from src.services.applications import kit, spine

    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        mine = await _bring(client, _ad(1, company="Northwind Ltd"))
        other = await _bring(client, _ad(2, company="Southwind"))
        near = await _bring(client, _ad(3, company="Northwind Labs International"))
        assert (await _check(client, mine))["reason"] != "company_avoided"
        patched = await client.patch("/api/profile", json={"edits": [{"path": AVOID, "value": ["  NORTHWIND  ", "Acme"]}]})
        assert patched.status_code == 200, patched.text
        body = await _check(client, mine)
        assert (body["decision"], body["reason"]) == ("stop", "company_avoided")
        assert (await _check(client, other))["reason"] != "company_avoided"
        assert (await _check(client, near))["reason"] != "company_avoided", "no fuzzy match"
        # the kit read model gives the same answer as the route
        db = await _db()
        row = await spine.get_owned_application(db, fixture_user_id, mine)
        assert row is not None and kit.company_avoided(fixture_user_id, row) is True
        # removing the entry (the user's own web click) frees it
        cleared = await client.patch("/api/profile", json={"edits": [{"path": AVOID, "value": ["Acme"]}]})
        assert cleared.status_code == 200, cleared.text
        assert (await _check(client, mine))["reason"] != "company_avoided"


@pytest.mark.asyncio
async def test_an_empty_list_and_an_unknown_company_never_match(authenticated_async_context, fixture_user_id):
    from src.services.applications import kit

    _seed_profile(fixture_user_id)
    assert kit.company_avoided(fixture_user_id, {"job_company": ""}) is False
    assert kit.company_avoided(fixture_user_id, {"job_company": "Northwind"}) is False
    assert kit.company_avoided(fixture_user_id, {}) is False


# ═══════════════════════════════════════════════════════════════════════════
# D3: duplicate_role
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_same_company_and_title_on_another_link_asks_duplicate_role_until_cleared(
    authenticated_async_context, fixture_user_id
):
    from src.services.applications import kit, spine

    now = datetime.now(timezone.utc)
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        first = await _bring(client, _ad(1, title="Data Engineer (Remote)"))
        repost = await _bring(client, _ad(2, title="data engineer - REMOTE"))
        other_title = await _bring(client, _ad(3, title="Senior Data Engineer (Remote)"))
        other_company = await _bring(client, _ad(4, company="Southwind", title="Data Engineer (Remote)"))
        await _applied_at(client, first, now - timedelta(days=20))
        for app_id in (repost, other_title, other_company):
            await _fill(client, app_id)
        body = await _check(client, repost)
        assert (body["decision"], body["reason"]) == ("ask", "duplicate_role")
        assert (await _check(client, other_title))["reason"] == BASE_ASK
        assert (await _check(client, other_company))["reason"] == BASE_ASK
        db = await _db()
        dup = await kit.duplicate_facts(db, fixture_user_id, await spine.get_owned_application(db, fixture_user_id, repost), now)
        assert dup["flag"] == "same_role" and dup["same_role"]["application_id"] == first
        assert dup["same_job"] is None
        # the existing web click clears it
        cleared = await client.post(f"/api/applications/{repost}/duplicate/clear")
        assert cleared.status_code == 201, cleared.text
        assert (await _check(client, repost))["reason"] == BASE_ASK


@pytest.mark.asyncio
async def test_the_role_window_is_respected_and_same_job_keeps_priority(
    authenticated_async_context, fixture_user_id, monkeypatch
):
    from src.services.applications import kit, spine

    now = datetime.now(timezone.utc)
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        old = await _bring(client, _ad(1, title="Data Engineer (Remote)"))
        repost = await _bring(client, _ad(2, title="Data Engineer - Remote"))
        await _applied_at(client, old, now - timedelta(days=200))
        # a truly old record: recorded 200 days ago too (the window reads either date)
        from src.repositories import pgsync

        conn = pgsync.connect(str(settings.DB_PATH))
        conn.execute("UPDATE application_events SET recorded_at = occurred_at WHERE application_id = ?", (old,))
        conn.commit()
        conn.close()
        db = await _db()
        row = await spine.get_owned_application(db, fixture_user_id, repost)
        assert (await kit.duplicate_facts(db, fixture_user_id, row, now))["same_role"] is None
        monkeypatch.setattr(settings, "DUPLICATE_ROLE_WINDOW_DAYS", 365)
        assert (await kit.duplicate_facts(db, fixture_user_id, row, now))["flag"] == "same_role"
        monkeypatch.setattr(settings, "DUPLICATE_ROLE_WINDOW_DAYS", 180)
        assert (await kit.duplicate_facts(db, fixture_user_id, row, now))["flag"] == ""


# ═══════════════════════════════════════════════════════════════════════════
# R2: new_site_first_time
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_new_site_is_true_until_an_application_was_applied_from_that_host(
    authenticated_async_context, fixture_user_id
):
    from src.services.applications import kit

    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        first, second, sim = await _bring(client, _ad(1)), await _bring(client, _ad(2)), await _bring(client, _ad(3))
        db = await _db()
        # no host (nothing filled yet): never "new"
        assert await kit._new_site(db, fixture_user_id, second, None) is False
        assert await kit._new_site(db, fixture_user_id, second, HOST) is True
        await _fill(client, first)
        # filled but not applied: still new
        assert await kit._new_site(db, fixture_user_id, second, HOST) is True
        # a bare `applied` event is not enough (security review 2026-10-18): a receipt is
        await _applied_at(client, first, datetime.now(timezone.utc))
        assert await kit._new_site(db, fixture_user_id, second, HOST) is True
        assert (await client.post(f"/api/applications/{first}/receipt", json={"channel": "company_site"})).status_code == 201
        assert await kit._new_site(db, fixture_user_id, second, HOST) is False
        assert await kit._new_site(db, fixture_user_id, second, "jobs.other.example") is True
        # this application's own fill never counts for itself
        assert await kit._new_site(db, fixture_user_id, first, HOST) is True
        # a simulation does not earn trust for a site
        assert (await _event(client, sim, "simulation")).status_code == 201
        await _fill(client, sim, "https://careers.simulated.example/apply/1")
        assert (await client.post(f"/api/applications/{sim}/receipt", json={"channel": "company_site"})).status_code == 201
        assert await kit._new_site(db, fixture_user_id, second, "careers.simulated.example") is True
        # a simulation mark recorded AFTER the receipt still keeps a site untrusted
        late = await _bring(client, _ad(4))
        await _fill(client, late, "https://careers.late.example/apply/1")
        assert (await client.post(f"/api/applications/{late}/receipt", json={"channel": "company_site"})).status_code == 201
        assert await kit._new_site(db, fixture_user_id, second, "careers.late.example") is False
        assert (await _event(client, late, "simulation")).status_code == 201
        assert await kit._new_site(db, fixture_user_id, second, "careers.late.example") is True


@pytest.mark.asyncio
async def test_gate_facts_carry_the_new_facts(authenticated_async_context, fixture_user_id):
    from src.services.applications import kit, spine

    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client, _ad(1))
        await _fill(client, app_id)
        db = await _db()
        row = await spine.get_owned_application(db, fixture_user_id, app_id)
        facts = await kit.gate_facts(db, fixture_user_id, row, email_verified=True)
        assert (facts.new_site, facts.submit_unconfirmed, facts.company_avoided, facts.duplicate_role) == (
            True, False, False, False,
        )
        assert (await _event(client, app_id, "submit_attempted")).status_code == 201
        facts = await kit.gate_facts(db, fixture_user_id, row, email_verified=True)
        assert facts.submit_unconfirmed is True


# ═══════════════════════════════════════════════════════════════════════════
# Security review 2026-10-18 regressions
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_a_retracted_applied_does_not_clear_an_unconfirmed_submit(authenticated_async_context, fixture_user_id):
    from src.services.applications import kit

    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client, _ad(1))
        assert (await _event(client, app_id, "submit_attempted")).status_code == 201
        applied = await _event(client, app_id, "applied")
        assert applied.status_code == 201
        db = await _db()
        assert await kit.submit_unconfirmed(db, fixture_user_id, app_id) is False
        undo = await _event(client, app_id, "note", detail="not really", corrects_event_id=applied.json()["event_id"])
        assert undo.status_code == 201, undo.text
        assert await kit.submit_unconfirmed(db, fixture_user_id, app_id) is True


@pytest.mark.asyncio
async def test_an_assistant_may_add_to_the_avoid_list_but_never_remove(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        token = await _mint_token(client)
        ok = await client.patch("/api/profile", json={"edits": [{"path": "preferences.avoid_companies", "value": ["Northwind"]}]})
        assert ok.status_code == 200, ok.text
    async with _bearer(token) as bot:
        add = await bot.patch(
            "/api/profile", json={"edits": [{"path": "preferences.avoid_companies", "value": ["Northwind", "Acme"]}]}
        )
        assert add.status_code == 200, add.text
        for value in ([], ["Acme"], None):
            gone = await bot.patch("/api/profile", json={"edits": [{"path": "preferences.avoid_companies", "value": value}]})
            assert gone.status_code == 403, (value, gone.text)
            assert "never remove" in gone.text
    async with authenticated_async_context() as client:
        # the user's own web edit removes at once
        web = await client.patch("/api/profile", json={"edits": [{"path": "preferences.avoid_companies", "value": []}]})
        assert web.status_code == 200, web.text


@pytest.mark.asyncio
async def test_back_dating_the_earlier_applied_still_asks_duplicate_role(authenticated_async_context, fixture_user_id):
    from src.services.applications import kit, spine

    now = datetime.now(timezone.utc)
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        first = await _bring(client, _ad(1, title="Data Engineer (Remote)"))
        repost = await _bring(client, _ad(2, title="Data Engineer - Remote"))
        applied = await _event(client, first, "applied")
        assert applied.status_code == 201
        moved = await _event(
            client, first, "applied", occurred_at="2024-01-01T00:00:00+00:00",
            corrects_event_id=applied.json()["event_id"],
        )
        assert moved.status_code == 201, moved.text
        db = await _db()
        row = await spine.get_owned_application(db, fixture_user_id, repost)
        assert (await kit.duplicate_facts(db, fixture_user_id, row, now))["flag"] == "same_role"


@pytest.mark.asyncio
async def test_the_other_preference_doors_also_refuse_an_assistant_removing_from_the_avoid_list(
    authenticated_async_context, fixture_user_id
):
    """Bug review 2026-10-18: POST /profile/preferences and POST /profile/clear must not
    empty the never-apply list for a token either."""
    import json as _json

    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        token = await _mint_token(client)
        ok = await client.post("/api/profile/preferences", data={"preferences": _json.dumps({"avoid_companies": ["Northwind"]})})
        assert ok.status_code == 200, ok.text
    async with _bearer(token) as bot:
        wipe = await bot.post("/api/profile/preferences", data={"preferences": _json.dumps({"avoid_companies": []})})
        assert wipe.status_code == 403, wipe.text
        other = await bot.post("/api/profile/preferences", data={"preferences": _json.dumps({"avoid_companies": ["Northwind", "Acme"]})})
        assert other.status_code == 200, other.text
        cleared = await bot.post("/api/profile/clear", data={"section": "preferences"})
        assert cleared.status_code == 403, cleared.text
    async with authenticated_async_context() as client:
        prof = (await client.get("/api/profile")).json()
        assert "Northwind" in str(prof)
