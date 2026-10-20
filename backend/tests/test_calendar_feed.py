"""The private calendar feed (migration 0058, ``routes/calendar.py``).

``GET /api/calendar/{token}.ics`` has no login and serves private data, so the
tests pin the credential rules (hash only, lockout, owner rate limit, headers,
no token in logs) next to the content rules (what is in the feed, what never is).
Helpers are copied, never imported from another test module.
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.core import settings
from src.services.applications import calendar_ics

_AD = {
    "title": "Data Engineer", "company": "Northwind", "location": "London", "country": "GB", "remote": False,
    "apply_url": "https://northwind.example/careers/7", "description": "Build the pipelines.",
}


def _iso(days: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).replace(microsecond=0).isoformat()


def _anon_client() -> AsyncClient:
    from src.api.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _bring(client: AsyncClient, ad: dict[str, Any] = _AD) -> int:
    resp = await client.post("/api/jobs/bring", json=ad)
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _event(client: AsyncClient, app_id: int, event_type: str, **extra: Any):
    resp = await client.post(f"/api/applications/{app_id}/events", json={"event_type": event_type, **extra})
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _make_feed(client: AsyncClient) -> str:
    resp = await client.post("/api/calendar-feed")
    assert resp.status_code == 201, resp.text
    return str(resp.json()["url"])


def _path(url: str) -> str:
    return url.replace(settings.SITE_BASE_URL, "")


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


def _reset_limits() -> None:
    from src.services.auth import rate_limit

    rate_limit._FAILURES.clear()
    rate_limit._BUCKETS.clear()


@pytest.fixture(autouse=True)
def _isolate_limits():
    """Per-IP lockout / rate-limit state is process-global: never let it leak in or out."""
    _reset_limits()
    try:
        yield
    finally:
        _reset_limits()


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


def _unfold(text: str) -> str:
    return text.replace("\r\n ", "")


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[dict[str, Any]] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(dict(record.__dict__))
        self.records[-1]["_message"] = record.getMessage()


@pytest.fixture
def log_capture():
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


# ── content ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_feed_lists_scheduled_interview_and_follow_up(authenticated_async_context):
    _reset_limits()
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        when = _iso(5)
        ev = await _event(client, app_id, "interview_scheduled", scheduled_at=when,
                          detail="private note: ask about salary", )
        follow = (datetime.now(timezone.utc) + timedelta(days=9)).date()
        await _event(client, app_id, "note", follow_up_on=follow.isoformat())
        url = await _make_feed(client)
    assert url.startswith(settings.SITE_BASE_URL + "/api/calendar/") and url.endswith(".ics")
    async with _anon_client() as anon:
        resp = await anon.get(_path(url))
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/calendar")
    body = _unfold(resp.text)
    assert body.startswith("BEGIN:VCALENDAR\r\n") and body.endswith("END:VCALENDAR\r\n")
    assert "SUMMARY:Interview: Data Engineer – Northwind" in body
    start = datetime.fromisoformat(when).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    assert f"DTSTART:{start}" in body
    assert f"UID:app-{app_id}-ev-{ev['event_id']}@job360.uk" in body
    assert f"UID:app-{app_id}-followup@job360.uk" in body
    assert f"DTSTART;VALUE=DATE:{follow.strftime('%Y%m%d')}" in body
    assert f"URL:{settings.SITE_BASE_URL}/applications/{app_id}" in body
    assert "salary" not in body, "notes never leave in the feed"


@pytest.mark.asyncio
async def test_feed_skips_superseded_and_closed(authenticated_async_context):
    _reset_limits()
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        first = await _event(client, app_id, "interview_scheduled", scheduled_at=_iso(3))
        second = await _event(client, app_id, "interview_scheduled", scheduled_at=_iso(6),
                              corrects_event_id=first["event_id"])
        closed_id = await _bring(client, {**_AD, "title": "Closed Role", "company": "Gone Ltd",
                                          "apply_url": "https://gone.example/c/1"})
        await _event(client, closed_id, "interview_scheduled", scheduled_at=_iso(4))
        await _event(client, closed_id, "rejected")
        url = await _make_feed(client)
    async with _anon_client() as anon:
        body = _unfold((await anon.get(_path(url))).text)
    assert f"app-{app_id}-ev-{second['event_id']}@" in body
    assert f"app-{app_id}-ev-{first['event_id']}@" not in body, "a superseded event never shows"
    assert "Closed Role" not in body and f"app-{closed_id}-" not in body, "closed applications are skipped"


@pytest.mark.asyncio
async def test_feed_never_leaks_other_users(authenticated_async_context):
    _reset_limits()
    async with authenticated_async_context() as client:
        mine = await _bring(client)
        await _event(client, mine, "interview_scheduled", scheduled_at=_iso(2))
        my_url = await _make_feed(client)
        cookie = await _second_user_session_cookie()
    from src.api.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", cookies={"job360_session": cookie}
    ) as other:
        theirs = await _bring(other, {**_AD, "title": "Secret Role", "company": "Other Corp",
                                      "apply_url": "https://other.example/c/1"})
        await _event(other, theirs, "interview_scheduled", scheduled_at=_iso(2))
        their_url = await _make_feed(other)
        foreign = await other.get(f"/api/applications/{mine}/calendar.ics")
        assert foreign.status_code == 404
    async with _anon_client() as anon:
        mine_body = _unfold((await anon.get(_path(my_url))).text)
        their_body = _unfold((await anon.get(_path(their_url))).text)
    assert "Northwind" in mine_body and "Other Corp" not in mine_body and "Secret Role" not in mine_body
    assert "Other Corp" in their_body and "Northwind" not in their_body


# ── the credential ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unknown_token_404_then_lockout(authenticated_async_context, monkeypatch):
    monkeypatch.setattr(settings, "FILE_BAD_TOKEN_MAX_PER_MIN", 3)
    _reset_limits()
    async with authenticated_async_context() as client:
        url = await _make_feed(client)
    async with _anon_client() as anon:
        for i in range(3):
            resp = await anon.get(f"/api/calendar/{'A' * 43}{i}.ics")
            assert resp.status_code == 404
            assert resp.headers["cache-control"] == "no-store"
        assert (await anon.get(f"/api/calendar/{'B' * 43}.ics")).status_code == 429, "junk is locked out"
        assert (await anon.get("/api/calendar/short.ics")).status_code == 429
        assert (await anon.get(_path(url))).status_code == 200, "a real feed is never locked out"
    _reset_limits()


@pytest.mark.asyncio
async def test_revoked_or_rotated_token_404(authenticated_async_context):
    _reset_limits()
    async with authenticated_async_context() as client:
        old = await _make_feed(client)
        new = await _make_feed(client)
        assert new != old
        status = await client.get("/api/calendar-feed")
        assert status.json()["exists"] is True and status.json()["created_at"]
        async with _anon_client() as anon:
            assert (await anon.get(_path(old))).status_code == 404, "a rotated URL dies"
            assert (await anon.get(_path(new))).status_code == 200
            assert (await client.delete("/api/calendar-feed")).status_code == 204
            assert (await anon.get(_path(new))).status_code == 404, "a revoked URL dies"
        assert (await client.get("/api/calendar-feed")).json() == {"exists": False, "created_at": None}
    _reset_limits()


@pytest.mark.asyncio
async def test_feed_stores_only_the_hash(authenticated_async_context, fixture_user_id):
    _reset_limits()
    async with authenticated_async_context() as client:
        url = await _make_feed(client)
    token = url.rsplit("/", 1)[1].removesuffix(".ics")
    rows = _sql("SELECT token_hash FROM calendar_feeds WHERE user_id = ?", (fixture_user_id,))
    assert len(rows) == 1
    assert rows[0][0] == hashlib.sha256(token.encode()).hexdigest() and token not in rows[0][0]


@pytest.mark.asyncio
async def test_feed_rate_limited_per_owner(authenticated_async_context, monkeypatch):
    monkeypatch.setattr(settings, "CALENDAR_FEED_MAX_PER_HOUR", 2)
    _reset_limits()
    async with authenticated_async_context() as client:
        url = await _make_feed(client)
    async with _anon_client() as anon:
        assert (await anon.get(_path(url))).status_code == 200
        assert (await anon.get(_path(url))).status_code == 200
        limited = await anon.get(_path(url))
    assert limited.status_code == 429
    assert limited.headers["cache-control"] == "no-store"
    _reset_limits()


@pytest.mark.asyncio
async def test_feed_headers_are_no_store_no_referrer(authenticated_async_context):
    _reset_limits()
    async with authenticated_async_context() as client:
        url = await _make_feed(client)
    async with _anon_client() as anon:
        resp = await anon.get(_path(url))
    assert resp.headers["cache-control"] == "no-store"
    assert resp.headers["referrer-policy"] == "no-referrer"
    assert "noindex" in resp.headers["x-robots-tag"]


# ── the writer ─────────────────────────────────────────────────────────────


def test_ics_escapes_crlf_injection_in_title():
    evil = "Eng\r\nBEGIN:VEVENT\r\nSUMMARY:pwned; x, y\\z\nATTENDEE:mailto:a@b.c"
    now = datetime(2026, 10, 14, tzinfo=timezone.utc)
    ev = calendar_ics.CalEvent(uid="u@job360.uk", summary=f"Interview: {evil}", url="https://x/a\r\nEVIL:1", stamp=now,
                               start=now)
    out = calendar_ics.render_calendar([ev])
    unfolded = _unfold(out)
    lines = unfolded.split("\r\n")
    assert lines.count("BEGIN:VEVENT") == 1, "the title cannot open a second event"
    assert not any(line.startswith(("ATTENDEE", "EVIL")) for line in lines), "no injected property line"
    assert "\\;" in unfolded and "\\," in unfolded and "\\\\" in unfolded and "\\n" in unfolded
    assert "\r" not in unfolded.replace("\r\n", "")
    assert all(len(line.encode()) <= 75 for line in out.split("\r\n")), "folded at 75 octets"


def test_ics_folds_at_75_octets_without_splitting_utf8():
    now = datetime(2026, 10, 14, tzinfo=timezone.utc)
    title = "é中" * 60  # 2- and 3-octet characters
    out = calendar_ics.render_calendar([calendar_ics.CalEvent("u@x", title, "https://x/1", now, start=now)])
    for line in out.split("\r\n"):
        assert len(line.encode("utf-8")) <= 75
    assert _unfold(out).count(title) == 1
    assert re.search(r"\r\n [^\r\n]", out), "a long line is folded with a leading space"


# ── logs ───────────────────────────────────────────────────────────────────


def test_feed_path_redacted_in_logs():
    import logging as _logging

    from src.utils.logger import AccessPathRedactFilter, redact_path

    assert redact_path("/api/calendar/SECRETTOKEN.ics") == "/api/calendar/[redacted]"
    assert redact_path("https://job360.uk/api/calendar/SECRETTOKEN.ics") == "https://job360.uk/api/calendar/[redacted]"
    assert redact_path("/api/calendar-feed") == "/api/calendar-feed", "the management route has no secret in its path"
    record = _logging.LogRecord(
        "uvicorn.access", _logging.INFO, "x", 1, '%s - "%s %s HTTP/%s" %d',
        ("1.2.3.4:5", "GET", "/api/calendar/SECRETTOKEN.ics", "1.1", 200), None,
    )
    AccessPathRedactFilter().filter(record)
    assert "SECRETTOKEN" not in record.getMessage() and "/api/calendar/[redacted]" in record.getMessage()


@pytest.mark.asyncio
async def test_feed_logs_never_carry_token_hash_or_title(authenticated_async_context, log_capture):
    _reset_limits()
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        await _event(client, app_id, "interview_scheduled", scheduled_at=_iso(2))
        url = await _make_feed(client)
        async with _anon_client() as anon:
            assert (await anon.get(_path(url))).status_code == 200
            await anon.get(f"/api/calendar/{'Z' * 43}.ics")
        await client.delete("/api/calendar-feed")
    token = url.rsplit("/", 1)[1].removesuffix(".ics")
    # httpx's own client-side log line is the TEST client talking, not the server.
    blob = repr([r for r in log_capture.records if r.get("name") != "httpx"])
    assert token not in blob and hashlib.sha256(token.encode()).hexdigest() not in blob
    assert "Northwind" not in blob and "Data Engineer" not in blob
    names = {r.get("event") for r in log_capture.records}
    assert {"calendar_feed_created", "calendar_feed_served", "calendar_feed_refused", "calendar_feed_revoked"} <= names
    served = next(r for r in log_capture.records if r.get("event") == "calendar_feed_served")
    assert served["events"] == 1


# ── doors ──────────────────────────────────────────────────────────────────


def test_no_mcp_tool_serves_calendar():
    from src.api import mcp_server

    names = set(mcp_server.build_server()._tool_manager._tools)
    assert not [n for n in names if "calendar" in n], "an agent must never mint or read a feed credential"
    assert "calendar_feeds" not in Path(mcp_server.__file__).read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_feed_management_needs_a_session(authenticated_async_context):
    async with _anon_client() as anon:
        assert (await anon.get("/api/calendar-feed")).status_code == 401
        assert (await anon.post("/api/calendar-feed")).status_code in (401, 403)
        assert (await anon.delete("/api/calendar-feed")).status_code in (401, 403)
    async with authenticated_async_context() as client:
        token = (await client.post("/api/tokens", json={"name": "agent"})).json()["token"]
    async with AsyncClient(
        transport=ASGITransport(app=__import__("src.api.main", fromlist=["app"]).app), base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as bearer:
        resp = await bearer.post("/api/calendar-feed")
        assert resp.status_code == 403 and resp.json()["detail"] == "session_required"


@pytest.mark.asyncio
async def test_application_ics_requires_owner(authenticated_async_context):
    async with _anon_client() as anon:
        assert (await anon.get("/api/applications/1/calendar.ics")).status_code == 401
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        empty = await client.get(f"/api/applications/{app_id}/calendar.ics")
        assert empty.status_code == 404, "nothing scheduled yet"
        await _event(client, app_id, "interview_scheduled", scheduled_at=_iso(2))
        resp = await client.get(f"/api/applications/{app_id}/calendar.ics")
        assert resp.status_code == 200
        assert 'attachment; filename="job360-application-' in resp.headers["content-disposition"]
        assert "SUMMARY:Interview: Data Engineer" in _unfold(resp.text)
        assert (await client.get("/api/applications/999999/calendar.ics")).status_code == 404


# ── one VEVENT per interview, details from the payload, reminders ───────────


def _vevents(body: str) -> list[str]:
    return [b.split("END:VEVENT")[0] for b in body.split("BEGIN:VEVENT")[1:]]


async def _feed_body(client: AsyncClient) -> str:
    url = await _make_feed(client)
    async with _anon_client() as anon:
        resp = await anon.get(_path(url))
    assert resp.status_code == 200, resp.text
    return _unfold(resp.text)


def _span(vevent: str) -> timedelta:
    start = re.search(r"DTSTART:(\d{8}T\d{6}Z)", vevent).group(1)
    end = re.search(r"DTEND:(\d{8}T\d{6}Z)", vevent).group(1)
    return datetime.strptime(end, "%Y%m%dT%H%M%SZ") - datetime.strptime(start, "%Y%m%dT%H%M%SZ")


@pytest.mark.asyncio
async def test_two_interviews_on_one_application_are_two_events(authenticated_async_context):
    _reset_limits()
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        first = await _event(client, app_id, "interview_scheduled", scheduled_at=_iso(3),
                             payload={"round": "Phone screen"})
        second = await _event(client, app_id, "interview_scheduled", scheduled_at=_iso(8),
                              payload={"round": "hiring-manager"})
        body = await _feed_body(client)
    events = _vevents(body)
    assert len(events) == 2
    assert f"UID:app-{app_id}-ev-{first['event_id']}@job360.uk" in events[0]
    assert f"UID:app-{app_id}-ev-{second['event_id']}@job360.uk" in events[1]
    assert "SUMMARY:Interview: Data Engineer – Northwind (Phone screen)" in events[0]
    assert "SUMMARY:Interview: Data Engineer – Northwind (Hiring manager)" in events[1]


@pytest.mark.asyncio
async def test_corrected_interview_is_dropped_but_the_other_round_stays(authenticated_async_context):
    _reset_limits()
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        keep = await _event(client, app_id, "interview_scheduled", scheduled_at=_iso(2))
        old = await _event(client, app_id, "interview_scheduled", scheduled_at=_iso(4))
        fixed = await _event(client, app_id, "interview_scheduled", scheduled_at=_iso(5),
                             corrects_event_id=old["event_id"])
        body = await _feed_body(client)
    assert f"app-{app_id}-ev-{old['event_id']}@" not in body
    assert f"app-{app_id}-ev-{keep['event_id']}@" in body and f"app-{app_id}-ev-{fixed['event_id']}@" in body
    assert len(_vevents(body)) == 2


@pytest.mark.asyncio
async def test_interview_outside_the_window_is_not_listed(authenticated_async_context):
    _reset_limits()
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        await _event(client, app_id, "interview_scheduled",
                     scheduled_at=_iso(-(settings.CALENDAR_FEED_LOOKBACK_DAYS + 5)))
        inside = await _event(client, app_id, "interview_scheduled", scheduled_at=_iso(2))
        body = await _feed_body(client)
    events = _vevents(body)
    assert len(events) == 1 and f"app-{app_id}-ev-{inside['event_id']}@" in events[0]


@pytest.mark.asyncio
async def test_duration_round_link_location_come_from_the_payload(authenticated_async_context):
    _reset_limits()
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        await _event(client, app_id, "interview_scheduled", scheduled_at=_iso(4), payload={
            "round": "Technical", "duration_minutes": 90, "link": "https://meet.example/room?id=1,2",
            "location": "HQ; floor 3",
        })
        solo_ev = await _event(client, app_id, "interview_scheduled", scheduled_at=_iso(5),
                               payload={"link": "https://meet.example/solo", "duration_minutes": 480})
        tiny_ev = await _event(client, app_id, "interview_scheduled", scheduled_at=_iso(6),
                               payload={"duration_minutes": 5})
        await _event(client, app_id, "interview_scheduled", scheduled_at=_iso(7))
        # Out-of-range durations are refused at write time now (slice 43); rows from before that
        # can still hold them, so the feed must keep clamping. Plant such rows directly.
        _sql("UPDATE application_events SET payload = ? WHERE id = ?",
             ('{"link": "https://meet.example/solo", "duration_minutes": 9999}', solo_ev["event_id"]))
        _sql("UPDATE application_events SET payload = ? WHERE id = ?",
             ('{"duration_minutes": 1}', tiny_ev["event_id"]))
        body = await _feed_body(client)
    full, solo, tiny, bare = _vevents(body)
    assert _span(full) == timedelta(minutes=90)
    assert "DESCRIPTION:https://meet.example/room?id=1\\,2\r\n" in full
    assert "LOCATION:HQ\\; floor 3" in full, "an explicit location wins over the link"
    assert "LOCATION:https://meet.example/solo" in solo and "DESCRIPTION:https://meet.example/solo" in solo
    assert _span(solo) == timedelta(minutes=480), "clamped to 8 hours"
    assert _span(tiny) == timedelta(minutes=5), "clamped to 5 minutes"
    assert _span(bare) == timedelta(minutes=60), "no payload: the old 60 minutes"
    assert "LOCATION" not in bare


@pytest.mark.asyncio
async def test_non_http_link_is_dropped(authenticated_async_context):
    _reset_limits()
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        refused = await client.post(f"/api/applications/{app_id}/events", json={
            "event_type": "interview_scheduled", "scheduled_at": _iso(2), "payload": {"link": "javascript:alert(1)"},
        })
        assert refused.status_code == 422, "a non-http link is refused at write time (slice 43)"
        # Rows from before that check can still hold one; the feed must keep dropping it.
        first = await _event(client, app_id, "interview_scheduled", scheduled_at=_iso(2), payload={"round": "Screen"})
        second = await _event(client, app_id, "interview_scheduled", scheduled_at=_iso(3))
        _sql("UPDATE application_events SET payload = ? WHERE id = ?",
             ('{"link": "javascript:alert(1)", "round": "Screen"}', first["event_id"]))
        _sql("UPDATE application_events SET payload = ? WHERE id = ?",
             ('{"link": "file:///etc/passwd"}', second["event_id"]))
        body = await _feed_body(client)
    assert "javascript" not in body and "file:" not in body and "passwd" not in body
    assert "LOCATION" not in body
    assert len(_vevents(body)) == 2 and "(Screen)" in body


@pytest.mark.asyncio
async def test_bad_payload_json_never_breaks_the_feed(authenticated_async_context):
    _reset_limits()
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        ev = await _event(client, app_id, "interview_scheduled", scheduled_at=_iso(2), payload={"round": "x"})
        other = await _event(client, app_id, "interview_scheduled", scheduled_at=_iso(3))
        _sql("UPDATE application_events SET payload = ? WHERE id = ?", ("{not json", ev["event_id"]))
        _sql("UPDATE application_events SET payload = ? WHERE id = ?", ('["a list"]', other["event_id"]))
        body = await _feed_body(client)
    events = _vevents(body)
    assert len(events) == 2
    assert all("\r\nSUMMARY:Interview: Data Engineer – Northwind\r\n" in "\r\n" + e for e in events)
    assert all("TRIGGER:-P1D" in e for e in events), "a bad payload still gets its reminders"


@pytest.mark.asyncio
async def test_interview_has_two_alarms_and_follow_up_one(authenticated_async_context):
    _reset_limits()
    follow_day = (datetime.now(timezone.utc) + timedelta(days=9)).date().isoformat()
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        await _event(client, app_id, "interview_scheduled", scheduled_at=_iso(2), payload={"round": "Final"})
        await _event(client, app_id, "note", follow_up_on=follow_day)
        body = await _feed_body(client)
    interview, follow = _vevents(body)
    assert interview.count("BEGIN:VALARM") == 2 == interview.count("END:VALARM")
    assert interview.count("ACTION:DISPLAY") == 2
    assert "TRIGGER:-P1D" in interview and "TRIGGER:-PT1H" in interview
    assert interview.count("DESCRIPTION:Interview: Data Engineer – Northwind (Final)") == 2
    assert follow.count("BEGIN:VALARM") == 1 and "TRIGGER:PT9H" in follow
    assert "DESCRIPTION:Follow up: Data Engineer – Northwind" in follow


def test_alarm_offsets_come_from_settings(monkeypatch):
    monkeypatch.setattr(settings, "CALENDAR_ALARM_INTERVIEW_FIRST_MINUTES", 2880)
    monkeypatch.setattr(settings, "CALENDAR_ALARM_INTERVIEW_SECOND_MINUTES", 90)
    assert calendar_ics._interview_alarms() == ("-P2D", "-PT90M")
    monkeypatch.setattr(settings, "CALENDAR_ALARM_INTERVIEW_SECOND_MINUTES", 0)
    assert calendar_ics._interview_alarms() == ("-P2D",)


def test_alarm_lines_are_crlf_and_folded():
    now = datetime(2026, 10, 14, tzinfo=timezone.utc)
    ev = calendar_ics.CalEvent(uid="u@x", summary="Interview: " + "é" * 80, url="https://x/1", stamp=now, start=now,
                               description="https://meet.example/" + "a" * 120, alarms=("-P1D", "-PT1H"))
    out = calendar_ics.render_calendar([ev])
    assert out.endswith("END:VCALENDAR\r\n") and "\n" not in out.replace("\r\n", "")
    assert all(len(line.encode()) <= 75 for line in out.split("\r\n"))
    unfolded = _unfold(out)
    assert unfolded.count("BEGIN:VALARM\r\nACTION:DISPLAY\r\nDESCRIPTION:Interview: ") == 2
    assert "\r\nTRIGGER:-P1D\r\nEND:VALARM\r\n" in unfolded and "\r\nTRIGGER:-PT1H\r\nEND:VALARM\r\n" in unfolded
