"""S3 - THE APPLICATION KIT (owner decisions 2026-10-08).

The kit is what an assistant reads to fill ONE application form: the CV and
letter of THAT application, every stored answer with its source, what is missing
for the job's country, a short-lived file link, and the human-in-the-loop state.
Tests go through the real doors (HTTP + MCP) and assert VALUES, not key presence
(rule #21). Helpers are copied, never imported from another test module.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.core import settings
from src.services.applications import kit as kit_service
from src.services.profile import assistant_settings as rules
from src.services.profile.models import AssistantSettings, CVData, UserPreferences, UserProfile

CV_TEXT = "Ada Lovelace\nSenior data engineer\nPython, dbt, Snowflake."
LETTER_TEXT = "Dear Northwind, I would love to join."
FORM = "https://careers.northwind.example/apply/7"
NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)

_AD = {
    "title": "Data Engineer", "company": "Northwind", "location": "London", "country": "GB", "remote": False,
    "apply_url": "https://northwind.example/careers/7",
    "description": "Build the pipelines. Python, dbt, Snowflake.",
}
_AD_2 = {
    **_AD, "title": "Platform Engineer", "company": "Southwind", "country": "DE",
    "apply_url": "https://southwind.example/c/3",
}
_AD_NO_COUNTRY = {
    **_AD, "title": "ML Engineer", "company": "Eastwind", "apply_url": "https://eastwind.example/c/9",
}
del _AD_NO_COUNTRY["country"]


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ── helpers (copied, never imported across test modules) ─────────────────────


def _seed_profile(user_id: str) -> None:
    from src.services.profile.storage import save_profile

    save_profile(
        UserProfile(
            cv_data=CVData(raw_text="Python data engineer.", name="Ada Lovelace", location="London, UK",
                           links=["https://www.linkedin.com/in/ada", "https://github.com/ada"]),
            preferences=UserPreferences(target_job_titles=["Data Engineer"]),
        ),
        user_id, source_action="cv_upload",
    )


def _e(path: str, value: Any) -> dict[str, Any]:
    return {"path": path, "value": value}


async def _patch(client: AsyncClient, *edits_: dict[str, Any]):
    return await client.patch("/api/profile", json={"edits": list(edits_)})


async def _mint_token(client: AsyncClient, name: str = "claude-code") -> str:
    resp = await client.post("/api/tokens", json={"name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()["token"]


def _bearer_client(token: str) -> AsyncClient:
    from src.api.main import app

    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    )


def _anon_client() -> AsyncClient:
    from src.api.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _bring(client: AsyncClient, ad: dict[str, Any] = _AD) -> int:
    resp = await client.post("/api/jobs/bring", json=ad)
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _save(client: AsyncClient, app_id: int, kind: str, text: str) -> dict[str, Any]:
    resp = await client.post(f"/api/applications/{app_id}/artifacts", json={"kind": kind, "text": text})
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _kit(client: AsyncClient, app_id: int):
    return await client.get(f"/api/applications/{app_id}/kit")


async def _event(client: AsyncClient, app_id: int, event_type: str, payload: dict[str, Any]):
    return await client.post(
        f"/api/applications/{app_id}/events", json={"event_type": event_type, "payload": payload}
    )


async def _controls(client: AsyncClient, app_id: int) -> dict[str, Any]:
    resp = await client.get(f"/api/applications/{app_id}/controls")
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _check(client: AsyncClient, app_id: int, url: str = FORM) -> dict[str, Any]:
    resp = await client.get(f"/api/applications/{app_id}/submit-check", params={"form_url": url})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _token_of(kit: dict[str, Any], doc: str = "cv") -> str:
    return kit[doc]["file"]["url"].rsplit("/", 1)[1]


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[dict[str, Any]] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(dict(record.__dict__))


@pytest.fixture
def log_capture():
    """Every record on the root logger (audit, access, error) - for 'no token in any log'."""
    root = logging.getLogger()
    handler = _Capture()
    root.addHandler(handler)
    old = root.level
    root.setLevel(logging.DEBUG)
    audit = logging.getLogger("job360.audit")
    old_audit = audit.level
    audit.setLevel(logging.INFO)
    yield handler
    root.removeHandler(handler)
    root.setLevel(old)
    audit.setLevel(old_audit)


def _events(capture: _Capture, name: str) -> list[dict[str, Any]]:
    return [r for r in capture.records if r.get("event") == name]


async def _second_user_session_cookie(email: str = "second@example.com") -> str:
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


def _sql(statement: str, params: tuple = ()) -> list[Any]:
    from src.repositories import pgsync

    conn = pgsync.connect(str(settings.DB_PATH))
    try:
        cur = conn.execute(statement, params)
        rows = cur.fetchall() if statement.lstrip().upper().startswith("SELECT") else []
        conn.commit()
        return rows
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════════════
# Pure: the gate, hosts, urls
# ═══════════════════════════════════════════════════════════════════════════


def _cfg(**kw: Any) -> rules.EffectiveSettings:
    return rules.effective(AssistantSettings(**kw))


def _facts(**kw: Any) -> rules.SubmitFacts:
    base: dict[str, Any] = {"status": "considering", "has_receipt": False, "submit_override": None}
    base.update(kw)
    return rules.SubmitFacts(**base)


COUNTS = rules.SubmitCounts(submitted_today=0, applied_since_auto_on=1)
AUTO = {"submit_mode": "auto_when_sure"}


def _gate(cfg: rules.EffectiveSettings, facts: rules.SubmitFacts, url: str = FORM, counts=COUNTS):
    out = rules.may_submit(cfg, facts, url, counts, now=NOW)
    return out.decision, out.reason


def test_gate_auto_and_unseen_asks_cv_not_seen_and_no_cv_counts_as_unseen():
    auto = _cfg(submit_mode="auto_when_sure")
    assert _gate(auto, _facts()) == ("ask", "cv_not_seen")
    assert _gate(auto, _facts(cv_seen=True)) == ("submit", "auto_when_sure")
    assert _gate(auto, _facts(cv_seen=True), counts=rules.SubmitCounts(0, 0)) == ("ask", "practice_run")


def test_gate_a_matching_yes_clears_asks_including_brand_sites_and_practice_run():
    confirm = _cfg()
    assert _gate(confirm, _facts()) == ("ask", "submit_mode_confirm")
    assert _gate(confirm, _facts(approved=True)) == ("submit", "user_approved")
    auto = _cfg(submit_mode="auto_when_sure")
    assert _gate(auto, _facts(approved=True), url="https://uk.indeed.com/x") == ("submit", "user_approved")
    assert _gate(auto, _facts(approved=True), url="", counts=rules.SubmitCounts(0, 0)) == ("submit", "user_approved")
    assert _gate(auto, _facts(approved=True, cv_seen=True), url="") == ("submit", "user_approved")


def test_gate_stops_are_never_overridden_by_a_yes():
    yes = {"approved": True, "cv_seen": True}
    paused = _cfg(paused_until="until_resumed", submit_mode="auto_when_sure")
    assert _gate(paused, _facts(**yes)) == ("stop", "paused")
    assert _gate(_cfg(), _facts(status="applied", **yes)) == ("stop", "already_applied")
    assert _gate(_cfg(), _facts(declined=True, **yes)) == ("stop", "user_declined")
    capped = _cfg(daily_cap=1)
    assert _gate(capped, _facts(**yes), counts=rules.SubmitCounts(1, 1)) == ("stop", "daily_cap_reached")
    # a paused account beats a decline that beats the cap, in rule order
    assert _gate(paused, _facts(declined=True)) == ("stop", "paused")


def test_gate_duplicate_job_stops_in_auto_and_asks_otherwise():
    dup = {"duplicate_job": True, "cv_seen": True}
    out = rules.may_submit(_cfg(submit_mode="auto_when_sure"), _facts(**dup), FORM, COUNTS, now=NOW)
    assert (out.decision, out.reason) == ("stop", "duplicate_job") and "Needs you: possible duplicate" in out.detail
    assert _gate(_cfg(), _facts(**dup)) == ("ask", "duplicate_job")
    # a yes clears the ASK, never the unattended-auto STOP
    assert _gate(_cfg(), _facts(approved=True, **dup)) == ("submit", "user_approved")
    assert _gate(_cfg(submit_mode="auto_when_sure"), _facts(approved=True, **dup)) == ("stop", "duplicate_job")
    # the per-job override counts as the mode
    assert _gate(_cfg(), _facts(submit_override="auto_when_sure", **dup)) == ("stop", "duplicate_job")


def test_every_reason_the_gate_can_return_is_in_the_closed_list():
    for reason in ("user_declined", "duplicate_job", "cv_not_seen", "user_approved"):
        assert reason in rules.REASONS


def test_account_hosts_seed_subdomain_and_lookalike(monkeypatch):
    needs = kit_service.host_needs_account
    for host in ("acme.myworkdayjobs.com", "myworkdayjobs.com", "wd5.myworkdaysite.com", "acme.taleo.net",
                 "careers-acme.icims.com", "career5.successfactors.eu", "performancemanager.successfactors.com"):
        assert needs(host, set()), host
    for host in ("notmyworkdayjobs.com", "myworkdayjobs.com.evil.example", "icims.com.au", "northwind.example", None):
        assert not needs(host, set()), host
    assert needs("jobs.bigco.example", {"jobs.bigco.example"}), "a learned host counts"
    assert not needs("jobs.bigco.example", {"other.example"})
    monkeypatch.setattr(settings, "ACCOUNT_REQUIRED_HOST_SUFFIXES", ("bigco.example",))
    assert needs("jobs.bigco.example", set()), "the seed list is a parameter"


def test_norm_url_keeps_the_query_and_drops_fragment_and_trailing_slash():
    assert kit_service.norm_url("HTTPS://Northwind.Example/careers/7/#apply") == "https://northwind.example/careers/7"
    assert kit_service.norm_url("https://x.example/j?id=1") != kit_service.norm_url("https://x.example/j?id=2")


def test_file_stem_matches_the_download_name_rule():
    assert kit_service.file_stem("Northwind Ltd.", "cv", 3) == "northwind-ltd-cv-v3"
    assert kit_service.file_stem("", "cover_letter", 1) == "cover-letter-v1"


def test_redact_path_hides_the_token_everywhere():
    from src.utils.logger import redact_path

    assert redact_path("/api/files/abc123_TOKEN-xyz") == "/api/files/[redacted]"
    assert redact_path("https://job360.uk/api/files/abc123") == "https://job360.uk/api/files/[redacted]"
    assert redact_path("/api/applications/5/kit") == "/api/applications/5/kit"


def test_sentry_scrub_redacts_the_token_in_url_and_transaction():
    from src.core.observability import _scrub_pii

    event = {
        "transaction": "/api/files/SECRETTOKEN",
        "request": {"url": "http://test/api/files/SECRETTOKEN", "headers": {}},
    }
    out = _scrub_pii(event, None)
    assert "SECRETTOKEN" not in json.dumps(out)
    assert out["request"]["url"].endswith("/api/files/[redacted]")


def test_uvicorn_access_filter_redacts_the_token():
    import logging as _logging

    from src.utils.logger import AccessPathRedactFilter

    record = _logging.LogRecord(
        "uvicorn.access", _logging.INFO, "x", 1, '%s - "%s %s HTTP/%s" %d',
        ("1.2.3.4:5", "GET", "/api/files/SECRETTOKEN", "1.1", 200), None,
    )
    AccessPathRedactFilter().filter(record)
    assert "SECRETTOKEN" not in record.getMessage() and "/api/files/[redacted]" in record.getMessage()


# ═══════════════════════════════════════════════════════════════════════════
# The kit: values, sources, missing, binding to THIS application
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_the_kit_carries_the_cv_letter_job_and_hashes(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        cv = await _save(client, app_id, "cv", CV_TEXT)
        letter = await _save(client, app_id, "cover_letter", LETTER_TEXT)
        resp = await _kit(client, app_id)
        assert resp.status_code == 200, resp.text
        assert resp.headers["cache-control"] == "no-store"
        kit = resp.json()
        assert kit["cv"]["text"] == CV_TEXT and kit["cv"]["sha256"] == sha(CV_TEXT)
        assert kit["cv"]["artifact_id"] == cv["artifact_id"] and kit["cv"]["version"] == 1
        assert kit["cv"]["chars"] == len(CV_TEXT)
        assert kit["cover_letter"]["text"] == LETTER_TEXT
        assert kit["cover_letter"]["artifact_id"] == letter["artifact_id"]
        assert kit["job"]["title"] == "Data Engineer" and kit["job"]["company"] == "Northwind"
        assert kit["job"]["country"] == "GB" and kit["job"]["remote"] is False
        assert kit["application"] == {
            "id": app_id, "status": "considering", "follow_up_on": None, "submit_override": None,
        }
        file = kit["cv"]["file"]
        assert file["filename"] == "northwind-cv-v1.pdf" and file["mime"] == "application/pdf"
        assert file["downloads_left"] == 3 and file["size"] > 100
        assert file["url"].startswith(settings.SITE_BASE_URL + "/api/files/")
        assert kit["cv"]["seen"] is None and kit["cv"]["approved"] is None
        assert kit["autofill"] == "unset" and kit["hold"] is None
        assert kit["duplicate"]["flag"] == "" and kit["duplicate"]["same_job"] is None
        assert kit["settings"]["submit_preview"]["decision"] == "ask"
        assert any("missing" in line for line in kit["instructions"])


@pytest.mark.asyncio
async def test_no_cv_is_null_with_a_reason_and_another_jobs_cv_is_never_used(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        one, two = await _bring(client), await _bring(client, _AD_2)
        await _save(client, one, "cv", "FIRST JOB CV")
        await _save(client, one, "cover_letter", "FIRST JOB LETTER")
        kit = (await _kit(client, two)).json()
        assert kit["cv"] is None and kit["cv_none_reason"] == "no CV saved for this application"
        assert kit["cover_letter"] is None and "FIRST JOB" not in json.dumps(kit)
        await _save(client, two, "cv", "SECOND JOB CV")
        kit = (await _kit(client, two)).json()
        assert kit["cv"]["text"] == "SECOND JOB CV" and kit["cv"]["version"] == 1
        assert "FIRST JOB" not in json.dumps(kit)


@pytest.mark.asyncio
async def test_the_kit_returns_the_newest_cv_version(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        await _save(client, app_id, "cv", "old cv")
        newest = await _save(client, app_id, "cv", "new cv")
        kit = (await _kit(client, app_id)).json()
        assert kit["cv"]["text"] == "new cv" and kit["cv"]["version"] == 2
        assert kit["cv"]["artifact_id"] == newest["artifact_id"]
        assert kit["cv"]["file"]["filename"] == "northwind-cv-v2.pdf"


@pytest.mark.asyncio
async def test_answers_carry_source_and_saved_at_and_the_equality_label(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        edits = await _patch(
            client,
            _e("user_info.contact", {"email": "ada@example.com", "phone": "+44 7700 900123"}),
            _e("user_info.right_to_work", {"countries": [
                {"country": "GB", "work_authorization": "citizen", "needs_sponsorship": False}]}),
            _e("user_info.logistics", {"notice_period": "1 month"}),
            _e("user_info.languages", [{"language": "English", "level": "native"}]),
            _e("user_info.equality", {"gender": "prefer not to say", "veteran": ""}),
            _e("user_info.answers", [{
                "question": "Why Northwind?", "answer": "I like pipelines.", "approved": True,
                "recorded_at": "2026-10-01T09:00:00+00:00"}]),
            _e("preferences.salary_by_country", [
                {"country": "GB", "amount": 85000, "currency": "GBP", "period": "year"}]),
        )
        assert edits.status_code == 200, edits.text
        answers = (await _kit(client, app_id)).json()["answers"]
        contact = {i["key"]: i for i in answers["contact"]}
        assert contact["contact.email"]["value"] == "ada@example.com"
        assert contact["contact.email"]["source"] == "memory" and contact["contact.email"]["saved_at"]
        assert contact["contact.name"]["value"] == "Ada Lovelace" and contact["contact.name"]["source"] == "profile"
        assert contact["contact.name"]["saved_at"] is None
        assert contact["contact.linkedin_url"]["value"] == "https://www.linkedin.com/in/ada"
        rtw = {i["key"]: i for i in answers["right_to_work"]}
        assert rtw["right_to_work.GB.work_authorization"]["value"] == "citizen"
        assert rtw["right_to_work.GB.needs_sponsorship"]["value"] is False, "false is a real answer"
        assert answers["logistics"][0]["key"] == "logistics.notice_period" and answers["logistics"][0]["value"] == "1 month"
        assert answers["languages"][0]["key"] == "languages.English" and answers["languages"][0]["value"] == "native"
        assert [i["key"] for i in answers["equality"]] == ["equality.gender"], "empty veteran is absent"
        assert answers["equality"][0]["label"] == "equality / voluntary"
        assert answers["equality"][0]["value"] == "prefer not to say", "'prefer not to say' is a real value"
        assert answers["salary"][0]["key"] == "salary.GB"
        assert answers["salary"][0]["value"] == {"amount": 85000, "currency": "GBP", "period": "year"}
        approved = answers["approved_text"][0]
        assert (approved["key"], approved["value"], approved["source"]) == (
            "Why Northwind?", "I like pipelines.", "approved_text")
        assert approved["saved_at"] == "2026-10-01T09:00:00+00:00"


@pytest.mark.asyncio
async def test_missing_is_for_the_jobs_country_and_unknown_country_is_missing_too(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        gb, de, unknown = await _bring(client), await _bring(client, _AD_2), await _bring(client, _AD_NO_COUNTRY)
        assert (await _patch(
            client,
            _e("user_info.contact", {"email": "ada@example.com", "phone": "+44 7700 900123",
                                     "legal_first_name": "Ada", "legal_last_name": "Lovelace"}),
            _e("user_info.right_to_work", {"countries": [
                {"country": "GB", "work_authorization": "citizen", "needs_sponsorship": False}]}),
            _e("preferences.salary_by_country", [
                {"country": "GB", "amount": 85000, "currency": "GBP", "period": "year"}]),
        )).status_code == 200
        assert (await _kit(client, gb)).json()["missing"] == [], "everything for GB is known"
        german = {m["key"] for m in (await _kit(client, de)).json()["missing"]}
        assert german == {"right_to_work.DE.work_authorization", "right_to_work.DE.needs_sponsorship", "salary.DE"}
        no_country = {m["key"] for m in (await _kit(client, unknown)).json()["missing"]}
        assert "job_country" in no_country and "right_to_work.work_authorization" in no_country
        assert "salary" in no_country and "right_to_work.GB.work_authorization" not in no_country


@pytest.mark.asyncio
async def test_missing_lists_absent_contact_basics(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        keys = {m["key"] for m in (await _kit(client, app_id)).json()["missing"]}
        assert {"contact.email", "contact.phone", "contact.legal_first_name", "contact.legal_last_name"} <= keys
        assert all(m["why"] for m in (await _kit(client, app_id)).json()["missing"])


@pytest.mark.asyncio
async def test_a_kit_read_is_a_timeline_event_and_the_hash_is_stable(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        cv = await _save(client, app_id, "cv", CV_TEXT)
        first = (await _kit(client, app_id)).json()
        second = (await _kit(client, app_id)).json()
        assert first["kit"]["sha256"] == second["kit"]["sha256"], "same inputs, same hash"
        assert first["kit"]["id"] != second["kit"]["id"]
        assert first["cv"]["file"]["url"] != second["cv"]["file"]["url"], "a fresh link on every call"
        detail = (await client.get(f"/api/applications/{app_id}")).json()
        reads = [e for e in detail["events"] if e["event_type"] == "kit_read"]
        assert len(reads) == 2 and detail["status"] == "considering"
        assert reads[0]["id"] == first["kit"]["id"] and reads[0]["recorded_by"] == "web"
        assert reads[0]["payload"]["kit_sha256"] == first["kit"]["sha256"]
        assert reads[0]["payload"]["cv_artifact_id"] == cv["artifact_id"]
        assert "api/files" not in json.dumps(reads), "the token is never stored in history"
        await _save(client, app_id, "cv", CV_TEXT + "\nMore.")
        third = (await _kit(client, app_id)).json()
        assert third["kit"]["sha256"] != first["kit"]["sha256"], "a new CV is a new kit"


@pytest.mark.asyncio
async def test_the_hourly_cap_is_429_and_writes_no_event_or_link(
    authenticated_async_context, fixture_user_id, monkeypatch
):
    monkeypatch.setattr(settings, "KIT_READS_MAX_PER_HOUR", 2)
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        await _save(client, app_id, "cv", CV_TEXT)
        assert (await _kit(client, app_id)).status_code == 200
        assert (await _kit(client, app_id)).status_code == 200
        before = _sql("SELECT COUNT(*) FROM artifact_links WHERE user_id = ?", (fixture_user_id,))[0][0]
        assert (await _kit(client, app_id)).status_code == 429
        after = _sql("SELECT COUNT(*) FROM artifact_links WHERE user_id = ?", (fixture_user_id,))[0][0]
        assert before == after == 2
        detail = (await client.get(f"/api/applications/{app_id}")).json()
        assert [e["event_type"] for e in detail["events"]].count("kit_read") == 2


@pytest.mark.asyncio
async def test_another_users_kit_is_404_and_leaves_no_link_row(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        await _save(client, app_id, "cv", CV_TEXT)
    cookie = await _second_user_session_cookie("kit-intruder@example.com")
    from src.api.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", cookies={"job360_session": cookie}
    ) as intruder:
        assert (await _kit(intruder, app_id)).status_code == 404
        assert (await _kit(intruder, 999999)).status_code == 404
    assert _sql("SELECT COUNT(*) FROM artifact_links")[0][0] == 0
    assert _sql("SELECT COUNT(*) FROM application_events WHERE event_type = 'kit_read'")[0][0] == 0


@pytest.mark.asyncio
async def test_unauthenticated_kit_is_401(authenticated_async_context):
    async with authenticated_async_context():
        pass
    async with _anon_client() as anon:
        assert (await anon.get("/api/applications/1/kit")).status_code == 401


# ═══════════════════════════════════════════════════════════════════════════
# The public file link
# ═══════════════════════════════════════════════════════════════════════════


async def _link_setup(client: AsyncClient, user_id: str) -> tuple[int, dict[str, Any]]:
    _seed_profile(user_id)
    app_id = await _bring(client)
    await _save(client, app_id, "cv", CV_TEXT)
    kit = (await _kit(client, app_id)).json()
    return app_id, kit


@pytest.mark.asyncio
async def test_the_link_downloads_three_times_then_410(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id, kit = await _link_setup(client, fixture_user_id)
    token = _token_of(kit)
    async with _anon_client() as anon:
        for _ in range(3):
            resp = await anon.get(f"/api/files/{token}")
            assert resp.status_code == 200, resp.text
            assert resp.content[:5] == b"%PDF-"
            assert resp.headers["cache-control"] == "no-store"
            assert resp.headers["content-disposition"] == 'attachment; filename="northwind-cv-v1.pdf"'
            assert resp.headers["referrer-policy"] == "no-referrer" and "noindex" in resp.headers["x-robots-tag"]
            assert resp.headers["content-type"] == "application/pdf"
        gone = await anon.get(f"/api/files/{token}")
        assert gone.status_code == 410 and "get the kit again" in gone.json()["detail"]
    row = _sql("SELECT downloads_left FROM artifact_links WHERE user_id = ?", (fixture_user_id,))[0]
    assert row[0] == 0, "the counter never goes below zero"


@pytest.mark.asyncio
async def test_the_token_is_stored_only_as_a_hash(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _, kit = await _link_setup(client, fixture_user_id)
    token = _token_of(kit)
    rows = _sql("SELECT token_hash, created_by, fmt, version_no FROM artifact_links WHERE user_id = ?", (fixture_user_id,))
    assert len(rows) == 1 and rows[0][0] == hashlib.sha256(token.encode()).hexdigest() and token not in rows[0][0]
    assert (rows[0][1], rows[0][2], rows[0][3]) == ("web", "pdf", 1)
    assert len(token) >= 40, "32 random bytes, url-safe"


@pytest.mark.asyncio
async def test_an_expired_link_is_410_and_a_cv_edit_kills_the_link(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id, kit = await _link_setup(client, fixture_user_id)
        expired_token = _token_of(kit)
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        _sql("UPDATE artifact_links SET expires_at = ? WHERE user_id = ?", (past, fixture_user_id))
        edited = (await _kit(client, app_id)).json()
        fresh = _token_of(edited)
        await _save(client, app_id, "cv", CV_TEXT + "\nEdited.")
    async with _anon_client() as anon:
        resp = await anon.get(f"/api/files/{expired_token}")
        assert resp.status_code == 410 and "expired" in resp.json()["detail"]
        changed = await anon.get(f"/api/files/{fresh}")
        assert changed.status_code == 410 and "changed" in changed.json()["detail"]


@pytest.mark.asyncio
async def test_link_ttl_and_download_count_are_parameters(authenticated_async_context, fixture_user_id, monkeypatch):
    monkeypatch.setattr(settings, "KIT_LINK_TTL_MINUTES", 5)
    monkeypatch.setattr(settings, "KIT_LINK_MAX_DOWNLOADS", 1)
    async with authenticated_async_context() as client:
        _, kit = await _link_setup(client, fixture_user_id)
    assert kit["cv"]["file"]["downloads_left"] == 1
    expires = datetime.fromisoformat(kit["cv"]["file"]["expires_at"])
    assert timedelta(minutes=4) < expires - datetime.now(timezone.utc) <= timedelta(minutes=5)
    async with _anon_client() as anon:
        assert (await anon.get(f"/api/files/{_token_of(kit)}")).status_code == 200
        assert (await anon.get(f"/api/files/{_token_of(kit)}")).status_code == 410


@pytest.mark.asyncio
async def test_an_unknown_token_is_404_then_the_ip_is_locked_out_429(
    authenticated_async_context, monkeypatch
):
    from src.services.auth import rate_limit

    monkeypatch.setattr(settings, "FILE_BAD_TOKEN_MAX_PER_MIN", 3)
    rate_limit._FAILURES.clear()
    rate_limit._BUCKETS.clear()
    async with authenticated_async_context():
        pass
    async with _anon_client() as anon:
        for i in range(3):
            resp = await anon.get(f"/api/files/{'A' * 43}{i}")
            assert resp.status_code == 404
        locked = await anon.get(f"/api/files/{'B' * 43}")
        assert locked.status_code == 429
        assert (await anon.get("/api/files/short")).status_code == 429
    rate_limit._FAILURES.clear()
    rate_limit._BUCKETS.clear()


@pytest.mark.asyncio
async def test_a_locked_out_address_still_downloads_a_valid_link(
    authenticated_async_context, fixture_user_id, monkeypatch
):
    """Review fix: behind the Next rewrite every caller can share the proxy's
    address, so junk tokens must never lock out a REAL link (the lockout is
    consulted only for tokens that do not resolve), and junk never spends the
    download cap (keyed by the link's owner)."""
    from src.services.auth import rate_limit

    monkeypatch.setattr(settings, "FILE_BAD_TOKEN_MAX_PER_MIN", 2)
    monkeypatch.setattr(settings, "FILE_DOWNLOADS_MAX_PER_MIN", 2)
    rate_limit._FAILURES.clear()
    rate_limit._BUCKETS.clear()
    async with authenticated_async_context() as client:
        _, kit = await _link_setup(client, fixture_user_id)
    token = _token_of(kit)
    async with _anon_client() as anon:
        for i in range(5):
            assert (await anon.get(f"/api/files/{'Q' * 43}{i}")).status_code in (404, 429)
        assert (await anon.get(f"/api/files/{'Q' * 44}")).status_code == 429, "junk is locked out"
        assert (await anon.get(f"/api/files/{token}")).status_code == 200, "a real link is not"
    rate_limit._FAILURES.clear()
    rate_limit._BUCKETS.clear()


def test_sentry_transactions_are_scrubbed_too(monkeypatch):
    """Review fix: traces_sample_rate > 0 sends transaction events, which skip
    before_send. They carry request.url, so the scrubber must run there too."""
    import sentry_sdk

    from src.core import observability

    monkeypatch.setattr("src.core.settings.SENTRY_DSN", "https://abc@o1.ingest.sentry.io/1", raising=False)
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(sentry_sdk, "init", lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr(sentry_sdk, "set_tag", lambda *a, **k: None)
    observability.init_sentry()
    assert calls and calls[0]["before_send_transaction"] is observability._scrub_pii
    assert calls[0]["before_send"] is observability._scrub_pii


@pytest.mark.asyncio
async def test_a_wrong_shape_token_is_404(authenticated_async_context):
    from src.services.auth import rate_limit

    rate_limit._FAILURES.clear()
    rate_limit._BUCKETS.clear()
    async with authenticated_async_context():
        pass
    async with _anon_client() as anon:
        assert (await anon.get("/api/files/short")).status_code == 404
        assert (await anon.get("/api/files/" + "x" * 300)).status_code == 404
    rate_limit._FAILURES.clear()


@pytest.mark.asyncio
async def test_download_rate_limit_per_link_owner_is_429(authenticated_async_context, fixture_user_id, monkeypatch):
    from src.services.auth import rate_limit

    monkeypatch.setattr(settings, "FILE_DOWNLOADS_MAX_PER_MIN", 2)
    rate_limit._BUCKETS.clear()
    async with authenticated_async_context() as client:
        _, kit = await _link_setup(client, fixture_user_id)
    token = _token_of(kit)
    async with _anon_client() as anon:
        assert (await anon.get(f"/api/files/{token}")).status_code == 200
        assert (await anon.get(f"/api/files/{token}")).status_code == 200
        assert (await anon.get(f"/api/files/{token}")).status_code == 429
    rate_limit._BUCKETS.clear()


@pytest.mark.asyncio
async def test_parallel_downloads_never_spend_the_last_one_twice(authenticated_async_context, fixture_user_id, monkeypatch):
    import asyncio

    monkeypatch.setattr(settings, "KIT_LINK_MAX_DOWNLOADS", 1)
    async with authenticated_async_context() as client:
        _, kit = await _link_setup(client, fixture_user_id)
    token = _token_of(kit)
    async with _anon_client() as anon:
        results = await asyncio.gather(*[anon.get(f"/api/files/{token}") for _ in range(4)])
    assert sorted(r.status_code for r in results) == [200, 410, 410, 410]


@pytest.mark.asyncio
async def test_a_refused_real_token_never_reaches_the_error_log(
    authenticated_async_context, fixture_user_id, log_capture, monkeypatch
):
    """Review fix: errors.log_http_exception logs the PATH of every 4xx. A real
    link refused with 410 (spent) or 429 (rate limited, still live!) must not
    put its token in that line."""
    from src.services.auth import rate_limit

    monkeypatch.setattr(settings, "KIT_LINK_MAX_DOWNLOADS", 1)
    monkeypatch.setattr(settings, "FILE_DOWNLOADS_MAX_PER_MIN", 2)
    rate_limit._BUCKETS.clear()
    async with authenticated_async_context() as client:
        _, kit = await _link_setup(client, fixture_user_id)
        live = _token_of((await _kit(client, int(kit["application"]["id"]))).json())
    spent = _token_of(kit)
    async with _anon_client() as anon:
        assert (await anon.get(f"/api/files/{spent}")).status_code == 200
        assert (await anon.get(f"/api/files/{spent}")).status_code == 410
        assert (await anon.get(f"/api/files/{live}")).status_code == 429
    rate_limit._BUCKETS.clear()
    server = [r for r in log_capture.records if not str(r.get("name", "")).startswith("http")]
    errors = [r for r in server if r.get("event") == "http_error" and "/api/files/" in str(r.get("path"))]
    assert {r["status_code"] for r in errors} >= {410, 429}
    assert all(r["path"] == "/api/files/[redacted]" for r in errors)
    blob = json.dumps(server, default=str)
    assert spent not in blob and live not in blob


@pytest.mark.asyncio
async def test_the_token_never_reaches_a_log_the_access_line_or_sentry(
    authenticated_async_context, fixture_user_id, log_capture
):
    from src.core.observability import _scrub_pii

    async with authenticated_async_context() as client:
        app_id, kit = await _link_setup(client, fixture_user_id)
    token = _token_of(kit)
    async with _anon_client() as anon:
        assert (await anon.get(f"/api/files/{token}")).status_code == 200
        assert (await anon.get(f"/api/files/{'Z' * 43}")).status_code == 404
    # The test's own HTTP CLIENT (httpx) logs the URL it requested - that is the
    # caller's side, not Job360's. Everything the server writes is checked.
    log_capture.records = [r for r in log_capture.records if not str(r.get("name", "")).startswith("http")]
    blob = json.dumps(log_capture.records, default=str)
    leaked = [r for r in log_capture.records if token in json.dumps(r, default=str)]
    assert not leaked, [(r.get("name"), r.get("msg"), r.get("event")) for r in leaked]
    assert hashlib.sha256(token.encode()).hexdigest() not in blob, "nor its hash"
    assert CV_TEXT.splitlines()[1] not in blob, "nor CV text"
    access = [r for r in log_capture.records if r.get("event") == "http_request" and "/api/files/" in str(r.get("path"))]
    assert access and all(r["path"] == "/api/files/[redacted]" for r in access)
    assert all("[redacted]" in r["msg"] % r["args"] if r.get("args") else "[redacted]" in str(r["msg"]) for r in access)
    scrubbed = _scrub_pii(
        {"transaction": f"/api/files/{token}", "request": {"url": f"http://test/api/files/{token}", "headers": {}}}, None
    )
    assert token not in json.dumps(scrubbed)
    created = _events(log_capture, "file_link_created")
    assert created and created[0]["user_id"] == fixture_user_id and created[0]["application_id"] == app_id
    downloaded = _events(log_capture, "file_download")
    assert downloaded[0]["user_id"] == fixture_user_id and downloaded[0]["status"] == 200
    refused = _events(log_capture, "file_download_refused")
    assert refused and refused[0]["status"] == 404 and refused[0]["reason"] == "unknown_token"
    read = _events(log_capture, "kit_read")[0]
    assert read["user_id"] == fixture_user_id and read["application_id"] == app_id and read["surface"] == "web"
    assert read["missing_count"] >= 1 and read["duplicate"] == "" and read["hold"] is False


@pytest.mark.asyncio
async def test_a_link_download_is_not_a_cv_seen(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id, kit = await _link_setup(client, fixture_user_id)
    async with _anon_client() as anon:
        assert (await anon.get(f"/api/files/{_token_of(kit)}")).status_code == 200
    async with authenticated_async_context() as client:
        assert (await _controls(client, app_id))["cv"]["seen"] is None
        detail = (await client.get(f"/api/applications/{app_id}")).json()
        assert "cv_seen" not in [e["event_type"] for e in detail["events"]]


# ═══════════════════════════════════════════════════════════════════════════
# Seen / approved: the human in the loop
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_web_download_writes_cv_seen_once_per_version(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        cv = await _save(client, app_id, "cv", CV_TEXT)
        url = f"/api/applications/{app_id}/artifacts/{cv['artifact_id']}/download"
        assert (await client.post(url, params={"fmt": "pdf"})).status_code == 200
        assert (await client.post(url, params={"fmt": "docx"})).status_code == 200
        detail = (await client.get(f"/api/applications/{app_id}")).json()
        seen = [e for e in detail["events"] if e["event_type"] == "cv_seen"]
        assert len(seen) == 1, "one mark per version"
        assert seen[0]["payload"] == {
            "artifact_id": cv["artifact_id"], "sha256": sha(CV_TEXT), "where": "web", "version": 1, "by": "web",
        }
        state = await _controls(client, app_id)
        assert state["cv"]["seen"]["where"] == "web" and state["cv"]["seen"]["by"] == "web" and state["cv"]["seen"]["at"]
        # a new version shows unseen again
        await _save(client, app_id, "cv", CV_TEXT + "\nMore.")
        assert (await _controls(client, app_id))["cv"]["seen"] is None
        kit = (await _kit(client, app_id)).json()
        assert kit["cv"]["seen"] is None and kit["cv"]["version"] == 2


@pytest.mark.asyncio
async def test_a_bearer_download_writes_no_cv_seen(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        cv = await _save(client, app_id, "cv", CV_TEXT)
        token = await _mint_token(client)
    async with _bearer_client(token) as agent:
        url = f"/api/applications/{app_id}/artifacts/{cv['artifact_id']}/download"
        assert (await agent.post(url)).status_code == 200
    async with authenticated_async_context() as client:
        assert (await _controls(client, app_id))["cv"]["seen"] is None


@pytest.mark.asyncio
async def test_the_seen_button_route_is_session_only_and_writes_once(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        assert (await client.post(f"/api/applications/{app_id}/cv-seen", json={})).status_code == 409, "no CV yet"
        cv = await _save(client, app_id, "cv", CV_TEXT)
        token = await _mint_token(client)
        first = await client.post(f"/api/applications/{app_id}/cv-seen", json={})
        assert first.status_code == 201, first.text
        assert first.json()["cv"]["seen"]["where"] == "web" and first.json()["cv"]["version"] == 1
        again = await client.post(f"/api/applications/{app_id}/cv-seen", json={"artifact_id": cv["artifact_id"]})
        assert again.status_code == 201
        detail = (await client.get(f"/api/applications/{app_id}")).json()
        assert [e["event_type"] for e in detail["events"]].count("cv_seen") == 1
        assert (await client.post(f"/api/applications/{app_id}/cv-seen", json={"artifact_id": 999999})).status_code == 404
        assert (await client.post(f"/api/applications/{app_id}/cv-seen", json={"who": "me"})).status_code == 422
    async with _bearer_client(token) as agent:
        assert (await agent.post(f"/api/applications/{app_id}/cv-seen", json={})).status_code in (401, 403)
    async with _anon_client() as anon:
        assert (await anon.post(f"/api/applications/{app_id}/cv-seen", json={})).status_code == 401


@pytest.mark.asyncio
async def test_event_rules_who_may_write_cv_seen(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        cv = await _save(client, app_id, "cv", CV_TEXT)
        token = await _mint_token(client)
        good_web = {"artifact_id": cv["artifact_id"], "sha256": sha(CV_TEXT), "where": "web"}
        assert (await _event(client, app_id, "cv_seen", {**good_web, "where": "chat"})).status_code == 403
        assert (await _event(client, app_id, "cv_seen", {**good_web, "where": "moon"})).status_code == 422
        assert (await _event(client, app_id, "cv_seen", {**good_web, "x": 1})).status_code == 422
        assert (await _event(client, app_id, "cv_seen", good_web)).status_code == 201
    async with _bearer_client(token) as agent:
        chat = {"artifact_id": cv["artifact_id"], "sha256": sha(CV_TEXT), "where": "chat"}
        refused = await _event(agent, app_id, "cv_seen", {**chat, "where": "web"})
        assert refused.status_code == 403 and "website" in refused.json()["detail"]
        stale = await _event(agent, app_id, "cv_seen", {**chat, "sha256": sha("something else")})
        assert stale.status_code == 409 and "this CV changed" in stale.json()["detail"]
        ok = await _event(agent, app_id, "cv_seen", chat)
        assert ok.status_code == 201, ok.text
    async with authenticated_async_context() as client:
        detail = (await client.get(f"/api/applications/{app_id}")).json()
        mine = [e for e in detail["events"] if e["event_type"] == "cv_seen"][-1]
        assert mine["payload"]["where"] == "chat" and mine["payload"]["by"] == "token:claude-code"
        assert mine["payload"]["version"] == 1 and mine["recorded_by"] == "token:claude-code"
        seen = (await _controls(client, app_id))["cv"]["seen"]
        assert seen["where"] == "chat" and seen["by"] == "token:claude-code"


@pytest.mark.asyncio
async def test_a_stale_cv_is_409_even_with_the_right_old_hash(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        old = await _save(client, app_id, "cv", CV_TEXT)
        await _save(client, app_id, "cv", CV_TEXT + "\nNew.")
        stale = await _event(client, app_id, "cv_seen", {
            "artifact_id": old["artifact_id"], "sha256": sha(CV_TEXT), "where": "web"})
        assert stale.status_code == 409 and "show the new version" in stale.json()["detail"]
        assert (await _controls(client, app_id))["cv"]["seen"] is None


@pytest.mark.asyncio
async def test_send_this_one_records_seen_and_approved_and_an_edit_voids_it(
    authenticated_async_context, fixture_user_id, log_capture
):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        assert (await client.post(f"/api/applications/{app_id}/send/approve")).status_code == 409, "no CV yet"
        await _save(client, app_id, "cv", CV_TEXT)
        assert (await _patch(client, _e("assistant_settings.submit_mode", "confirm"))).status_code == 200
        assert (await _check(client, app_id))["reason"] == "submit_mode_confirm"
        resp = await client.post(f"/api/applications/{app_id}/send/approve")
        assert resp.status_code == 201, resp.text
        state = resp.json()
        assert state["cv"]["seen"]["where"] == "web" and state["cv"]["approved"]["where"] == "web"
        verdict = await _check(client, app_id)
        assert (verdict["decision"], verdict["reason"]) == ("submit", "user_approved")
        detail = (await client.get(f"/api/applications/{app_id}")).json()
        types = [e["event_type"] for e in detail["events"]]
        assert types.count("cv_seen") == 1 and types.count("submit_approved") == 1
        # the CV is edited: the yes no longer matches
        await _save(client, app_id, "cv", CV_TEXT + "\nEdited after the yes.")
        after = await _check(client, app_id)
        assert (after["decision"], after["reason"]) == ("ask", "submit_mode_confirm")
        assert (await _controls(client, app_id))["cv"]["approved"] is None
        # Indeed also clears on a fresh yes (owner answer 2)
        assert (await client.post(f"/api/applications/{app_id}/send/approve")).status_code == 201
        indeed = await _check(client, app_id, "https://uk.indeed.com/viewjob?jk=1")
        assert (indeed["decision"], indeed["reason"]) == ("submit", "user_approved")
    approved_logs = _events(log_capture, "submit_approved")
    assert approved_logs and approved_logs[0]["user_id"] == fixture_user_id and approved_logs[0]["where"] == "web"
    assert CV_TEXT.splitlines()[1] not in json.dumps(log_capture.records, default=str)


@pytest.mark.asyncio
async def test_a_yes_never_overrides_pause_or_already_applied(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        await _save(client, app_id, "cv", CV_TEXT)
        assert (await client.post(f"/api/applications/{app_id}/send/approve")).status_code == 201
        assert (await _check(client, app_id))["reason"] == "user_approved"
        assert (await _patch(client, _e("assistant_settings.paused_until", "until_resumed"))).status_code == 200
        paused = await _check(client, app_id)
        assert (paused["decision"], paused["reason"]) == ("stop", "paused")
        assert (await _patch(client, _e("assistant_settings.paused_until", ""))).status_code == 200
        assert (await client.post(f"/api/applications/{app_id}/receipt", json={"channel": "company_site"})).status_code == 201
        done = await _check(client, app_id)
        assert (done["decision"], done["reason"]) == ("stop", "already_applied")


@pytest.mark.asyncio
async def test_dont_send_stops_until_a_later_send(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        await _save(client, app_id, "cv", CV_TEXT)
        assert (await client.post(f"/api/applications/{app_id}/send/approve")).status_code == 201
        declined = await client.post(f"/api/applications/{app_id}/send/decline")
        assert declined.status_code == 201 and declined.json()["declined"]["where"] == "web"
        verdict = await _check(client, app_id)
        assert (verdict["decision"], verdict["reason"]) == ("stop", "user_declined")
        assert (await _kit(client, app_id)).json()["settings"]["submit_preview"]["reason"] == "user_declined"
        assert (await client.post(f"/api/applications/{app_id}/send/approve")).status_code == 201
        assert (await _controls(client, app_id))["declined"] is None
        assert (await _check(client, app_id))["reason"] == "user_approved"


@pytest.mark.asyncio
async def test_chat_decline_and_chat_approval_rules(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        cv = await _save(client, app_id, "cv", CV_TEXT)
        token = await _mint_token(client)
    chat = {"artifact_id": cv["artifact_id"], "sha256": sha(CV_TEXT), "where": "chat"}
    async with _bearer_client(token) as agent:
        assert (await _event(agent, app_id, "submit_declined", {"where": "web"})).status_code == 403
        assert (await _event(agent, app_id, "submit_declined", {"where": "chat"})).status_code == 201
        assert (await _check(agent, app_id))["reason"] == "user_declined"
        assert (await _event(agent, app_id, "submit_approved", {**chat, "where": "web"})).status_code == 403
        assert (await _event(agent, app_id, "submit_approved", chat)).status_code == 201
        verdict = await _check(agent, app_id)
        assert (verdict["decision"], verdict["reason"]) == ("submit", "user_approved"), "a later yes clears a decline"
        assert (await _event(agent, app_id, "submit_approved", {**chat, "sha256": "0" * 64})).status_code == 409


@pytest.mark.asyncio
async def test_no_cv_approval_is_artifact_null_and_a_new_cv_voids_it(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        token = await _mint_token(client)
    async with _bearer_client(token) as agent:
        yes = await _event(agent, app_id, "submit_approved", {"artifact_id": None, "sha256": "", "where": "chat"})
        assert yes.status_code == 201, yes.text
        assert (await _check(agent, app_id))["reason"] == "user_approved"
    async with authenticated_async_context() as client:
        await _save(client, app_id, "cv", CV_TEXT)
        assert (await _check(client, app_id))["reason"] == "submit_mode_confirm"


# ═══════════════════════════════════════════════════════════════════════════
# Gate through the API: auto + unseen, duplicates, autofill, hold
# ═══════════════════════════════════════════════════════════════════════════


async def _auto_on(client: AsyncClient, user_id: str) -> None:
    assert (await _patch(client, _e("assistant_settings.submit_mode", "auto_when_sure"))).status_code == 200


@pytest.mark.asyncio
async def test_auto_with_an_unseen_cv_asks_then_seen_goes_to_practice_then_submit(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        one, two = await _bring(client), await _bring(client, _AD_2)
        await _save(client, one, "cv", CV_TEXT)
        await _save(client, two, "cv", CV_TEXT)
        await _auto_on(client, fixture_user_id)
        assert (await _check(client, one))["reason"] == "cv_not_seen"
        assert (await client.post(f"/api/applications/{one}/cv-seen", json={})).status_code == 201
        assert (await _check(client, one))["reason"] == "practice_run"
        assert (await client.post(f"/api/applications/{one}/receipt", json={"channel": "company_site"})).status_code == 201
        assert (await _check(client, two))["reason"] == "cv_not_seen"
        assert (await client.post(f"/api/applications/{two}/cv-seen", json={})).status_code == 201
        assert (await _check(client, two))["decision"] == "submit"
        await _save(client, two, "cv", CV_TEXT + "\nChanged.")
        assert (await _check(client, two))["reason"] == "cv_not_seen", "a CV edit is unseen again"


@pytest.mark.asyncio
async def test_duplicate_same_job_stops_in_auto_asks_in_confirm_and_the_button_clears_it(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        await _save(client, app_id, "cv", CV_TEXT)
        assert (await client.post(f"/api/applications/{app_id}/receipt", json={"channel": "company_site"})).status_code == 201
        # a second brought copy of the same ad URL under another title: same job by URL
        twin = await _bring(client, {**_AD, "title": "Data Engineer (Contract)"})
        await _save(client, twin, "cv", CV_TEXT)
        assert (await client.post(f"/api/applications/{twin}/send/approve")).status_code == 201
        dup = (await _controls(client, twin))["duplicate"]
        assert dup["flag"] == "same_job" and dup["same_job"]["application_id"] == app_id and dup["cleared"] is None
        assert (await _check(client, twin))["reason"] == "user_approved", "a yes clears the confirm-mode ask"
        await _auto_on(client, fixture_user_id)
        stop = await _check(client, twin)
        assert (stop["decision"], stop["reason"]) == ("stop", "duplicate_job"), "auto + duplicate stops, yes or not"
        cleared = await client.post(f"/api/applications/{twin}/duplicate/clear")
        assert cleared.status_code == 201 and cleared.json()["duplicate"]["cleared"]["where"] == "web"
        assert (await _check(client, twin))["decision"] in ("submit", "ask")
        assert (await _check(client, twin))["reason"] != "duplicate_job"


@pytest.mark.asyncio
async def test_duplicate_clear_is_web_only(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        token = await _mint_token(client)
    async with _bearer_client(token) as agent:
        assert (await _event(agent, app_id, "duplicate_cleared", {"where": "web"})).status_code == 403
        assert (await agent.post(f"/api/applications/{app_id}/duplicate/clear")).status_code in (401, 403)
    async with authenticated_async_context() as client:
        assert (await _event(client, app_id, "duplicate_cleared", {"where": "chat"})).status_code == 403
        assert (await _event(client, app_id, "duplicate_cleared", {"where": "web"})).status_code == 201


@pytest.mark.asyncio
async def test_autofill_events_assistants_may_only_deny(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        await _save(client, app_id, "cv", CV_TEXT)
        token = await _mint_token(client)
        assert (await _controls(client, app_id))["autofill"]["mode"] == "unset"
        assert (await _kit(client, app_id)).json()["autofill"] == "unset"
    async with _bearer_client(token) as agent:
        assert (await _event(agent, app_id, "autofill_set", {"mode": "allow"})).status_code == 403
        assert (await _event(agent, app_id, "autofill_set", {"mode": "maybe"})).status_code == 422
        assert (await _event(agent, app_id, "autofill_set", {"mode": "deny", "x": 1})).status_code == 422
        assert (await agent.post(f"/api/applications/{app_id}/autofill", json={"mode": "deny"})).status_code in (401, 403)
        assert (await _event(agent, app_id, "autofill_set", {"mode": "deny"})).status_code == 201
        assert (await _kit(agent, app_id)).json()["autofill"] == "deny"
    async with authenticated_async_context() as client:
        state = (await _controls(client, app_id))["autofill"]
        assert state["mode"] == "deny" and state["by"] == "token:claude-code" and state["at"]
        assert state["where"] == "chat", "an assistant's deny must never read as a website click"
        allowed = await client.post(f"/api/applications/{app_id}/autofill", json={"mode": "allow"})
        assert allowed.status_code == 201 and allowed.json()["autofill"]["mode"] == "allow"
        assert allowed.json()["autofill"]["by"] == "web" and allowed.json()["autofill"]["where"] == "web"
        assert (await _kit(client, app_id)).json()["autofill"] == "allow"
        assert (await client.post(f"/api/applications/{app_id}/autofill", json={"mode": "x"})).status_code == 422
        # autofill never changes the gate
        assert (await _check(client, app_id))["reason"] == "submit_mode_confirm"


@pytest.mark.asyncio
async def test_hold_warns_when_another_actor_read_the_kit_recently(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        await _save(client, app_id, "cv", CV_TEXT)
        token = await _mint_token(client)
        assert (await _kit(client, app_id)).json()["hold"] is None, "my own read is not a hold"
        assert (await _kit(client, app_id)).json()["hold"] is None
    async with _bearer_client(token) as agent:
        hold = (await _kit(agent, app_id)).json()["hold"]
        assert hold["held_by"] == "web" and hold["since"] and hold["until"] > hold["since"]
        until = datetime.fromisoformat(hold["until"]) - datetime.fromisoformat(hold["since"])
        assert until == timedelta(minutes=settings.KIT_HOLD_MINUTES)
        assert (await _kit(agent, app_id)).json()["hold"] is not None
        released = await _event(agent, app_id, "hold_released", {"reason": "done"})
        assert released.status_code == 201
        assert (await _event(agent, app_id, "hold_released", {"reason": "bored"})).status_code == 422
    async with authenticated_async_context() as client:
        assert (await _kit(client, app_id)).json()["hold"] is None, "the assistant released it"
    async with _bearer_client(token) as agent:
        assert (await _kit(agent, app_id)).json()["hold"]["held_by"] == "web", "the user's read is a hold again"
        assert (await _event(agent, app_id, "hold_released", {"reason": "stopped"})).status_code == 201
        assert (await _kit(agent, app_id)).json()["hold"] is None


@pytest.mark.asyncio
async def test_a_hold_ends_after_applied_and_after_the_window(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        await _save(client, app_id, "cv", CV_TEXT)
        token = await _mint_token(client)
        await _kit(client, app_id)
        assert (await client.post(f"/api/applications/{app_id}/receipt", json={"channel": "company_site"})).status_code == 201
    async with _bearer_client(token) as agent:
        assert (await _kit(agent, app_id)).json()["hold"] is None, "applied ends the hold"
    async with authenticated_async_context() as client:
        assert (await _kit(client, app_id)).json()["hold"] is not None
        old = (datetime.now(timezone.utc) - timedelta(minutes=settings.KIT_HOLD_MINUTES + 1)).isoformat()
        _sql("UPDATE application_events SET recorded_at = ? WHERE event_type = 'kit_read'", (old,))
    async with _bearer_client(token) as agent:
        assert (await _kit(agent, app_id)).json()["hold"] is None, "30 minutes later nobody is on it"


# ═══════════════════════════════════════════════════════════════════════════
# Account sites + form_filled
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_account_site_seed_lookalike_and_learned(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        workday = await _bring(client, {**_AD, "apply_url": "https://acme.wd5.myworkdayjobs.com/en-US/jobs/job/1"})
        look = await _bring(client, {**_AD_2, "apply_url": "https://notmyworkdayjobs.com/jobs/2"})
        other = await _bring(client, {**_AD_NO_COUNTRY, "apply_url": "https://careers.bigco.example/jobs/3"})
        site = (await _kit(client, workday)).json()["account_site"]
        assert site["host"] == "acme.wd5.myworkdayjobs.com" and site["likely_needs_account"] is True
        assert site["known_account"] is None
        assert (await _kit(client, look)).json()["account_site"]["likely_needs_account"] is False
        assert (await _kit(client, other)).json()["account_site"]["likely_needs_account"] is False
        learned = await _event(client, other, "account_needed", {"host": "https://careers.bigco.example/signin"})
        assert learned.status_code == 201, learned.text
        # learned for the USER: every later application on that host is flagged
        later = await _bring(client, {**_AD, "title": "Other role", "apply_url": "https://careers.bigco.example/jobs/99"})
        assert (await _kit(client, later)).json()["account_site"]["likely_needs_account"] is True
        assert (await _kit(client, other)).json()["account_site"]["likely_needs_account"] is True


@pytest.mark.asyncio
async def test_site_account_memory_has_no_room_for_a_password_and_spans_applications(
    authenticated_async_context, fixture_user_id, log_capture
):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        one = await _bring(client, {**_AD, "apply_url": "https://acme.myworkdayjobs.com/jobs/1"})
        two = await _bring(client, {**_AD_2, "apply_url": "https://acme.myworkdayjobs.com/jobs/2"})
        bad = await _event(client, one, "site_account", {"host": "acme.myworkdayjobs.com", "password": "hunter2"})
        assert bad.status_code == 422 and "hunter2" not in bad.text
        assert (await _event(client, one, "site_account", {"host": "acme.myworkdayjobs.com", "user": "ada"})).status_code == 422
        assert (await _event(client, one, "site_account", {"password": "hunter2"})).status_code == 422
        assert (await _event(client, one, "site_account", {"host": "has space"})).status_code == 422
        assert (await _event(client, one, "account_needed", {"host": "x.example", "password": "p"})).status_code == 422
        assert (await _event(client, one, "site_account", {"host": "ACME.myworkdayjobs.com"})).status_code == 201
        site = (await _kit(client, two)).json()["account_site"]
        assert site["known_account"]["recorded_by"] == "web" and site["known_account"]["recorded_at"]
        detail = (await client.get(f"/api/applications/{one}")).json()
        stored = [e for e in detail["events"] if e["event_type"] == "site_account"][0]
        assert stored["payload"] == {"host": "acme.myworkdayjobs.com", "by": "web"}
        assert "hunter2" not in json.dumps(log_capture.records, default=str)
    recorded = _events(log_capture, "site_account")
    assert recorded and recorded[0]["host"] == "acme.myworkdayjobs.com" and recorded[0]["user_id"] == fixture_user_id


@pytest.mark.asyncio
async def test_form_filled_stores_the_host_not_the_url(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        url = "https://careers.northwind.example/apply/7?token=SECRET-IN-QUERY#frag"
        ok = await _event(client, app_id, "form_filled", {"form_url": url, "fields_count": 12})
        assert ok.status_code == 201, ok.text
        for bad in ({"form_url": url, "fields_count": 501}, {"form_url": url, "fields_count": -1},
                    {"form_url": url, "fields_count": True}, {"form_url": "", "fields_count": 3},
                    {"form_url": url}, {"form_url": url, "fields_count": 3, "values": {"a": 1}}):
            assert (await _event(client, app_id, "form_filled", bad)).status_code == 422, bad
        detail = (await client.get(f"/api/applications/{app_id}")).json()
        stored = [e for e in detail["events"] if e["event_type"] == "form_filled"]
        assert len(stored) == 1 and stored[0]["payload"] == {
            "host": "careers.northwind.example", "fields_count": 12, "by": "web"}
        assert "SECRET-IN-QUERY" not in json.dumps(detail)
        assert detail["status"] == "considering"


@pytest.mark.asyncio
async def test_kit_read_cannot_be_forged(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        forged = await _event(client, app_id, "kit_read", {"kit_sha256": "x"})
        assert forged.status_code == 422 and "Job360 itself" in forged.json()["detail"]


# ═══════════════════════════════════════════════════════════════════════════
# Receipts: possible_duplicate + the kit used
# ═══════════════════════════════════════════════════════════════════════════


async def _apply(client: AsyncClient, app_id: int, **body: Any) -> dict[str, Any]:
    resp = await client.post(f"/api/applications/{app_id}/receipt", json={"channel": "company_site", **body})
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_receipt_copies_the_kit_and_flags_nothing_when_clean(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        await _save(client, app_id, "cv", CV_TEXT)
        kit = (await _kit(client, app_id)).json()
        receipt = await _apply(client, app_id)
        assert receipt["possible_duplicate"] == ""
        full = (await client.get(f"/api/receipts/{receipt['receipt_id']}")).json()
        assert full["possible_duplicate"] == ""
        assert full["kit_event_id"] == kit["kit"]["id"] and full["kit_sha256"] == kit["kit"]["sha256"]


@pytest.mark.asyncio
async def test_receipt_without_a_kit_reads_null_and_empty(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        receipt = await _apply(client, app_id)
        full = (await client.get(f"/api/receipts/{receipt['receipt_id']}")).json()
        assert (full["possible_duplicate"], full["kit_event_id"], full["kit_sha256"]) == ("", None, "")


@pytest.mark.asyncio
async def test_same_company_within_30_days_is_flagged_and_31_days_is_not(
    authenticated_async_context, fixture_user_id, log_capture
):
    now = datetime.now(timezone.utc)
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        first = await _bring(client)
        second = await _bring(client, {**_AD, "title": "Analytics Engineer", "apply_url": "https://northwind.example/careers/8"})
        third = await _bring(client, {**_AD, "title": "BI Engineer", "apply_url": "https://northwind.example/careers/9"})
        await _apply(client, first, applied_at=(now - timedelta(days=29)).isoformat())
        flagged = await _apply(client, second)
        assert flagged["possible_duplicate"] == "same_company"
        detail = (await client.get(f"/api/applications/{second}")).json()
        applied = [e for e in detail["events"] if e["event_type"] == "applied"][0]
        assert applied["payload"]["possible_duplicate"] == "same_company", "the application's own record"
        assert detail["status"] == "applied", "a flag never blocks"
        kit = (await _kit(client, third)).json()
        assert kit["duplicate"]["flag"] == "same_company" and kit["duplicate"]["same_company_30d"] == 2
    flags = _events(log_capture, "possible_duplicate")
    assert flags and flags[0]["flag"] == "same_company" and flags[0]["user_id"] == fixture_user_id


@pytest.mark.asyncio
async def test_a_company_application_31_days_ago_is_not_a_duplicate(authenticated_async_context, fixture_user_id):
    now = datetime.now(timezone.utc)
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        first = await _bring(client)
        second = await _bring(client, {**_AD, "title": "Analytics Engineer", "apply_url": "https://northwind.example/careers/8"})
        await _apply(client, first, applied_at=(now - timedelta(days=31)).isoformat())
        receipt = await _apply(client, second)
        assert receipt["possible_duplicate"] == ""
        assert (await _kit(client, second)).json()["duplicate"] == {
            "same_job": {"application_id": second, "status": "applied", "applied_at": receipt["sent_at"]},
            "same_company_30d": 0, "flag": "same_job", "cleared": None,
        }


@pytest.mark.asyncio
async def test_a_second_receipt_for_the_same_application_is_same_job_and_never_4xx(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        first = await _apply(client, app_id)
        assert first["possible_duplicate"] == ""
        second = await _apply(client, app_id)
        assert second["possible_duplicate"] == "same_job"
        kit = (await _kit(client, app_id)).json()
        assert kit["duplicate"]["flag"] == "same_job" and kit["duplicate"]["same_job"]["application_id"] == app_id
        assert kit["duplicate"]["same_job"]["status"] == "applied"


@pytest.mark.asyncio
async def test_another_users_applications_never_count_as_duplicates(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        mine = await _bring(client)
        await _apply(client, mine)
    cookie = await _second_user_session_cookie("kit-other@example.com")
    from src.api.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", cookies={"job360_session": cookie}
    ) as other:
        theirs = await _bring(other)
        dup = (await _kit(other, theirs)).json()["duplicate"]
        assert dup["flag"] == "" and dup["same_job"] is None and dup["same_company_30d"] == 0


# ═══════════════════════════════════════════════════════════════════════════
# MCP: the same function, instructions, parity
# ═══════════════════════════════════════════════════════════════════════════


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


def _payload(result) -> dict:
    assert not result.is_error, result.content[0].text
    return json.loads(result.content[0].text)


@pytest.mark.asyncio
async def test_mcp_get_application_kit_is_the_same_kit(authenticated_async_context, fixture_user_id, log_capture):
    from src.api.mcp_server import mcp_runtime

    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        await _save(client, app_id, "cv", CV_TEXT)
        token = await _mint_token(client)
    async with mcp_runtime():
        async with _mcp_client(token) as mcp:
            names = {t.name for t in (await mcp.list_tools()).tools}
            assert "get_application_kit" in names
            kit = _payload(await mcp.call_tool("get_application_kit", {"application_id": app_id}))
            assert kit["cv"]["text"] == CV_TEXT and kit["cv"]["sha256"] == sha(CV_TEXT)
            assert kit["cv"]["file"]["url"].startswith(settings.SITE_BASE_URL + "/api/files/")
            other = await mcp.call_tool("get_application_kit", {"application_id": 999999})
            assert other.is_error and "404" in other.content[0].text
            # chat events through record_event inherit the route rules
            denied = await mcp.call_tool("record_event", {
                "event_type": "cv_seen", "application_id": app_id,
                "payload": {"artifact_id": kit["cv"]["artifact_id"], "sha256": kit["cv"]["sha256"], "where": "web"}})
            assert denied.is_error and "403" in denied.content[0].text
            ok = _payload(await mcp.call_tool("record_event", {
                "event_type": "cv_seen", "application_id": app_id,
                "payload": {"artifact_id": kit["cv"]["artifact_id"], "sha256": kit["cv"]["sha256"], "where": "chat"}}))
            assert ok["event_type"] == "cv_seen"
            again = _payload(await mcp.call_tool("get_application_kit", {"application_id": app_id}))
            assert again["cv"]["seen"]["where"] == "chat" and again["cv"]["seen"]["by"] == "token:claude-code"
            no_buttons = [n for n in names if any(w in n for w in ("approve", "decline", "autofill", "duplicate", "seen"))]
            assert not no_buttons, "the website's four buttons have no MCP twin"
    ev = _events(log_capture, "kit_read")
    assert [e["surface"] for e in ev] == ["mcp", "mcp"]


def test_instructions_docstrings_and_recipe_carry_the_kit_rules():
    from src.api.mcp_server import INSTRUCTIONS, build_server
    from src.api.routes.recipes import load_recipe

    for word in ("APPLY KIT", "get_application_kit", "missing", "file.url", "cv_seen", "submit_approved",
                 "form_filled", "site_account", "account_needed", "autofill"):
        assert word in INSTRUCTIONS, word
    tools = {t.name: t for t in build_server()._tool_manager.list_tools()}
    assert "cv_not_seen" in tools["check_submit"].description and "user_declined" in tools["check_submit"].description
    assert "autofill_set" in tools["record_event"].description and "account_needed" in tools["record_event"].description
    apply = " ".join(load_recipe("apply").text.lower().split())
    for word in ("get_application_kit", "duplicate.flag", "form_filled", "submit_approved", "cv_seen",
                 "cv_not_seen", "site_account", "never ask for or type a password", "`autofill`", "download `file.url`"):
        assert word.lower() in apply, word


def test_every_kit_event_type_is_a_note_event_that_never_moves_status():
    from src.services.applications.status import status_for_event

    for event_type in kit_service.KIT_EVENT_TYPES:
        assert event_type in settings.APPLICATION_NOTE_EVENT_TYPES
        assert status_for_event(event_type) is None


def test_the_artifact_links_table_is_erased_with_the_account_and_not_exported():
    from src.repositories.database import JobDatabase

    assert "artifact_links" in JobDatabase._PER_USER_TABLES
    assert "artifact_links" not in JobDatabase._EXPORT_TABLES


def test_artifact_links_is_the_only_new_table_update_and_history_stays_append_only():
    """The counter slot is the one UPDATE; events / artifacts are never rewritten (M3)."""
    import re
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "src"
    bad: list[str] = []
    for path in src.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(r"(UPDATE|DELETE FROM)\s+(application_events|application_artifacts|application_receipts)\b", text):
            bad.append(f"{path.name}:{m.group(0)}")
    assert not bad, bad
    updates = [
        p.name for p in src.rglob("*.py")
        if re.search(r"UPDATE\s+artifact_links", p.read_text(encoding="utf-8"))
    ]
    assert updates == ["files.py"], "only the download route spends the counter"
