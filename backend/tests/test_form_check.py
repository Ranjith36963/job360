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
        {"question": "Favourite colour", "key": "logistics.colour"},
    ])
    assert [(a["question"], a["key"], a["value"], a["matched_by"]) for a in out["answered"]] == [
        ("Email", "contact.email", "a@b.c", "key"),
        ("Notice (days)", "Notice period (in days)?", "30", "key"),
        ("NOTICE PERIOD in days", "Notice period (in days)?", "30", "wording"),
        ("Phone", "contact.phone", "123", "key"),
    ]
    assert out["answered"][3]["confirmed"] is False and "confirmed" not in out["answered"][0]
    assert out["missing"] == [
        {"question": "Favourite colour", "required": True, "why": "key 'logistics.colour' is not in this application's kit"}
    ]
    assert out["questions_count"] == 5
    assert out["counts"] == {"answered": 4, "missing": 1, "missing_required": 1}
    assert out["answered"][3]["needs_confirmation"] is True  # confirmed false = inferred = confirm with the user
    assert out["next"] == ASK_NEXT + kit_service.FORM_NEXT_CONFIRM


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
        # Notice period is a sensitive question (gap audit #14); this answer is the user's own approved one,
        # so it is filled as it is (owner 2026-10-26: only an answer the user has not stood behind waits).
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
            {"question": "Visa?", "required": True, "needs_confirmation": True,
             "why": "key 'right_to_work.GB.invented' is not in this application's kit"},
            {"question": "Favourite colour", "required": False, "why": "no saved answer for this question"},
            {"question": "Pronouns", "required": True, "why": "no saved answer for this question"},
        ]
        assert body["counts"] == {"answered": 1, "missing": 3, "missing_required": 2}
        assert body["questions_count"] == 4
        # "Visa?" is sensitive AND a knock-out (weak spot #1b): the ask also says to confirm and to stop.
        assert body["next"] == ASK_NEXT + kit_service.FORM_NEXT_CONFIRM + kit_service.FORM_NEXT_KNOCKOUT
        assert body["knockouts"] == [{"question": "Visa?", "key": "right_to_work.GB.invented"}]


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


# ── Weak spot #1b: control facts, sensitive answers, knock-outs, form_checked, sweep shortfalls ──────────────


def test_limit_over_limit_flag_keeps_the_value_whole():
    answers = {"approved_text": [{"key": "Why us?", "value": "x" * 300, "source": "approved_text", "saved_at": None}]}
    out = kit_service.form_check(answers, [
        {"question": "Why us?", "field_type": "textarea", "limit": 200, "limit_unit": "chars"},
        {"question": "Why us?", "field_type": "textarea", "limit": 400},
    ])
    assert out["answered"][0]["over_limit"] is True and len(out["answered"][0]["value"]) == 300
    assert "over_limit" not in out["answered"][1]
    words = {"approved_text": [{"key": "Why us?", "value": "one two three four", "source": "approved_text"}]}
    row = kit_service.form_check(words, [{"question": "Why us?", "limit": 3, "limit_unit": "words"}])["answered"][0]
    assert row["over_limit"] is True


def test_options_mismatch_moves_to_missing_and_casefold_matches():
    answers = {"logistics": [{"key": "logistics.remote", "value": "Yes", "source": "memory", "saved_at": "x"}]}
    bad = kit_service.form_check(answers, [
        {"question": "Remote?", "key": "logistics.remote", "field_type": "select", "options": ["No", "Maybe"]},
    ])
    assert bad["answered"] == [] and bad["missing"][0]["why"] == "saved answer is not one of the form's options"
    assert bad["missing"][0]["field_type"] == "select"
    ok = kit_service.form_check(answers, [
        {"question": "Remote?", "key": "logistics.remote", "field_type": "radio", "options": ["yes", "No"]},
    ])
    assert ok["missing"] == [] and ok["answered"][0]["value"] == "Yes" and ok["answered"][0]["field_type"] == "radio"
    free = kit_service.form_check(answers, [{"question": "Remote?", "key": "logistics.remote", "options": ["No"]}])
    assert free["answered"][0]["value"] == "Yes"  # options only bind select / radio / checkbox


def test_sensitive_needs_confirmation_and_plain_contact_does_not():
    answers = {
        "salary": [{"key": "salary.GB", "value": "60000", "source": "memory", "saved_at": "x"}],
        "right_to_work": [{"key": "right_to_work.GB", "value": "Yes", "source": "profile", "saved_at": "x"}],
        "contact": [
            {"key": "contact.email", "value": "a@b.c", "source": "memory", "saved_at": "x"},
            {"key": "contact.phone", "value": "1", "source": "memory", "saved_at": "x", "confirmed": False},
        ],
    }
    out = kit_service.form_check(answers, [
        {"question": "Expected salary", "key": "salary.GB"},
        {"question": "Right to work?", "key": "right_to_work.GB"},
        {"question": "Email", "key": "contact.email"},
        {"question": "Phone", "key": "contact.phone"},
    ])
    flags = {a["key"]: a.get("needs_confirmation", False) for a in out["answered"]}
    assert flags == {"salary.GB": True, "right_to_work.GB": True, "contact.email": False, "contact.phone": True}
    assert out["needs_confirmation"] == ["Expected salary", "Right to work?", "Phone"]
    assert out["next"].endswith(kit_service.FORM_NEXT_CONFIRM) is False  # a knock-out is listed too, after it
    assert kit_service.FORM_NEXT_CONFIRM in out["next"]


def test_knockout_listed_with_value_and_stop_and_ordinary_question_is_not():
    answers = {"right_to_work": [{"key": "right_to_work.GB", "value": "Yes", "source": "profile", "saved_at": "x"}]}
    out = kit_service.form_check(answers, [
        {"question": "Will you require visa sponsorship?", "key": "right_to_work.GB"},
        {"question": "Do you have 5+ years of professional experience?"},
        {"question": "Favourite colour"},
    ])
    assert [k["question"] for k in out["knockouts"]] == [
        "Will you require visa sponsorship?", "Do you have 5+ years of professional experience?",
    ]
    assert out["knockouts"][0]["value"] == "Yes" and "value" not in out["knockouts"][1]
    assert out["stop_for_user"] is True and kit_service.FORM_NEXT_KNOCKOUT in out["next"]
    calm = kit_service.form_check(answers, [{"question": "Favourite colour"}])
    assert calm["knockouts"] == [] and "stop_for_user" not in calm


@pytest.mark.asyncio
async def test_bad_field_type_is_422_and_default_is_unknown(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        bad = await _form_check(client, app_id, [{"question": "Email", "field_type": "dropdown"}])
        assert bad.status_code == 422
        long_option = await _form_check(client, app_id, [{"question": "Email", "options": ["x" * 201]}])
        assert long_option.status_code == 422
        ok = (await _form_check(client, app_id, [{"question": "Email", "field_type": "text", "limit": 50}])).json()
        assert ok["missing"][0]["field_type"] == "text"


def test_pure_file_question_for_cv_or_letter_is_answered_by_the_saved_artifact():
    docs = {"cv": {"id": 83, "version_no": 1}, "cover_letter": {"id": 90, "version_no": 2}}
    qs = [
        {"question": "CV", "field_type": "file"},
        {"question": "Upload your Resume / Curriculum Vitae", "field_type": "file"},
        {"question": "Motivation letter", "field_type": "file", "required": False},
        {"question": "Portfolio", "field_type": "file"},
        {"question": "Proof of ID", "field_type": "file"},
        {"question": "CV or cover letter", "field_type": "file"},
        {"question": "CV", "field_type": "text"},
    ]
    out = kit_service.form_check({}, qs, documents=docs)
    by_q = {a["question"]: a for a in out["answered"]}
    assert by_q["CV"]["value"] == "CV v1 (artifact 83)" and by_q["CV"]["source"] == "artifact"
    assert by_q["Upload your Resume / Curriculum Vitae"]["value"] == "CV v1 (artifact 83)"
    assert by_q["Motivation letter"]["value"] == "Cover letter v2 (artifact 90)"
    assert [m["question"] for m in out["missing"]] == ["Portfolio", "Proof of ID", "CV or cover letter", "CV"]
    assert {m["why"] for m in out["missing"]} == {"no saved answer for this question"}
    # No artifact saved: the CV stays missing and says what to do.
    bare = kit_service.form_check({}, qs[:1] + qs[3:4])
    assert [m["question"] for m in bare["missing"]] == ["CV", "Portfolio"]
    assert bare["missing"][0]["why"] == "no CV saved for this application yet: save the CV first (save_artifact kind cv)"
    assert bare["missing"][1]["why"] == "no saved answer for this question"


@pytest.mark.asyncio
async def test_file_question_for_the_cv_lands_in_answered_once_a_cv_is_saved(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        questions = [
            {"question": "CV", "field_type": "file"},
            {"question": "Cover letter", "field_type": "file", "required": False},
            {"question": "Portfolio", "field_type": "file", "required": False},
        ]
        before = (await _form_check(client, app_id, questions)).json()
        assert before["answered"] == []
        assert before["missing"][0]["why"] == (
            "no CV saved for this application yet: save the CV first (save_artifact kind cv)"
        )
        saved = await client.post(f"/api/applications/{app_id}/artifacts", json={"kind": "cv", "text": "CV TEXT"})
        assert saved.status_code == 201, saved.text
        cv_id = saved.json()["artifact_id"]
        body = (await _form_check(client, app_id, questions)).json()
        assert [(a["question"], a["value"], a["source"], a["matched_by"]) for a in body["answered"]] == [
            ("CV", f"CV v1 (artifact {cv_id})", "artifact", "wording")
        ]
        assert [m["question"] for m in body["missing"]] == ["Cover letter", "Portfolio"]
        assert body["counts"] == {"answered": 1, "missing": 2, "missing_required": 0}
        letter = await client.post(
            f"/api/applications/{app_id}/artifacts", json={"kind": "cover_letter", "text": "LETTER TEXT"}
        )
        assert letter.status_code == 201, letter.text
        again = (await _form_check(client, app_id, questions)).json()
        assert [a["value"] for a in again["answered"]] == [
            f"CV v1 (artifact {cv_id})", f"Cover letter v1 (artifact {letter.json()['artifact_id']})"
        ]
        assert [m["question"] for m in again["missing"]] == ["Portfolio"]


async def _post_event(client: AsyncClient, app_id: int, event_type: str, payload: dict[str, Any]):
    return await client.post(
        f"/api/applications/{app_id}/events", json={"event_type": event_type, "payload": payload}
    )


@pytest.mark.asyncio
async def test_form_checked_event_stores_no_values(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        await _seed_answers(client)
        route = (await _form_check(client, app_id, [{"question": "Email", "key": "contact.email"}])).json()
        payload = {
            "questions_count": route["questions_count"], "answered": 1, "missing": 0,
            "answers": [{"question": "Email", "key": "contact.email", "source": "memory"}],
        }
        resp = await _post_event(client, app_id, "form_checked", payload)
        assert resp.status_code == 201, resp.text
        stored = _count(
            "SELECT COUNT(*) FROM application_events WHERE application_id = ? AND event_type = 'form_checked' "
            "AND payload NOT LIKE ?", (app_id, "%ada@example.com%"),
        )
        assert stored == 1  # the saved value is nowhere in the stored payload
        leak = await _post_event(client, app_id, "form_checked", {**payload, "answers": [
            {"question": "Email", "source": "memory", "value": "ada@example.com"},
        ]})
        assert leak.status_code == 422 and "Valid example payload:" in leak.text


@pytest.mark.asyncio
async def test_check_submit_stops_on_a_short_sweep_and_on_unreadable_fields(
    authenticated_async_context, fixture_user_id
):
    form = "https://careers.northwind.example/apply/7"
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)

        async def verdict() -> dict[str, Any]:
            resp = await client.get(f"/api/applications/{app_id}/submit-check", params={"form_url": form})
            assert resp.status_code == 200, resp.text
            return resp.json()

        assert (await _post_event(client, app_id, "form_filled", {"form_url": form, "fields_count": 9})).status_code == 201
        assert (await _post_event(client, app_id, "form_swept", {"fields_checked": 9, "fixed": 0})).status_code == 201
        # No form_checked on record (an older application): the sweep alone is enough.
        assert (await verdict())["reason"] not in ("form_count_mismatch", "unreadable_fields")
        checked = {"questions_count": 12, "answered": 12, "missing": 0}
        assert (await _post_event(client, app_id, "form_checked", checked)).status_code == 201
        short = await verdict()
        assert (short["decision"], short["reason"]) == ("fill", "form_count_mismatch")
        assert (await _post_event(client, app_id, "form_swept", {"fields_checked": 12, "fixed": 0})).status_code == 201
        assert (await verdict())["reason"] not in ("form_count_mismatch", "unreadable_fields")
        unread = {"fields_checked": 12, "fixed": 0, "unreadable": ["Resume upload"]}
        assert (await _post_event(client, app_id, "form_swept", unread)).status_code == 201
        stuck = await verdict()
        assert (stuck["decision"], stuck["reason"]) == ("fill", "unreadable_fields")
        assert (await _post_event(client, app_id, "form_swept", {"fields_checked": 12, "fixed": 1})).status_code == 201
        assert (await verdict())["reason"] not in ("form_count_mismatch", "unreadable_fields")


# ── Weak spot #1b (c): derived form_state and the form_check_missing gate ───────────────────


def _event_count(app_id: int) -> int:
    return _count("SELECT COUNT(*) FROM application_events WHERE application_id = ?", (app_id,))


@pytest.mark.asyncio
async def test_form_state_goes_filled_then_submitted_without_touching_events(
    authenticated_async_context, fixture_user_id
):
    form = "https://careers.northwind.example/apply/7"
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)

        async def state() -> Any:
            resp = await client.get(f"/api/applications/{app_id}")
            assert resp.status_code == 200, resp.text
            return resp.json()["form_state"]

        assert await state() is None  # nothing filled yet
        assert (await _post_event(client, app_id, "form_filled", {"form_url": form, "fields_count": 9})).status_code == 201
        assert await state() == "filled"
        before = _event_count(app_id)
        rec = await client.post(
            f"/api/applications/{app_id}/receipt", json={"channel": "company site", "note": "sent"},
        )
        assert rec.status_code in (200, 201), rec.text
        assert await state() == "submitted"
        # append-only: the state moved, the recorded events only grew (never edited or removed)
        assert _event_count(app_id) >= before


def test_derive_form_state_is_pure():
    from src.services.applications.spine import derive_form_state

    filled = {"event_type": "form_filled"}
    assert derive_form_state([], []) is None
    assert derive_form_state([filled], []) == "filled"
    assert derive_form_state([filled], [{"id": 1}]) == "submitted"
    assert derive_form_state([{"event_type": "note"}], [{"id": 1}]) is None  # a receipt alone is not a filled form


def test_form_check_required_follows_the_cutoff_day(form_check_gate):
    assert kit_service.FORM_CHECK_REQUIRED_FROM.date().isoformat() == "2026-10-15"
    assert kit_service.form_check_required("2026-10-14T23:59:59+00:00") is False
    assert kit_service.form_check_required("2026-10-15T00:00:00+00:00") is True
    assert kit_service.form_check_required("2026-10-15T08:00:00") is True  # naive = UTC
    assert kit_service.form_check_required("not a date") is False and kit_service.form_check_required(None) is False


@pytest.mark.asyncio
async def test_new_application_needs_form_checked_old_one_does_not(
    authenticated_async_context, fixture_user_id, form_check_gate, monkeypatch
):
    from datetime import datetime, timezone

    form = "https://careers.northwind.example/apply/7"
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)  # created now: after the cut-off

        async def verdict() -> dict[str, Any]:
            resp = await client.get(f"/api/applications/{app_id}/submit-check", params={"form_url": form})
            assert resp.status_code == 200, resp.text
            return resp.json()

        assert (await _post_event(client, app_id, "form_filled", {"form_url": form, "fields_count": 9})).status_code == 201
        assert (await _post_event(client, app_id, "form_swept", {"fields_checked": 9, "fixed": 0})).status_code == 201
        stop = await verdict()
        assert (stop["decision"], stop["reason"]) == ("fill", "form_check_missing")
        assert "check_form" in stop["detail"]
        # the kit's own preview imagines the form checked: it never shows this stop before the fill
        # An OLDER application (cut-off later than its created_at) keeps working with no form_checked.
        monkeypatch.setattr(kit_service, "FORM_CHECK_REQUIRED_FROM", datetime(2999, 1, 1, tzinfo=timezone.utc))
        assert (await verdict())["reason"] != "form_check_missing"
        monkeypatch.undo()
        # recording the form check clears the stop (count 9 == swept 9)
        checked = {"questions_count": 9, "answered": 9, "missing": 0}
        assert (await _post_event(client, app_id, "form_checked", checked)).status_code == 201
        assert (await verdict())["reason"] != "form_check_missing"


# ── Owner rule 2026-10-20 (strict): every REQUIRED question is answered, only optional ones may stay blank ──


@pytest.mark.asyncio
async def test_a_required_question_left_unanswered_stops_the_submit_until_it_is_answered(
    authenticated_async_context, fixture_user_id, form_check_gate
):
    form = "https://careers.northwind.example/apply/7"
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)

        async def verdict() -> dict[str, Any]:
            resp = await client.get(f"/api/applications/{app_id}/submit-check", params={"form_url": form})
            assert resp.status_code == 200, resp.text
            return resp.json()

        async def fill_and_sweep() -> None:
            assert (await _post_event(client, app_id, "form_filled", {"form_url": form, "fields_count": 9})).status_code == 201
            assert (await _post_event(client, app_id, "form_swept", {"fields_checked": 9, "fixed": 0})).status_code == 201

        # 2 questions have no saved answer and one of them is required
        one_required = {"questions_count": 9, "answered": 7, "missing": 2, "missing_required": 1}
        assert (await _post_event(client, app_id, "form_checked", one_required)).status_code == 201
        await fill_and_sweep()
        stop = await verdict()
        assert (stop["decision"], stop["reason"]) == ("stop", "required_unanswered")
        assert "ask_user" in stop["detail"] and "Optional questions may stay blank" in stop["detail"]

        # the user answered it; check_form is run again and now only an OPTIONAL question is missing
        only_optional = {"questions_count": 9, "answered": 8, "missing": 1, "missing_required": 0}
        assert (await _post_event(client, app_id, "form_checked", only_optional)).status_code == 201
        await fill_and_sweep()
        assert (await verdict())["reason"] != "required_unanswered"


@pytest.mark.asyncio
async def test_missing_questions_without_saying_how_many_are_required_count_as_required(
    authenticated_async_context, fixture_user_id, form_check_gate
):
    form = "https://careers.northwind.example/apply/7"
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        vague = {"questions_count": 9, "answered": 8, "missing": 1}  # no missing_required: it must say
        assert (await _post_event(client, app_id, "form_checked", vague)).status_code == 201
        assert (await _post_event(client, app_id, "form_filled", {"form_url": form, "fields_count": 9})).status_code == 201
        assert (await _post_event(client, app_id, "form_swept", {"fields_checked": 9, "fixed": 0})).status_code == 201
        resp = await client.get(f"/api/applications/{app_id}/submit-check", params={"form_url": form})
        assert resp.json()["reason"] == "required_unanswered"
        # more required than missing is nonsense: refused
        bad = await _post_event(client, app_id, "form_checked", {
            "questions_count": 9, "answered": 8, "missing": 1, "missing_required": 2,
        })
        assert bad.status_code == 422 and "missing_required" in bad.text


@pytest.mark.asyncio
async def test_each_page_of_a_multi_page_form_is_judged_by_its_newest_check(
    authenticated_async_context, fixture_user_id, form_check_gate
):
    form = "https://careers.northwind.example/apply/7"
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)

        async def check(page: int, missing_required: int) -> None:
            body = {
                "questions_count": 4, "answered": 4 - missing_required, "missing": missing_required,
                "missing_required": missing_required, "page": page, "pages_total": 2,
            }
            assert (await _post_event(client, app_id, "form_checked", body)).status_code == 201

        await check(1, 0)
        await check(2, 1)  # page 2 has a required question with no answer; page 1 is fine
        assert (await _post_event(client, app_id, "form_filled", {"form_url": form, "fields_count": 8})).status_code == 201
        for page in (1, 2):
            sweep = {"fields_checked": 4, "fixed": 0, "page": page, "pages_total": 2}
            assert (await _post_event(client, app_id, "form_swept", sweep)).status_code == 201
        resp = await client.get(f"/api/applications/{app_id}/submit-check", params={"form_url": form})
        assert resp.json()["reason"] == "required_unanswered"
        await check(2, 0)  # page 2 checked again after the user answered
        sweep = {"fields_checked": 4, "fixed": 0, "page": 2, "pages_total": 2}
        assert (await _post_event(client, app_id, "form_swept", sweep)).status_code == 201
        resp = await client.get(f"/api/applications/{app_id}/submit-check", params={"form_url": form})
        assert resp.json()["reason"] != "required_unanswered"
