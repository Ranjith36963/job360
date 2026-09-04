"""The application spine (docs/plans/2026-09-04-application-spine/spec.md).

Frozen per spec.md's own list (§Frozen tests) — once written these are not
edited to make the implementation pass; a test believed wrong is left as-is
and called out in the build report instead.

An Application is born at ``POST /jobs/bring`` (R1), carries a durable job
snapshot (R2), and its whole history is one append-only event log (R3) whose
last status event is cached on ``applications.status`` (R4). Artifacts
(R5), the fit verdict (R6), receipts (R8), ``whats_new`` (R9) and
``export_history`` (R10) all read/write through that log. New modules
(``src.services.applications.*``, ``src.api.routes.applications``) do not
exist yet — every test below is expected to fail with an ImportError or
AttributeError until slice 2 is built; that is the intended RED state.
"""
from __future__ import annotations

import logging
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

_AD = {
    "title": "Data Engineer",
    "company": "Northwind",
    "location": "Remote",
    "apply_url": "https://northwind.example/careers/7",
    "description": "Build the pipelines. Python, dbt, Snowflake. Fully remote.",
}

_AD_2 = {
    "title": "Platform Engineer",
    "company": "Southwind",
    "location": "London",
    "apply_url": "https://southwind.example/careers/3",
    "description": "Own the platform. Kubernetes, Terraform, Go.",
}


# ── Shared helpers ────────────────────────────────────────────────────────────


async def _bring(client: AsyncClient, ad: dict[str, Any] = _AD) -> dict[str, Any]:
    resp = await client.post("/api/jobs/bring", json=ad)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _get_application(client: AsyncClient, application_id: int, **params: Any):
    return await client.get(f"/api/applications/{application_id}", params=params)


async def _save_artifact(
    client: AsyncClient, application_id: int, kind: str, text: str, **extra: Any
):
    body = {"kind": kind, "text": text, **extra}
    return await client.post(f"/api/applications/{application_id}/artifacts", json=body)


async def _save_fit(client: AsyncClient, application_id: int, **body: Any):
    return await client.put(f"/api/applications/{application_id}/fit", json=body)


async def _record_event(client: AsyncClient, application_id: int, event_type: str, **extra: Any):
    body = {"event_type": event_type, **extra}
    return await client.post(f"/api/applications/{application_id}/events", json=body)


async def _record_receipt(client: AsyncClient, application_id: int, **body: Any):
    return await client.post(f"/api/applications/{application_id}/receipt", json=body)


async def _mint_token(client: AsyncClient, name: str = "cli") -> str:
    resp = await client.post("/api/tokens", json={"name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()["token"]


def _bearer_client(token: str) -> AsyncClient:
    from src.api.main import app

    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    )


async def _second_user_session_cookie(email: str = "second@example.com") -> str:
    """Register + verify + log in a SECOND user against the same (already
    DB_PATH-patched) app the fixture user lives in, and return their session
    cookie. Mirrors what ``authenticated_async_context`` does for the first
    user (conftest.py) — a second call to that fixture within one test would
    just re-use the SAME registered user, since the DB/tmp_path is bound once
    at fixture setup, not per invocation.
    """
    from fastapi.testclient import TestClient

    from src.api.main import app
    from src.core import settings
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
    cookie = sync_client.cookies.get("job360_session")
    assert cookie, "failed to capture second user's session cookie"
    sync_client.close()
    return cookie


def _session_client(cookie: str) -> AsyncClient:
    from src.api.main import app

    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", cookies={"job360_session": cookie}
    )


class _CapturingAuditHandler(logging.Handler):
    """Collects every ``job360.audit`` LogRecord's ``extra`` fields (S9)."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[dict[str, Any]] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(dict(record.__dict__))


@pytest.fixture
def audit_capture():
    logger = logging.getLogger("job360.audit")
    handler = _CapturingAuditHandler()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    yield handler
    logger.removeHandler(handler)


# ═══════════════════════════════════════════════════════════════════════════
# R1/R2 — birth at bring_job, the durable snapshot
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_bring_creates_one_application_considering(authenticated_async_context):
    async with authenticated_async_context() as client:
        body = await _bring(client)
        assert isinstance(body["application_id"], int)
        assert body["status"] == "considering"
        # constraint 4 — legacy shape untouched (also pinned standalone below).
        assert "job" in body and "existing" in body and "scored" in body

        detail = await _get_application(client, body["application_id"])
        assert detail.status_code == 200, detail.text
        events = detail.json()["events"]
        brought = [e for e in events if e["event_type"] == "brought"]
        assert len(brought) == 1


@pytest.mark.asyncio
async def test_bringing_twice_reuses_the_application(authenticated_async_context):
    async with authenticated_async_context() as client:
        first = await _bring(client)
        second = await _bring(client)
        assert second["application_id"] == first["application_id"]
        assert second["existing"] is True

        detail = await _get_application(client, first["application_id"])
        brought = [e for e in detail.json()["events"] if e["event_type"] == "brought"]
        assert len(brought) == 1, "bringing the same job twice must not append a second brought event"


@pytest.mark.asyncio
async def test_the_job_snapshot_survives_a_purge(authenticated_async_context):
    from src.api import dependencies as api_deps

    async with authenticated_async_context() as client:
        body = await _bring(client)
        job_id = body["job"]["id"]

        db = await api_deps.get_db()
        await db._conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        await db._conn.commit()

        detail = await _get_application(client, body["application_id"])
        assert detail.status_code == 200, detail.text
        job = detail.json()["job"]
        # R2 snapshot column names, copied at birth — never joined off `jobs`.
        assert job["job_title"] == _AD["title"]
        assert job["job_company"] == _AD["company"]
        assert job["catalog_present"] is False


@pytest.mark.asyncio
async def test_purge_spares_a_brought_job(authenticated_async_context):
    """Hard rule #3 amendment (R2): ``purge_old_jobs`` must not delete a
    ``user_brought`` row even when it is old, while an equally-old scraped row
    is still deleted on schedule."""
    from datetime import datetime, timedelta, timezone

    from src.api import dependencies as api_deps
    from src.core.settings import USER_BROUGHT_SOURCE
    from src.models import Job

    old = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()

    async with authenticated_async_context():
        pass
    db = await api_deps.get_db()

    scraped = Job(
        title="Old Scraped Role", company="Acme Scrape", apply_url="https://acme.test/scraped",
        source="reed", date_found=old, last_seen_at=old, first_seen_at=old,
    )
    brought = Job(
        title="Old Brought Role", company="Acme Brought", apply_url="https://acme.test/brought",
        source=USER_BROUGHT_SOURCE, date_found=old, last_seen_at=old, first_seen_at=old,
    )
    await db.insert_job(scraped)
    await db.insert_job(brought)
    await db.commit()
    scraped_id = await db.get_job_id_by_key(scraped.normalized_key())
    brought_id = await db.get_job_id_by_key(brought.normalized_key())
    assert scraped_id and brought_id

    await db.purge_old_jobs(days=30)

    assert await db.get_job_by_id(scraped_id) is None, "an old SCRAPED row must still be purged"
    assert await db.get_job_by_id(brought_id) is not None, "an old USER_BROUGHT row must survive the purge"


@pytest.mark.asyncio
async def test_legacy_bring_response_shape_is_unchanged(authenticated_async_context):
    """Constraint 4 — job/existing/scored keep working for any existing caller."""
    async with authenticated_async_context() as client:
        body = await _bring(client)
        assert set(("job", "existing", "scored")).issubset(body)
        assert isinstance(body["job"]["id"], int)
        assert body["existing"] is False
        assert isinstance(body["scored"], bool)


# ═══════════════════════════════════════════════════════════════════════════
# R5 — artifacts are versioned forever
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_two_cv_versions_both_readable(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = (await _bring(client))["application_id"]

        v1 = await _save_artifact(client, app_id, "cv", "CV VERSION ONE")
        assert v1.status_code == 201, v1.text
        assert v1.json()["version_no"] == 1
        v2 = await _save_artifact(client, app_id, "cv", "CV VERSION TWO")
        assert v2.status_code == 201, v2.text
        assert v2.json()["version_no"] == 2

        got_v1 = await client.get(f"/api/applications/{app_id}/artifacts/{v1.json()['artifact_id']}")
        got_v2 = await client.get(f"/api/applications/{app_id}/artifacts/{v2.json()['artifact_id']}")
        assert got_v1.status_code == 200 and got_v2.status_code == 200
        assert got_v1.json()["text"] == "CV VERSION ONE", "v1's text must survive v2 being written byte-identical"
        assert got_v2.json()["text"] == "CV VERSION TWO"


@pytest.mark.asyncio
async def test_version_numbers_are_per_kind(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = (await _bring(client))["application_id"]

        cv1 = await _save_artifact(client, app_id, "cv", "cv draft")
        cv2 = await _save_artifact(client, app_id, "cv", "cv edited")
        cl1 = await _save_artifact(client, app_id, "cover_letter", "dear hiring manager")

        assert cv1.json()["version_no"] == 1
        assert cv2.json()["version_no"] == 2
        assert cl1.json()["version_no"] == 1, "cover_letter's own version series must not inherit cv's counter"


@pytest.mark.asyncio
async def test_artifact_over_the_cap_is_422(authenticated_async_context):
    from src.core import settings

    async with authenticated_async_context() as client:
        app_id = (await _bring(client))["application_id"]
        too_long = "x" * (settings.APPLICATION_ARTIFACT_MAX_CHARS + 1)
        resp = await _save_artifact(client, app_id, "cv", too_long)
        assert resp.status_code == 422, resp.text
        assert "APPLICATION_ARTIFACT_MAX_CHARS" in resp.text


@pytest.mark.asyncio
async def test_made_by_and_recorded_by_come_from_the_credential(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = (await _bring(client))["application_id"]

        via_session = await _save_artifact(client, app_id, "cv", "session-written cv")
        assert via_session.status_code == 201, via_session.text
        assert via_session.json()["made_by"] == "web"

        token = await _mint_token(client, name="my-agent")

    async with _bearer_client(token) as agent:
        via_token = await _save_artifact(agent, app_id, "cv", "token-written cv")
        assert via_token.status_code == 201, via_token.text
        assert via_token.json()["made_by"] == "token:my-agent"

        forged = await _record_event(agent, app_id, "note", recorded_by="someone-else")
        assert forged.status_code == 422, forged.text


# ═══════════════════════════════════════════════════════════════════════════
# R3/R4/R7 — the event log, status cache, and vocabulary
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_unknown_event_type_is_422_listing_the_allowed_types(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = (await _bring(client))["application_id"]
        resp = await _record_event(client, app_id, "definitely_not_a_real_type")
        assert resp.status_code == 422, resp.text
        assert "brought" in resp.text  # the allowed list is named in the error


@pytest.mark.asyncio
async def test_status_event_moves_status_and_note_does_not(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = (await _bring(client))["application_id"]

        applied = await _record_event(client, app_id, "applied")
        assert applied.status_code == 201, applied.text
        assert applied.json()["status"] == "applied"

        noted = await _record_event(client, app_id, "note", detail="left a good impression")
        assert noted.status_code == 201, noted.text
        assert noted.json()["status"] == "applied", "a non-status event must not move the cached status"


@pytest.mark.asyncio
async def test_status_is_rebuildable_from_the_event_log(authenticated_async_context):
    from src.services.applications.status import replay_status

    from src.api import dependencies as api_deps

    async with authenticated_async_context() as client:
        app_id = (await _bring(client))["application_id"]
        await _record_event(client, app_id, "applied")
        await _record_event(client, app_id, "replied")
        interview = await _record_event(client, app_id, "interview_requested")
        assert interview.json()["status"] == "interview_requested"

        detail = await _get_application(client, app_id)
        events = detail.json()["events"]

    db = await api_deps.get_db()
    cur = await db._conn.execute("SELECT status FROM applications WHERE id = ?", (app_id,))
    row = await cur.fetchone()
    stored_status = row["status"]

    # #21 — assert a NON-DEFAULT value, not merely that replay ran.
    assert stored_status == "interview_requested"
    assert replay_status(events) == stored_status


@pytest.mark.asyncio
async def test_a_correcting_event_supersedes_and_is_skipped_by_the_recompute(authenticated_async_context):

    async with authenticated_async_context() as client:
        app_id = (await _bring(client))["application_id"]
        await _record_event(client, app_id, "applied")
        mistake = await _record_event(client, app_id, "rejected")
        assert mistake.json()["status"] == "rejected"
        mistake_id = mistake.json()["event_id"]

        # The agent notices the "rejected" was a mistake and retires it with a
        # NOTE (non-status) event, rather than another status event — so the
        # only way the correct final status comes back is if the recompute
        # actually SKIPS the superseded event.
        correction = await _record_event(
            client, app_id, "note", detail="mis-recorded, employer never replied", corrects_event_id=mistake_id
        )
        assert correction.status_code == 201, correction.text
        assert correction.json()["status"] == "applied", "recompute must skip the superseded event"

        detail = await _get_application(client, app_id)
    events = detail.json()["events"]
    target = next(e for e in events if e["id"] == mistake_id)
    assert target["superseded"] is True


@pytest.mark.asyncio
async def test_events_are_append_only(authenticated_async_context):
    import re
    from pathlib import Path

    src = Path(__file__).resolve().parent.parent / "src"
    offenders = []
    for py in src.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        if re.search(r"(UPDATE|DELETE\s+FROM)\s+application_events", text, re.IGNORECASE):
            offenders.append(str(py))
        if re.search(r"(UPDATE|DELETE\s+FROM)\s+application_artifacts", text, re.IGNORECASE):
            offenders.append(str(py))
    assert offenders == [], f"application_events/application_artifacts must be append-only: {offenders}"

    from src.api.main import app

    for route in app.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", set()) or set()
        if path.startswith("/api/applications") and ("/events" in path or "/artifacts" in path):
            assert not (methods & {"PATCH", "PUT", "DELETE"}), f"{path} exposes {methods}"


@pytest.mark.asyncio
async def test_backdated_occurred_at_is_accepted_and_ordered(authenticated_async_context):
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    long_ago = (now - timedelta(days=10)).isoformat()
    recent = (now - timedelta(hours=1)).isoformat()

    async with authenticated_async_context() as client:
        app_id = (await _bring(client))["application_id"]
        # Recorded FIRST but happened MORE RECENTLY than the backdated one below.
        recent_evt = await _record_event(client, app_id, "note", detail="today's note", occurred_at=recent)
        assert recent_evt.status_code == 201, recent_evt.text
        backdated = await _record_event(
            client, app_id, "note", detail="a note about last week", occurred_at=long_ago
        )
        assert backdated.status_code == 201, backdated.text

        detail = await _get_application(client, app_id)
    events = [e for e in detail.json()["events"] if e["event_type"] == "note"]
    occurred = [e["occurred_at"] for e in events]
    assert occurred == sorted(occurred), "the timeline must be ordered by occurred_at, backdated events included"


@pytest.mark.asyncio
async def test_future_occurred_at_is_422(authenticated_async_context):
    from datetime import datetime, timedelta, timezone

    from src.core import settings

    too_far = (
        datetime.now(timezone.utc)
        + timedelta(seconds=settings.APPLICATION_EVENT_MAX_FUTURE_SECONDS + 3600)
    ).isoformat()

    async with authenticated_async_context() as client:
        app_id = (await _bring(client))["application_id"]
        resp = await _record_event(client, app_id, "note", occurred_at=too_far)
        assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_event_types_match_vision_doc(authenticated_async_context):
    """docs/product/VISION.md:66-71 is the one place the event vocabulary is
    written in prose; this pins code and doc to the same set so they cannot
    drift apart silently."""
    from src.core import settings

    vision_types = {
        "brought", "fit_judged", "artifact_saved", "contact_added", "outreach_sent",
        "applied", "replied", "interview_requested", "interview_scheduled",
        "interview_done", "offer", "rejected", "withdrawn", "ghosted", "note", "lesson",
    }
    code_types = set(settings.APPLICATION_STATUS_EVENT_TYPES) | set(settings.APPLICATION_NOTE_EVENT_TYPES)
    assert code_types == vision_types


# ═══════════════════════════════════════════════════════════════════════════
# S2/S4 — cross-user isolation
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_a_foreign_application_id_is_404_on_every_route(authenticated_async_context):
    async with authenticated_async_context() as owner:
        owned = await _bring(owner)
        app_id = owned["application_id"]
        artifact = await _save_artifact(owner, app_id, "cv", "owner's cv")
        artifact_id = artifact.json()["artifact_id"]

    cookie = await _second_user_session_cookie("intruder-a@example.com")
    async with _session_client(cookie) as intruder:
        assert (await _get_application(intruder, app_id)).status_code == 404
        assert (await _save_artifact(intruder, app_id, "cv", "not yours")).status_code == 404
        assert (await _save_fit(intruder, app_id, score=50, verdict="x")).status_code == 404
        assert (await _record_event(intruder, app_id, "note", detail="x")).status_code == 404
        assert (await _record_receipt(intruder, app_id)).status_code == 404
        assert (await intruder.get(f"/api/applications/{app_id}/artifacts/{artifact_id}")).status_code == 404


@pytest.mark.asyncio
async def test_a_foreign_artifact_id_on_a_receipt_is_404(authenticated_async_context):
    async with authenticated_async_context() as owner:
        owned = await _bring(owner)
        artifact = await _save_artifact(owner, owned["application_id"], "cv", "owner's cv")
        foreign_artifact_id = artifact.json()["artifact_id"]

    cookie = await _second_user_session_cookie("intruder-b@example.com")
    async with _session_client(cookie) as intruder:
        own = await _bring(intruder, _AD_2)
        resp = await _record_receipt(intruder, own["application_id"], cv_artifact_id=foreign_artifact_id)
        assert resp.status_code == 404, resp.text


# ═══════════════════════════════════════════════════════════════════════════
# R6 — the fit verdict is stored, never computed
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_save_fit_stores_the_verdict_and_keeps_both_judgements(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = (await _bring(client))["application_id"]

        first = await _save_fit(client, app_id, score=40, verdict="weak match", gaps=["no Go"], reasoning="early read")
        assert first.status_code == 200, first.text
        second = await _save_fit(client, app_id, score=80, verdict="strong match", gaps=[], reasoning="after reading the JD closely")
        assert second.status_code == 200, second.text
        assert second.json()["fit"]["score"] == 80

        detail = await _get_application(client, app_id)
    body = detail.json()
    assert body["fit"]["score"] == 80, "the slot holds only the CURRENT answer"
    judged = [e for e in body["events"] if e["event_type"] == "fit_judged"]
    assert len(judged) == 2, "the log keeps BOTH judgements"


@pytest.mark.asyncio
async def test_save_fit_computes_nothing(authenticated_async_context, monkeypatch):
    import sys

    class _Explosive:
        def __getattr__(self, name: str) -> Any:
            raise AssertionError(f"save_fit touched skill_matcher.{name} — it must never score")

    async with authenticated_async_context() as client:
        app_id = (await _bring(client))["application_id"]  # bring is allowed to score; poison AFTER
        monkeypatch.setitem(sys.modules, "src.services.skill_matcher", _Explosive())

        resp = await _save_fit(client, app_id, score=70, verdict="looks fine", reasoning="agent's own read")
        assert resp.status_code == 200, resp.text


# ═══════════════════════════════════════════════════════════════════════════
# R8 — record_application is the rich receipt
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_record_application_freezes_the_named_version(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = (await _bring(client))["application_id"]
        await _save_artifact(client, app_id, "cv", "cv v1")
        v2 = await _save_artifact(client, app_id, "cv", "cv v2 — the one actually sent")

        resp = await _record_receipt(
            client, app_id, cv_artifact_id=v2.json()["artifact_id"], channel="company site", confirmation="REF-123"
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["cv_artifact_id"] == v2.json()["artifact_id"]
        assert body["cv_version_no"] == 2

        detail = await _get_application(client, app_id)
    payload = detail.json()
    assert any(e["event_type"] == "applied" for e in payload["events"])
    assert payload["receipts"][0]["cv_artifact_id"] == v2.json()["artifact_id"]


@pytest.mark.asyncio
async def test_legacy_receipts_route_writes_through(authenticated_async_context):
    async with authenticated_async_context() as client:
        bring_resp = await client.post("/api/jobs/bring", json=_AD)
        assert bring_resp.status_code == 200, bring_resp.text
        job_id = bring_resp.json()["job"]["id"]
        application_id = bring_resp.json()["application_id"]

        legacy = await client.post(f"/api/receipts/{job_id}", json={"channel": "email"})
        assert legacy.status_code == 201, legacy.text

        detail = await _get_application(client, application_id)
    payload = detail.json()
    assert any(e["event_type"] == "applied" for e in payload["events"])
    assert payload["receipts"], "the legacy route must fill application_id and be visible from the spine"


# ═══════════════════════════════════════════════════════════════════════════
# R9 — whats_new
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_whats_new_pages_on_recorded_at_not_occurred_at(authenticated_async_context):
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    a_moment_ago = (now - timedelta(seconds=5)).isoformat()
    long_ago_in_the_world = (now - timedelta(days=30)).isoformat()

    async with authenticated_async_context() as client:
        app_id = (await _bring(client))["application_id"]
        # since is set to "a moment ago" — recorded NOW, but occurred 30 days
        # ago in the world. A cursor on occurred_at would wrongly skip this.
        resp = await _record_event(
            client, app_id, "note", detail="found an old email thread", occurred_at=long_ago_in_the_world
        )
        assert resp.status_code == 201, resp.text

        whats_new = await client.get("/api/whats-new", params={"since": a_moment_ago})
    assert whats_new.status_code == 200, whats_new.text
    body = whats_new.json()
    assert any(e["detail"] == "found an old email thread" for e in body["events"])


@pytest.mark.asyncio
async def test_whats_new_truncates_explicitly(authenticated_async_context, monkeypatch):
    from src.core import settings

    monkeypatch.setattr(settings, "WHATS_NEW_MAX_EVENTS", 3)

    async with authenticated_async_context() as client:
        app_id = (await _bring(client))["application_id"]
        for i in range(5):
            r = await _record_event(client, app_id, "note", detail=f"note {i}")
            assert r.status_code == 201, r.text

        resp = await client.get("/api/whats-new")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["truncated"] is True
    assert len(body["events"]) <= 3
    assert body["next_since"], "a usable cursor must be returned when truncated"


# ═══════════════════════════════════════════════════════════════════════════
# R10 — export_history
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_export_history_bounds_and_truncates(authenticated_async_context, monkeypatch):
    from src.core import settings

    monkeypatch.setattr(settings, "EXPORT_HISTORY_MAX_APPLICATIONS", 2)

    async with authenticated_async_context() as client:
        for i in range(4):
            await _bring(client, {**_AD, "apply_url": f"https://northwind.example/careers/{i}", "title": f"Role {i}"})

        resp = await client.get("/api/applications/export")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["truncated"] is True
    assert len(body["applications"]) <= 2
    assert body.get("next_since")


@pytest.mark.asyncio
async def test_export_history_is_rate_limited_per_user(authenticated_async_context, monkeypatch):
    from src.core import settings

    monkeypatch.setattr(settings, "EXPORT_HISTORY_MAX_PER_HOUR", 2)

    async with authenticated_async_context() as client:
        await _bring(client)
        assert (await client.get("/api/applications/export")).status_code == 200
        assert (await client.get("/api/applications/export")).status_code == 200
        limited = await client.get("/api/applications/export")
        assert limited.status_code == 429, limited.text

    cookie = await _second_user_session_cookie("export-other-user@example.com")
    async with _session_client(cookie) as other:
        await _bring(other, _AD_2)
        unaffected = await other.get("/api/applications/export")
        assert unaffected.status_code == 200, "the limit is per USER, not per IP/process"


# ═══════════════════════════════════════════════════════════════════════════
# R11 — get_application / list_applications
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_get_application_omits_artifact_text_by_default(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = (await _bring(client))["application_id"]
        await _save_artifact(client, app_id, "cv", "the actual cv text")

        default = await _get_application(client, app_id)
        assert default.status_code == 200, default.text
        for artifact in default.json()["artifacts"]:
            assert artifact.get("text") is None, "artifact text must be OFF by default"

        with_text = await _get_application(client, app_id, with_artifact_text="true")
    assert with_text.status_code == 200, with_text.text
    texts = [a.get("text") for a in with_text.json()["artifacts"]]
    assert "the actual cv text" in texts


# ═══════════════════════════════════════════════════════════════════════════
# R15 — tailoring stays, as a web fallback that also versions
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_tailor_generate_also_writes_an_artifact_version(authenticated_async_context, monkeypatch):
    from src.api.routes import tailor as tailor_route
    from src.services.tailoring.generator import GeneratedDoc

    async def _fake_generate(*, doc_kind: str, **kwargs: Any) -> GeneratedDoc:
        return GeneratedDoc(doc_kind=doc_kind, document=f"a generated {doc_kind}", model="test-model", flagged_terms=[])

    monkeypatch.setattr(tailor_route, "generate_document", _fake_generate)
    monkeypatch.setattr(
        tailor_route, "_load_cv_text", lambda user_id: "Real CV text with enough content to generate from."
    )

    async with authenticated_async_context() as client:
        bring_resp = await client.post("/api/jobs/bring", json=_AD)
        job_id = bring_resp.json()["job"]["id"]
        application_id = bring_resp.json()["application_id"]

        gen = await client.post(f"/api/tailor/{job_id}/generate")
        assert gen.status_code == 200, gen.text

        detail = await _get_application(client, application_id)
    artifacts = detail.json()["artifacts"]
    assert any(a["made_by"] == "web:tailor" for a in artifacts)


@pytest.mark.asyncio
async def test_tailor_save_edit_writes_a_human_version(authenticated_async_context, monkeypatch):
    from src.api.routes import tailor as tailor_route
    from src.services.tailoring.generator import GeneratedDoc

    async def _fake_generate(*, doc_kind: str, **kwargs: Any) -> GeneratedDoc:
        return GeneratedDoc(doc_kind=doc_kind, document=f"a generated {doc_kind}", model="test-model", flagged_terms=[])

    monkeypatch.setattr(tailor_route, "generate_document", _fake_generate)
    monkeypatch.setattr(
        tailor_route, "_load_cv_text", lambda user_id: "Real CV text with enough content to generate from."
    )

    async with authenticated_async_context() as client:
        bring_resp = await client.post("/api/jobs/bring", json=_AD)
        job_id = bring_resp.json()["job"]["id"]
        application_id = bring_resp.json()["application_id"]
        await client.post(f"/api/tailor/{job_id}/generate")

        edited = await client.patch(f"/api/tailor/{job_id}/cv", json={"text": "my hand-edited cv"})
        assert edited.status_code == 200, edited.text

        detail = await _get_application(client, application_id)
    artifacts = detail.json()["artifacts"]
    assert any(a["made_by"] == "human" for a in artifacts)


# ═══════════════════════════════════════════════════════════════════════════
# S9 — audit log never carries a body
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_audit_log_never_carries_a_body(authenticated_async_context, audit_capture):
    async with authenticated_async_context() as client:
        app_id = (await _bring(client))["application_id"]
        await _save_artifact(client, app_id, "cv", "SECRET_ARTIFACT_MARKER_TEXT")
        await _record_event(client, app_id, "note", detail="SECRET_DETAIL_MARKER", payload={"x": "SECRET_PAYLOAD_MARKER"})
        await _save_fit(client, app_id, score=10, verdict="v", reasoning="SECRET_REASONING_MARKER")

    markers = ("SECRET_ARTIFACT_MARKER_TEXT", "SECRET_DETAIL_MARKER", "SECRET_PAYLOAD_MARKER", "SECRET_REASONING_MARKER")
    for record in audit_capture.records:
        blob = " ".join(f"{k}={v!r}" for k, v in record.items())
        for marker in markers:
            assert marker not in blob, f"audit log leaked a body field: {marker} in {blob[:300]}"
