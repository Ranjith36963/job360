"""Weak spot #1 - ``check_form``: which questions on the LIVE form already have a saved answer.

The assistant reads the form and lists every question; Job360 only looks up (no NLP, no LLM):
a given kit ``key`` must really exist in THIS application's kit, and with no key the normalized
exact wording is matched against the user's approved answers. Tests go through the real doors
(HTTP + the MCP tool function) and assert VALUES (rule #21). Helpers are copied, never imported.
"""
from __future__ import annotations

import logging
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.core import settings
from src.services.applications import kit as kit_service
from src.services.profile.models import AssistantSettings, CVData, UserPreferences, UserProfile

_AD = {
    "title": "Data Engineer", "company": "Northwind", "location": "London", "country": "GB", "remote": False,
    "apply_url": "https://northwind.example/careers/7",
    "description": "Build the pipelines. Python, dbt, Snowflake.",
}
ASK_NEXT = (
    "Ask the user ONCE, in one message, for every question in `missing`; "
    "save reusable answers with update_profile (user_info.answers); never guess."
)


def _seed_profile(user_id: str) -> None:
    from src.services.profile.storage import save_assistant_settings, save_profile

    save_profile(
        UserProfile(
            cv_data=CVData(raw_text="Python data engineer.", name="Ada Lovelace", location="London, UK"),
            preferences=UserPreferences(target_job_titles=["Data Engineer"]),
        ),
        user_id, source_action="cv_upload",
    )
    save_assistant_settings(
        user_id, AssistantSettings(apply_min_score=50, above_line="auto", below_line="auto"), "test_seed"
    )


def _e(path: str, value: Any) -> dict[str, Any]:
    return {"path": path, "value": value}


async def _patch(client: AsyncClient, *edits_: dict[str, Any]):
    return await client.patch("/api/profile", json={"edits": list(edits_)})


async def _bring(client: AsyncClient, ad: dict[str, Any] = _AD) -> int:
    resp = await client.post("/api/jobs/bring", json=ad)
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _seed_answers(client: AsyncClient) -> None:
    resp = await _patch(
        client,
        _e("user_info.contact", {"email": "ada@example.com", "phone": "+44 7700 900123"}),
        _e("user_info.logistics", {"notice_period": "1 month"}),
        _e("user_info.answers", [
            {"question": "notice period in days", "answer": "30", "approved": True,
             "recorded_at": "2026-10-01T09:00:00+00:00"},
            {"question": "Why Southwind?", "answer": "Their pipelines.", "approved": True,
             "company": "Southwind", "recorded_at": "2026-10-01T09:00:00+00:00"},
            {"question": "Why Northwind?", "answer": "I like pipelines.", "approved": True,
             "company": "Northwind", "recorded_at": "2026-10-01T09:00:00+00:00"},
        ]),
    )
    assert resp.status_code == 200, resp.text


async def _form_check(client: AsyncClient, app_id: int, questions: list[dict[str, Any]], form_url: str = ""):
    return await client.post(
        f"/api/applications/{app_id}/kit/form-check", json={"form_url": form_url, "questions": questions}
    )


def _count(statement: str, params: tuple = ()) -> int:
    from src.repositories import pgsync

    conn = pgsync.connect(str(settings.DB_PATH))
    try:
        return int(conn.execute(statement, params).fetchone()[0])
    finally:
        conn.close()


async def _second_user_session_cookie(email: str) -> str:
    from fastapi.testclient import TestClient

    from src.api.main import app
    from src.repositories import pgsync

    sync_client = TestClient(app)
    r = sync_client.post("/api/auth/register", json={"email": email, "password": "s3cretpassword"})
    assert r.status_code == 201, r.text
    conn = pgsync.connect(str(settings.DB_PATH))
    conn.execute("UPDATE users SET email_verified_at = ? WHERE email = ?", ("2026-01-01T00:00:00Z", email))
    conn.commit()
    conn.close()
    lr = sync_client.post("/api/auth/login", json={"email": email, "password": "s3cretpassword"})
    assert lr.status_code == 200, lr.text
    return str(lr.cookies.get("job360_session"))


# ── Pure ─────────────────────────────────────────────────────────────────────


def _answers() -> dict[str, list[dict[str, Any]]]:
    return {
        "contact": [
            {"key": "contact.email", "value": "a@b.c", "source": "memory", "saved_at": "x"},
            {"key": "contact.phone", "value": "123", "source": "memory", "saved_at": "x", "confirmed": False},
        ],
        "approved_text": [
            {"key": "Notice period (in days)?", "value": "30", "source": "approved_text", "saved_at": None},
        ],
    }


def test_pure_normalization_is_casefold_and_alnum_runs():
    assert kit_service._norm_question("  Notice period (In days)?? ") == "notice period in days"
    assert kit_service._norm_question("E-mail / address") == "e mail address"
    assert kit_service._norm_question("???") == ""


def test_pure_form_check_matches_key_wording_and_reports_gaps():
    out = kit_service.form_check(_answers(), [
        {"question": "Email", "required": True, "key": "contact.email"},
        {"question": "Notice (days)", "required": True, "key": "notice period in days"},
        {"question": "NOTICE PERIOD in days", "required": False},
        {"question": "Phone", "key": "contact.phone"},
        {"question": "Salary", "key": "salary.GB"},
    ])
    assert [(a["question"], a["key"], a["value"], a["matched_by"]) for a in out["answered"]] == [
        ("Email", "contact.email", "a@b.c", "key"),
        ("Notice (days)", "Notice period (in days)?", "30", "key"),
        ("NOTICE PERIOD in days", "Notice period (in days)?", "30", "wording"),
        ("Phone", "contact.phone", "123", "key"),
    ]
    assert out["answered"][3]["confirmed"] is False and "confirmed" not in out["answered"][0]
    assert out["missing"] == [
        {"question": "Salary", "required": True, "why": "key 'salary.GB' is not in this application's kit"}
    ]
    assert out["questions_count"] == 5
    assert out["counts"] == {"answered": 4, "missing": 1, "missing_required": 1}
    assert out["next"] == ASK_NEXT


def test_pure_all_answered_says_fill():
    out = kit_service.form_check(_answers(), [{"question": "Email", "key": "contact.email"}])
    assert out["missing"] == []
    assert out["next"] == "Every question has a saved answer. Fill the form with these values."


def test_kit_instructions_point_at_check_form():
    assert any("call check_form with them" in line for line in kit_service.KIT_INSTRUCTIONS)


# ── Route ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_key_match_returns_value_and_source(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        await _seed_answers(client)
        resp = await _form_check(client, app_id, [
            {"question": "Email address", "required": True, "key": "contact.email"},
            {"question": "Full name", "key": "contact.name"},
        ])
        assert resp.status_code == 200, resp.text
        assert resp.headers["cache-control"] == "no-store"
        body = resp.json()
        assert body["application_id"] == app_id and body["questions_count"] == 2
        assert body["answered"] == [
            {"question": "Email address", "required": True, "key": "contact.email", "value": "ada@example.com",
             "source": "memory", "matched_by": "key"},
            {"question": "Full name", "required": True, "key": "contact.name", "value": "Ada Lovelace",
             "source": "profile", "matched_by": "key"},
        ]
        assert body["missing"] == [] and body["counts"] == {"answered": 2, "missing": 0, "missing_required": 0}
        assert body["next"] == "Every question has a saved answer. Fill the form with these values."


@pytest.mark.asyncio
async def test_wording_match_ignores_case_and_punctuation(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        await _seed_answers(client)
        body = (await _form_check(client, app_id, [{"question": "Notice period (In days)?"}])).json()
        assert body["answered"] == [{
            "question": "Notice period (In days)?", "required": True, "key": "notice period in days",
            "value": "30", "source": "approved_text", "matched_by": "wording",
        }]
        # The agent may also pass the saved approved_text question itself as the key.
        keyed = (await _form_check(client, app_id, [{"question": "Notice?", "key": "Notice Period in Days"}])).json()
        assert keyed["answered"][0]["value"] == "30" and keyed["answered"][0]["matched_by"] == "key"


@pytest.mark.asyncio
async def test_unknown_key_and_unmatched_wording_are_missing_and_counts_split_required(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        await _seed_answers(client)
        body = (await _form_check(client, app_id, [
            {"question": "Visa?", "required": True, "key": "right_to_work.GB.invented"},
            {"question": "Favourite colour", "required": False},
            {"question": "Pronouns", "required": True},
            {"question": "Email", "key": "contact.email"},
        ])).json()
        assert body["missing"] == [
            {"question": "Visa?", "required": True,
             "why": "key 'right_to_work.GB.invented' is not in this application's kit"},
            {"question": "Favourite colour", "required": False, "why": "no saved answer for this question"},
            {"question": "Pronouns", "required": True, "why": "no saved answer for this question"},
        ]
        assert body["counts"] == {"answered": 1, "missing": 3, "missing_required": 2}
        assert body["questions_count"] == 4
        assert body["next"] == ASK_NEXT


@pytest.mark.asyncio
async def test_another_companys_scoped_answer_is_not_matched(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)  # Northwind
        await _seed_answers(client)
        body = (await _form_check(client, app_id, [
            {"question": "Why Southwind?"},
            {"question": "Why Southwind?", "key": "Why Southwind?"},
            {"question": "Why Northwind?"},
        ])).json()
        assert [m["question"] for m in body["missing"]] == ["Why Southwind?", "Why Southwind?"]
        assert body["missing"][1]["why"] == "key 'Why Southwind?' is not in this application's kit"
        assert [(a["question"], a["value"]) for a in body["answered"]] == [("Why Northwind?", "I like pipelines.")]


@pytest.mark.asyncio
async def test_confirmed_false_is_carried_through(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        await _seed_answers(client)
        # An assistant-written, unconfirmed fact is an inference in the kit (feature 9).
        agent_token = (await client.post("/api/tokens", json={"name": "claude-code"})).json()["token"]
        from src.api.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test",
            headers={"Authorization": f"Bearer {agent_token}"},
        ) as agent:
            put = await agent.patch("/api/profile", json={"edits": [
                _e("user_info.logistics", {"notice_period": "2 weeks"})]})
            assert put.status_code == 200, put.text
            body = (await _form_check(agent, app_id, [
                {"question": "Notice", "key": "logistics.notice_period"},
                {"question": "Email", "key": "contact.email"},
            ])).json()
        assert body["answered"][0]["value"] == "2 weeks" and body["answered"][0]["confirmed"] is False
        assert "confirmed" not in body["answered"][1]


@pytest.mark.asyncio
async def test_another_users_application_is_404(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
    cookie = await _second_user_session_cookie("form-intruder@example.com")
    from src.api.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", cookies={"job360_session": cookie}
    ) as intruder:
        assert (await _form_check(intruder, app_id, [{"question": "Email", "key": "contact.email"}])).status_code == 404
        assert (await _form_check(intruder, 999999, [{"question": "Email"}])).status_code == 404


@pytest.mark.asyncio
async def test_question_list_bounds_are_422(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        assert (await _form_check(client, app_id, [])).status_code == 422
        assert (await _form_check(client, app_id, [{"question": f"q{i}"} for i in range(81)])).status_code == 422
        assert (await _form_check(client, app_id, [{"question": f"q{i}"} for i in range(80)])).status_code == 200
        assert (await _form_check(client, app_id, [{"question": "   "}])).status_code == 422
        assert (await _form_check(client, app_id, [{"question": "x" * 501}])).status_code == 422
        assert (await _form_check(client, app_id, [{"question": "x", "who": "me"}])).status_code == 422


@pytest.mark.asyncio
async def test_a_form_check_writes_no_event_and_logs_no_personal_data(
    authenticated_async_context, fixture_user_id, caplog
):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        await _seed_answers(client)
        before = _count("SELECT COUNT(*) FROM application_events WHERE application_id = ?", (app_id,))
        with caplog.at_level(logging.INFO):
            resp = await _form_check(client, app_id, [
                {"question": "Email", "key": "contact.email"}, {"question": "Pronouns"}])
        assert resp.status_code == 200, resp.text
        after = _count("SELECT COUNT(*) FROM application_events WHERE application_id = ?", (app_id,))
    assert before == after
    logs = [r for r in caplog.records if getattr(r, "event", None) == "form_check"]
    assert len(logs) == 1
    rec = logs[0]
    assert (rec.application_id, rec.questions_count, rec.answered, rec.missing, rec.missing_required, rec.result) == (
        app_id, 2, 1, 1, 1, "ok")
    text = " ".join(str(v) for v in rec.__dict__.values())
    assert "ada@example.com" not in text and "Pronouns" not in text


# ── MCP ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mcp_check_form_matches_the_route(authenticated_async_context, fixture_user_id):
    pytest.importorskip("mcp")
    from mcp.server.mcpserver.exceptions import ToolError

    from src.api import mcp_server

    tool = mcp_server.build_server()._tool_manager.get_tool("check_form")
    assert tool is not None
    assert len(tool.description) <= 2000
    user = mcp_server.CurrentUser(
        id=fixture_user_id, email="e2e@example.com", email_verified=True, auth_via="token", actor_name="cc",
    )
    questions = [
        {"question": "Email", "key": "contact.email"},
        {"question": "Notice period (In days)?"},
        {"question": "Pronouns", "required": False},
    ]
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        await _seed_answers(client)
        token = mcp_server._current_user.set(user)
        try:
            route = (await _form_check(client, app_id, questions)).json()
            viatool = await tool.fn(application_id=app_id, questions=questions)
            assert viatool == route
            assert viatool["counts"] == {"answered": 2, "missing": 1, "missing_required": 0}
            assert viatool["answered"][0]["value"] == "ada@example.com"
            with pytest.raises(ToolError) as info:
                await tool.fn(application_id=999999, questions=questions)
            assert str(info.value).startswith("404")
            with pytest.raises(ToolError) as bad:
                await tool.fn(application_id=app_id, questions=[])
            assert str(bad.value).startswith("422")
        finally:
            mcp_server._current_user.reset(token)
