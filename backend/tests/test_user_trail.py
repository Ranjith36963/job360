"""scripts/user_trail.py — one user's timeline, with NO values in the output."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from scripts.user_trail import build_trail, mask_email, render
from src.repositories import pgsync
from src.repositories.database import JobDatabase

EMAIL = "trailtester@example.com"
SECRET_VALUE = "SECRET-VALUE-do-not-print"


@pytest.fixture
def trail_db(tmp_path, monkeypatch):
    """Fresh per-test schema with the full migrated schema (same as test_audit_trail)."""
    from migrations import runner
    from src.core import settings

    db_path = str(tmp_path / "trail_test.db")
    monkeypatch.setattr(settings, "DB_PATH", db_path, raising=True)

    async def _boot():
        db = JobDatabase(db_path)
        await db.init_db()
        await db.close()
        await runner.up(db_path)

    asyncio.run(_boot())
    return db_path


def _ts(delta: timedelta) -> str:
    return (datetime.now(timezone.utc) - delta).strftime("%Y-%m-%d %H:%M:%S")


def _seed(db_path: str) -> str:
    uid = uuid.uuid4().hex
    other = uuid.uuid4().hex
    conn = pgsync.connect(db_path)
    try:
        for u, mail in ((uid, EMAIL), (other, "someoneelse@example.com")):
            conn.execute(
                "INSERT INTO users (id, email, password_hash) VALUES (?, ?, 'x')", (u, mail)
            )
        audit = (
            ("mcp_tool_call", "error", uid, {"tool": "update_profile", "http_status": 422}, 1),
            (
                "profile_edit_rejected", "422", uid,
                {"failed_path": "user_info.contact.country", "paths": ["a.b", "c.d"],
                 "reason": "bad_value", "note": SECRET_VALUE, "value": SECRET_VALUE},
                2,
            ),
            ("mcp_tool_call", "ok", uid, {"tool": "get_profile", "free_text": SECRET_VALUE}, 3),
            ("mcp_tool_call", "ok", uid, {"tool": "update_profile"}, 24 * 30),  # outside window
            ("mcp_tool_call", "ok", other, {"tool": "other_users_tool"}, 1),
        )
        for event, status, owner, detail, hours in audit:
            conn.execute(
                "INSERT INTO audit_log (occurred_at, event, status, user_id, detail) "
                "VALUES (?, ?, ?, ?, ?)",
                (_ts(timedelta(hours=hours)), event, status, owner, json.dumps(detail)),
            )
        conn.execute(
            "INSERT INTO profile_edits (user_id, path, value, set_by, set_at) VALUES (?, ?, ?, ?, ?)",
            (uid, "preferences.check_every", json.dumps(SECRET_VALUE), "token:7",
             _ts(timedelta(hours=1))),
        )
        conn.execute(
            "INSERT INTO application_events (user_id, application_id, event_type, detail, payload, "
            "occurred_at, recorded_at, recorded_by) VALUES (?, 9, 'applied', ?, '{}', ?, ?, 'agent:3')",
            (uid, SECRET_VALUE, _ts(timedelta(hours=3)), _ts(timedelta(hours=3))),
        )
        conn.execute(
            "INSERT INTO assistant_setting_requests (user_id, path, value, requested_by, requested_at) "
            "VALUES (?, 'assistant_settings.apply_mode', ?, 'token:7', ?)",
            (uid, json.dumps(SECRET_VALUE), _ts(timedelta(hours=4))),
        )
        conn.commit()
    finally:
        conn.close()
    return uid


def _cursor(db_path: str):
    return pgsync.connect(db_path)._raw.cursor()


def test_timeline_has_every_source_in_time_order_and_only_this_user(trail_db):
    _seed(trail_db)
    entries = build_trail(_cursor(trail_db), EMAIL, days=7)
    assert entries is not None
    stamps = [e["ts"] for e in entries]
    assert stamps == sorted(stamps)
    whats = [e["what"] for e in entries]
    assert {"update_profile", "get_profile", "profile_edit", "event:applied",
            "setting_request"} <= set(whats)
    assert "other_users_tool" not in whats
    assert len(entries) == 6, "the 30-day-old row must be outside the 7 day window"


def test_reason_path_and_status_are_shown(trail_db):
    _seed(trail_db)
    entries = build_trail(_cursor(trail_db), EMAIL, days=7)
    rejected = [e for e in entries if e["reason"] == "bad_value"]
    assert rejected and rejected[0]["path"] == "user_info.contact.country"
    http = [e for e in entries if e["reason"] == "http_422"]
    assert http and http[0]["what"] == "update_profile"


def test_output_never_contains_values_or_the_full_email(trail_db):
    _seed(trail_db)
    out = render(EMAIL, 7, build_trail(_cursor(trail_db), EMAIL, days=7))
    assert SECRET_VALUE not in out
    assert EMAIL not in out and "trailtester" not in out
    assert "tr***@example.com" in out


def test_unknown_user_is_none(trail_db):
    _seed(trail_db)
    assert build_trail(_cursor(trail_db), "nobody@example.com") is None


def test_email_lookup_is_case_insensitive(trail_db):
    _seed(trail_db)
    assert build_trail(_cursor(trail_db), EMAIL.upper(), days=7)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("abcdefg@x.io", "ab***@x.io"), ("ab@x.io", "***@x.io"), ("a@x.io", "***@x.io"), ("nope", "(none)"), (None, "(none)")],
)
def test_mask_email(raw, expected):
    assert mask_email(raw) == expected


def test_empty_failed_path_falls_through_to_paths():
    from scripts.user_trail import _audit_fields

    out = _audit_fields({"tool": "update_profile", "failed_path": "", "paths": ["a.b", "c.d"], "reason": ""})
    assert "failed_path" not in out and out["paths"] == "a.b,c.d" and "reason" not in out
