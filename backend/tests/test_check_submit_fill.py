"""Owner 2026-10-24 - check_submit says plainly what to do next.

A real scheduled run read ``stop`` / ``form_not_recorded`` as "end the run" and filled nothing. Now a
stop the assistant fixes itself answers ``fill`` with ``next_action``; only a run-wide stop (paused, a
daily or weekly limit, outside hours) sets ``stop_run``. ``fill`` is never ``submit``, so an old client
that knows only submit/ask/stop/pace_wait never presses submit on it. ask_user refuses one ask that
names several open jobs. Helpers are defined here, never imported (per-test schema isolation).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient

from src.services.profile import assistant_settings as rules
from src.services.profile.models import AssistantSettings, CVData, UserPreferences, UserProfile

FORM = "https://jobs.ashby.example/acme/apply"
RECIPES = Path(__file__).resolve().parents[1] / "src" / "recipes"


def _ad(company: str, n: int) -> dict[str, Any]:
    return {
        "title": "Data Engineer", "company": company, "location": "London", "country": "GB", "remote": False,
        "apply_url": f"https://jobs.ashby.example/{company.lower()}/{n}", "description": "Pipelines in Python.",
    }


def _seed_profile(user_id: str) -> None:
    from src.services.profile.storage import save_assistant_settings, save_profile

    save_profile(
        UserProfile(
            cv_data=CVData(raw_text="Python engineer.", name="Ada Lovelace", location="London, UK"),
            preferences=UserPreferences(target_job_titles=["Data Engineer"]),
        ),
        user_id, source_action="cv_upload",
    )
    save_assistant_settings(
        user_id, AssistantSettings(apply_min_score=50, above_line="auto", below_line="auto"), "test_seed"
    )


async def _bring(client: AsyncClient, company: str = "Acme", n: int = 1) -> int:
    resp = await client.post("/api/jobs/bring", json=_ad(company, n))
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _verdict(client: AsyncClient, app_id: int) -> dict[str, Any]:
    resp = await client.get(f"/api/applications/{app_id}/submit-check", params={"form_url": FORM})
    assert resp.status_code == 200, resp.text
    return resp.json()


# ── Pure: the mapping ────────────────────────────────────────────────────────


@pytest.mark.parametrize(("reason", "action"), sorted(rules.FILL_NEXT_ACTIONS.items()))
def test_each_own_work_stop_answers_fill_with_its_next_step(reason, action):
    shown = rules.present_verdict(rules.SubmitDecision("stop", reason, "x"), has_cv=True)
    assert shown == {"decision": "fill", "reason": reason, "detail": "x", "next_action": action, "stop_run": False}
    assert action in rules.NEXT_ACTIONS


def test_no_cv_yet_means_write_the_cv_first():
    shown = rules.present_verdict(rules.SubmitDecision("stop", "form_not_recorded", "x"), has_cv=False)
    assert (shown["decision"], shown["next_action"]) == ("fill", "write_cv")


@pytest.mark.parametrize("reason", sorted(rules.RUN_STOP_REASONS))
def test_only_run_wide_stops_end_the_run(reason):
    shown = rules.present_verdict(rules.SubmitDecision("stop", reason, "x"), has_cv=True)
    assert (shown["decision"], shown["next_action"], shown["stop_run"]) == ("stop", None, True)


@pytest.mark.parametrize("reason", ["company_avoided", "already_applied", "submit_unconfirmed", "pace_wait"])
def test_a_job_stop_skips_this_job_only(reason):
    shown = rules.present_verdict(rules.SubmitDecision("stop", reason, "x"), has_cv=True)
    assert (shown["decision"], shown["next_action"], shown["stop_run"]) == ("stop", None, False)


@pytest.mark.parametrize("decision", ["submit", "ask"])
def test_submit_and_ask_pass_through_unchanged(decision):
    shown = rules.present_verdict(rules.SubmitDecision(decision, "form_not_recorded", "x"), has_cv=False)
    assert (shown["decision"], shown["next_action"], shown["stop_run"]) == (decision, None, False)


def test_fill_is_never_submit_for_any_reason():
    for reason in (*rules.REASONS, "type_first_pending", "form_count_mismatch", "unreadable_fields"):
        for has_cv in (True, False):
            assert rules.present_verdict(rules.SubmitDecision("stop", reason, ""), has_cv=has_cv)["decision"] != "submit"


def test_the_recipes_say_fill_never_ends_the_run():
    for name in ("apply.md", "batch.md"):
        text = (RECIPES / name).read_text(encoding="utf-8")
        assert "`fill` = do the next step now; never a reason to end the run" in text, name
        assert "stop_run" in text, name
    rules_md = (RECIPES / "rules.md").read_text(encoding="utf-8")
    assert "`fill` - do the next step now; never a reason to end the run" in rules_md
    for action in rules.NEXT_ACTIONS:
        assert action in rules_md, action


# ── Through the real door ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_route_says_write_the_cv_then_fill_the_form(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        first = await _verdict(client, app_id)
        assert (first["decision"], first["reason"], first["next_action"], first["stop_run"]) == (
            "fill", "form_not_recorded", "write_cv", False
        )
        cv = await client.post(f"/api/applications/{app_id}/artifacts", json={"kind": "cv", "text": "Ada, Python."})
        assert cv.status_code in (200, 201), cv.text
        second = await _verdict(client, app_id)
        assert (second["decision"], second["next_action"]) == ("fill", "open_and_fill_form")


@pytest.mark.asyncio
async def test_route_pause_ends_the_run(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        resp = await client.patch(
            "/api/profile", json={"edits": [{"path": "assistant_settings.paused_until", "value": "until_resumed"}]}
        )
        assert resp.status_code == 200, resp.text
        verdict = await _verdict(client, app_id)
        assert (verdict["decision"], verdict["reason"], verdict["stop_run"], verdict["next_action"]) == (
            "stop", "paused", True, None
        )


@pytest.mark.asyncio
async def test_mcp_check_submit_answers_fill_like_the_route(authenticated_async_context, fixture_user_id):
    from src.api import mcp_server as m
    from src.api.auth_deps import CurrentUser

    tools = m.build_server()._tool_manager
    token = m._current_user.set(
        CurrentUser(id=fixture_user_id, email="e2e@example.com", email_verified=True, auth_via="token", actor_name="cc")
    )
    try:
        async with authenticated_async_context() as client:
            _seed_profile(fixture_user_id)
            app_id = await _bring(client)
            route = await _verdict(client, app_id)
            tool = await tools.get_tool("check_submit").fn(application_id=app_id, form_url=FORM)
            assert (tool["decision"], tool["next_action"], tool["stop_run"]) == ("fill", "write_cv", False)
            assert {k: tool[k] for k in ("decision", "reason", "next_action", "stop_run")} == {
                k: route[k] for k in ("decision", "reason", "next_action", "stop_run")
            }
    finally:
        m._current_user.reset(token)


# ── ask_user: one ask per job ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_ask_naming_another_open_job_is_refused(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        acme = await _bring(client, "Acme", 1)
        await _bring(client, "Globex", 2)
        both = await client.post("/api/asks", json={
            "question": "Expected salary for Acme and Globex?", "application_id": acme,
        })
        assert both.status_code == 422 and "one ask per job" in both.text and "Globex" in both.text
        ctx = await client.post("/api/asks", json={
            "question": "Expected salary?", "context": "Same question on the globex form", "application_id": acme,
        })
        assert ctx.status_code == 422, ctx.text
        own = await client.post("/api/asks", json={"question": "Expected salary for Acme?", "application_id": acme})
        assert own.status_code in (200, 201), own.text
        general = await client.post("/api/asks", json={"question": "Prefer Acme or Globex?"})
        assert general.status_code in (200, 201), general.text  # a general ask is not about one job
        part = await client.post("/api/asks", json={"question": "Any Globexian contacts?", "application_id": acme})
        assert part.status_code in (200, 201), part.text  # whole words only
