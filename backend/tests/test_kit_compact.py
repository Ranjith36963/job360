"""The compact application kit for PC runs (owner, 2026-11-18).

The hourly own-browser (PC) run of 2026-11-18 15:06 UTC stopped with about 45 tasks untouched: every
application task read the full kit (CV and letter text, up to 25 approved essays, the last fill, ten
instruction lines, every setting), around 34,000 characters each, and the run's context filled up.
`compact=true` keeps what a PC task types and checks (ids, form URL, file links, the short answers,
the gate facts) and swaps the long text for sizes and a pointer. Same route, same per-user gates,
same `kit_read` (it is the hold). The full kit stays the default.
Helpers are copied, never imported from another test module.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.core import settings
from src.services.applications import kit as kit_service
from src.services.profile.models import AssistantSettings, CVData, UserPreferences, UserProfile

# A realistic application: a two-page CV, a full letter, 28 saved essay answers, the usual Memory.
CV = "Ada Lovelace\nSenior data engineer | London\n\n" + "\n".join(
    f"- Built and ran data pipeline {i} in Python, dbt and Snowflake; cut its cost and halved its latency."
    for i in range(55)
)
LETTER = "Dear Northwind hiring team,\n\n" + ("I have spent eight years building reliable data platforms. " * 40)
_AD = {
    "title": "Data Engineer", "company": "Northwind", "location": "London", "country": "GB", "remote": False,
    "apply_url": "https://northwind.example/careers/7", "description": "Build the pipelines. " * 300,
}
FORM = "https://careers.northwind.example/apply/7"
ESSAYS = 28
# Budget, as the MCP tool serves it (indented JSON). This application's full kit is ~43,000 characters;
# its compact kit ~6,400, of which ~2,400 are the 14 short answers a task types (kept on purpose). 45 tasks
# x 7,000 = 315,000 characters, against ~1,950,000 for 45 full kits.
COMPACT_MAX_CHARS = 7000


def _seed_profile(user_id: str) -> None:
    from src.services.profile.storage import save_assistant_settings, save_profile

    save_profile(
        UserProfile(
            cv_data=CVData(raw_text="Python data engineer: dbt, Snowflake.", name="Ada Lovelace", location="London, UK",
                           links=["https://www.linkedin.com/in/ada", "https://github.com/ada"]),
            preferences=UserPreferences(target_job_titles=["Data Engineer"]),
        ),
        user_id, source_action="cv_upload",
    )
    save_assistant_settings(
        user_id, AssistantSettings(apply_min_score=50, above_line="auto", below_line="auto"), "test_seed"
    )


def _e(path: str, value: Any) -> dict[str, Any]:
    return {"path": path, "value": value}


async def _realistic_application(client: AsyncClient) -> int:
    resp = await client.post("/api/jobs/bring", json=_AD)
    assert resp.status_code == 200, resp.text
    app_id = int(resp.json()["application_id"])
    fit = await client.put(f"/api/applications/{app_id}/fit", json={"score": 80, "verdict": "ok", "score_source": "full_ad"})
    assert fit.status_code == 200, fit.text
    for kind, text in (("cv", CV), ("cover_letter", LETTER)):
        saved = await client.post(f"/api/applications/{app_id}/artifacts", json={"kind": kind, "text": text})
        assert saved.status_code == 201, saved.text
    essays = [
        {"question": f"Describe a time you showed our value number {i} at work?",
         "answer": ("In my last role I led a migration that " * 8) + f"({i})", "approved": True,
         "recorded_at": f"2026-09-{i + 1:02d}T09:00:00+00:00"}
        for i in range(ESSAYS)
    ]
    edits = await client.patch("/api/profile", json={"edits": [
        _e("user_info.contact", {"email": "ada@example.com", "phone": "+44 7700 900123",
                                 "legal_first_name": "Ada", "legal_last_name": "Lovelace"}),
        _e("user_info.right_to_work", {"countries": [
            {"country": "GB", "work_authorization": "citizen", "needs_sponsorship": False}]}),
        _e("user_info.logistics", {"notice_period": "1 month"}),
        _e("user_info.languages", [{"language": "English", "level": "native"}]),
        _e("user_info.equality", {"gender": "prefer not to say"}),
        _e("user_info.answers", essays),
        _e("preferences.salary_by_country", [
            {"country": "GB", "min": 85000, "max": 95000, "currency": "GBP", "period": "year"}]),
    ]})
    assert edits.status_code == 200, edits.text
    fill = [{"question": f"Question {i} on the form?", "answer": "Typed answer text " * 10, "source": "profile"}
            for i in range(20)]
    filled = await client.post(f"/api/applications/{app_id}/events", json={
        "event_type": "form_filled", "payload": {"form_url": FORM, "fields_count": 20, "answers": fill}})
    assert filled.status_code == 201, filled.text
    return app_id


async def _mint_token(client: AsyncClient) -> str:
    resp = await client.post("/api/tokens", json={"name": "claude-code"})
    assert resp.status_code == 201, resp.text
    return resp.json()["token"]


def _mcp_client(token: str):
    import httpx2
    from mcp.client import Client
    from mcp.client.streamable_http import streamable_http_client

    from src.api.main import app

    http = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )
    return Client(streamable_http_client("http://test/api/mcp", http_client=http))


def _text(result) -> str:
    assert not result.is_error, result.content[0].text
    return result.content[0].text


async def _second_user_session_cookie(email: str) -> str:
    from fastapi.testclient import TestClient

    from src.api.main import app
    from tests._signin import sign_in

    cookie, _ = sign_in(TestClient(app), email)
    return cookie


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[dict[str, Any]] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(dict(record.__dict__))


@pytest.fixture
def log_capture():
    audit = logging.getLogger("job360.audit")
    handler, old = _Capture(), audit.level
    audit.addHandler(handler)
    audit.setLevel(logging.INFO)
    yield handler
    audit.removeHandler(handler)
    audit.setLevel(old)


def _sql(statement: str) -> list[Any]:
    from src.repositories import pgsync

    conn = pgsync.connect(str(settings.DB_PATH))
    try:
        return conn.execute(statement).fetchall()
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_the_compact_kit_is_small_and_carries_every_fact_a_pc_task_uses(
    authenticated_async_context, fixture_user_id, log_capture
):
    from src.api.mcp_server import mcp_runtime

    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _realistic_application(client)
        token = await _mint_token(client)
    async with mcp_runtime():
        async with _mcp_client(token) as mcp:
            full_text = _text(await mcp.call_tool("get_application_kit", {"application_id": app_id, "session": "pc-1506"}))
            small_text = _text(await mcp.call_tool(
                "get_application_kit", {"application_id": app_id, "session": "pc-1506", "compact": True}))
    full, small = json.loads(full_text), json.loads(small_text)
    assert len(full_text) > 40_000, f"the realistic full kit is {len(full_text)} characters"
    assert len(small_text) < COMPACT_MAX_CHARS, f"compact kit is {len(small_text)} characters"
    assert len(full_text) > 6 * len(small_text)

    # The same application, the same kit content (its sha is of the FULL content), its own kit_read.
    assert small["kit"]["sha256"] == full["kit"]["sha256"] and small["kit"]["id"] > full["kit"]["id"]
    assert small["application"] == full["application"] and small["job"]["apply_url"] == _AD["apply_url"]
    for key in ("duplicate", "account_site", "autofill", "missing", "warnings", "answers_summary", "hold"):
        assert small[key] == full[key], key
    # The gate facts the PC run reads (settings: paused, email codes, the submit preview).
    assert small["settings"]["submit_preview"] == full["settings"]["submit_preview"]
    assert small["settings"]["paused"] is full["settings"]["paused"] is False
    for key in ("pause_reason", "paused_until", "use_email_codes", "submit_mode"):
        assert small["settings"][key] == full["settings"][key]["effective"], key
    assert "grant_covers_jobs" not in small["settings"] and "grant_covers_jobs" in full["settings"]
    # The ad's checks: codes and verdicts only (no ad quote or reason left to fence).
    assert small["job"]["checks"] == {c["code"]: c["ok"] for c in full["job"]["checks"]}
    # Files to upload: ids, the hash a cv_seen / record_application names, a FRESH link; no text.
    for kind in ("cv", "cover_letter"):
        doc, whole = small[kind], full[kind]
        assert (doc["artifact_id"], doc["version"], doc["sha256"], doc["chars"]) == (
            whole["artifact_id"], whole["version"], whole["sha256"], whole["chars"])
        assert doc["text"] == "" and "text_more" not in doc
        assert doc["file"]["url"].startswith(settings.SITE_BASE_URL + "/api/files/")
        assert doc["file"]["url"] != whole["file"]["url"] and doc["file"]["downloads_left"] == 3
        assert doc["checks"] == whole["checks"]
    assert full["cv"]["text"] == CV, "the default kit still carries the text"
    # Every short answer to type, with its key, value and state; the essays become a count.
    for group in ("contact", "right_to_work", "logistics", "salary", "languages", "equality"):
        assert [(i["key"], i["value"], i["state"]) for i in small["answers"][group]] == [
            (i["key"], i["value"], i["state"]) for i in full["answers"][group]], group
    assert "approved_text" not in small["answers"] and small["answers_omitted"] == ESSAYS
    assert len(full["answers"]["approved_text"]) == settings.KIT_APPROVED_TEXT_MAX
    assert small["last_fill"] == [] and len(full["last_fill"]) == 20
    assert full["instructions"][: len(kit_service.KIT_INSTRUCTIONS)] == list(kit_service.KIT_INSTRUCTIONS)
    # The pointer: where each part left out is fetched on demand.
    said = " ".join(small["instructions"])
    assert "check_form" in said and "compact=false" in said and kit_service.TEXT_MORE in said
    assert f"{ESSAYS} approved_text" in said and "20 last_fill" in said
    # The log line that would have shown the 15:06 run: which kit each read was.
    assert [r["compact"] for r in log_capture.records if r.get("event") == "kit_read"] == [False, True]
    calls = [r for r in log_capture.records if r.get("event") == "mcp_tool_call" and r.get("tool") == "get_application_kit"]
    assert [r["compact"] for r in calls] == [False, True]


@pytest.mark.asyncio
async def test_compact_is_the_same_hold_and_http_door(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _realistic_application(client)
        before = _sql("SELECT COUNT(*) FROM artifact_links")[0][0]
        resp = await client.get(f"/api/applications/{app_id}/kit", params={"compact": "true", "session": "pc-1506"})
        assert resp.status_code == 200, resp.text
        assert resp.headers["cache-control"] == "no-store"
        body = resp.json()
        assert body["cv"]["text"] == "" and body["answers"]["approved_text"] == []
        assert _sql("SELECT COUNT(*) FROM artifact_links")[0][0] == before + 2, "one fresh link per document"
        reads = _sql("SELECT payload FROM application_events WHERE event_type = 'kit_read'")
        assert len(reads) == 1 and "pc-1506" in json.dumps(reads[0][0])
        # A second session sees the compact read as the hold, like any kit read.
        other = (await client.get(f"/api/applications/{app_id}/kit", params={"session": "chat-2"})).json()
        assert other["hold"] and other["hold"]["session"] == "pc-1506"


@pytest.mark.asyncio
async def test_another_users_compact_kit_is_404_on_both_doors(authenticated_async_context, fixture_user_id):
    from src.api.main import app
    from src.api.mcp_server import mcp_runtime

    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _realistic_application(client)
    cookie = await _second_user_session_cookie("compact-intruder@example.com")
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", cookies={"job360_session": cookie}
    ) as intruder:
        got = await intruder.get(f"/api/applications/{app_id}/kit", params={"compact": "true"})
        assert got.status_code == 404
        token = await _mint_token(intruder)
    async with mcp_runtime():
        async with _mcp_client(token) as mcp:
            denied = await mcp.call_tool("get_application_kit", {"application_id": app_id, "compact": True})
            assert denied.is_error and "404" in denied.content[0].text
    assert _sql("SELECT COUNT(*) FROM artifact_links")[0][0] == 0
    assert _sql("SELECT COUNT(*) FROM application_events WHERE event_type = 'kit_read'")[0][0] == 0


def test_the_pc_run_asks_for_the_compact_kit():
    from src.api.routes.recipes import load_recipe
    from src.services.applications import own_browser

    assert "get_application_kit(application_id, session, compact=true)" in load_recipe("own_browser").text
    hint = own_browser._next_step({"kind": "application", "application_id": 7})
    assert hint == 'get_application_kit(application_id=7, session="<your session>", compact=true)'
