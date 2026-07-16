"""DB-backed audit trail (docs/fable/05 C8) — migration 0025 + audit_trail.py.

The audit logger already fires at every security event; these tests pin the new
DB persistence: rows actually land (value-presence, rule #21), the email PII is
NEVER persisted, GDPR erasure anonymises, and a broken writer can never raise
into the request path.
"""
from __future__ import annotations

import logging
import uuid

import pytest

from src.repositories import pgsync
from src.repositories.database import JobDatabase
from src.services import audit_trail
from src.services.audit_trail import _DBAuditHandler


def _make_record(**extras) -> logging.LogRecord:
    rec = logging.makeLogRecord({"msg": "auth", "levelno": logging.INFO})
    for k, v in extras.items():
        setattr(rec, k, v)
    return rec


def _rows(db_path: str, where: str, params: tuple) -> list:
    conn = pgsync.connect(db_path)
    try:
        cur = conn.execute(f"SELECT * FROM audit_log WHERE {where}", params)  # noqa: S608 — test helper, fixed columns
        return cur.fetchall()
    finally:
        conn.close()


@pytest.fixture
def audit_db(tmp_path, monkeypatch):
    """Point DB_PATH at a fresh per-test schema and apply the audit DDL."""
    db_path = str(tmp_path / "audit_test.db")
    from src.core import settings

    monkeypatch.setattr(settings, "DB_PATH", db_path, raising=True)
    from pathlib import Path

    ddl = (Path("migrations") / "0025_audit_log.up.sql").read_text()
    conn = pgsync.connect(db_path)
    try:
        conn.executescript(ddl)
        conn.commit()
    finally:
        conn.close()
    return db_path


def test_emit_persists_a_queryable_row(audit_db):
    uid = uuid.uuid4().hex
    handler = _DBAuditHandler()
    handler.emit(
        _make_record(event="login", status="ok", user_id=uid, client_ip="1.2.3.4", user_agent="pytest")
    )
    rows = _rows(audit_db, "user_id = ?", (uid,))
    assert len(rows) == 1
    row = rows[0]
    assert row["event"] == "login"
    assert row["status"] == "ok"
    assert row["client_ip"] == "1.2.3.4"
    assert row["occurred_at"], "DB default must stamp occurred_at"


def test_email_extra_is_never_persisted(audit_db):
    """Failed-login records carry `email` for the FILE log; the DB row must not.

    DB rows are anonymised via user_id on GDPR erasure — a raw email inside the
    detail blob would dodge that, so it is denylisted.
    """
    handler = _DBAuditHandler()
    handler.emit(
        _make_record(event="login", status="fail", email="victim@example.com", client_ip="9.9.9.9")
    )
    rows = _rows(audit_db, "event = ? AND status = ?", ("login", "fail"))
    assert rows, "row should have been written"
    assert "victim@example.com" not in str(dict(rows[-1]))


def test_writer_failure_never_raises(audit_db, monkeypatch):
    handler = _DBAuditHandler()
    monkeypatch.setattr(handler, "_write", lambda row: (_ for _ in ()).throw(RuntimeError("db down")))
    # Must swallow — an audit failure can never break auth.
    handler.emit(_make_record(event="login", status="ok"))


@pytest.mark.asyncio
async def test_hard_delete_anonymises_audit_rows(audit_db):
    uid = uuid.uuid4().hex
    handler = _DBAuditHandler()
    handler.emit(_make_record(event="login", status="ok", user_id=uid))
    assert _rows(audit_db, "user_id = ?", (uid,))

    db = JobDatabase(audit_db)
    await db.init_db()
    try:
        await db._conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES (?, ?, ?)",
            (uid, f"{uid}@example.com", "h"),
        )
        await db._conn.commit()
        await db.hard_delete_user(uid)
    finally:
        await db.close()

    # The event row SURVIVES (security history) but the personal link is gone.
    assert not _rows(audit_db, "user_id = ?", (uid,))
    survivors = _rows(audit_db, "event = ? AND user_id IS NULL", ("login",))
    assert survivors, "audit history must survive erasure, anonymised"


def test_install_is_idempotent():
    logger = logging.getLogger(f"job360.audit-test-{uuid.uuid4().hex}")
    audit_trail.install_db_audit_trail(logger)
    audit_trail.install_db_audit_trail(logger)
    from logging.handlers import QueueHandler

    assert sum(isinstance(h, QueueHandler) for h in logger.handlers) == 1
