"""Tests for metrics_exporter — pipeline.json + notifications.json snapshots."""
import asyncio
import json

from src.repositories import pgsync
from src.services.metrics_exporter import export_notification_metrics, export_pipeline_metrics

# ---------------------------------------------------------------------------
# Helpers — build minimal in-memory SQLite DBs that match the real schema.
# ---------------------------------------------------------------------------

def _db_with_run_log(path: str) -> None:
    con = pgsync.connect(path)
    con.execute(
        "CREATE TABLE run_log ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "timestamp TEXT, total_found INTEGER, new_jobs INTEGER, "
        "sources_queried INTEGER, total_duration REAL, "
        "run_uuid TEXT, per_source_errors TEXT)"
    )
    con.execute(
        "INSERT INTO run_log VALUES (1,'2026-05-01T10:00:00',42,5,10,12.3,'abc','{}') "
    )
    con.execute(
        "INSERT INTO run_log VALUES (2,'2026-05-01T11:00:00',30,2,10,9.1,'def','{}')"
    )
    con.commit()
    con.close()


def _db_with_ledger(path: str) -> None:
    con = pgsync.connect(path)
    con.execute(
        "CREATE TABLE notification_ledger ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "user_id TEXT, job_id INTEGER, channel TEXT, status TEXT, "
        "sent_at TEXT, error_message TEXT, retry_count INTEGER, "
        "created_at TEXT DEFAULT CURRENT_TIMESTAMP)"
    )
    con.executemany(
        "INSERT INTO notification_ledger(user_id,job_id,channel,status,sent_at,retry_count) VALUES(?,?,?,?,?,?)",
        [
            ("u1", 1, "email", "sent", "2026-05-01T10:00:00", 0),
            ("u1", 2, "email", "sent", "2026-05-01T10:05:00", 0),
            ("u1", 3, "email", "failed", None, 2),
            ("u1", 4, "webhook", "sent", "2026-05-01T10:10:00", 0),
        ],
    )
    con.commit()
    con.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_export_pipeline_metrics_creates_file(tmp_path):
    db = str(tmp_path / "jobs.db")
    _db_with_run_log(db)

    asyncio.run(export_pipeline_metrics(db, metrics_dir=tmp_path))

    out = tmp_path / "pipeline.json"
    assert out.exists()
    data = json.loads(out.read_text())
    assert "runs" in data
    assert len(data["runs"]) == 2


def test_export_pipeline_metrics_correct_fields(tmp_path):
    db = str(tmp_path / "jobs.db")
    _db_with_run_log(db)

    asyncio.run(export_pipeline_metrics(db, metrics_dir=tmp_path))

    runs = json.loads((tmp_path / "pipeline.json").read_text())["runs"]
    first = runs[0]  # ORDER BY id DESC → row 2 is first
    assert first["total_found"] == 30
    assert first["run_uuid"] == "def"
    assert "total_duration" in first


def test_export_notification_metrics_creates_file(tmp_path):
    db = str(tmp_path / "jobs.db")
    _db_with_ledger(db)

    asyncio.run(export_notification_metrics(db, metrics_dir=tmp_path))

    out = tmp_path / "notifications.json"
    assert out.exists()
    data = json.loads(out.read_text())
    assert "channels" in data


def test_export_notification_metrics_aggregates_correctly(tmp_path):
    db = str(tmp_path / "jobs.db")
    _db_with_ledger(db)

    asyncio.run(export_notification_metrics(db, metrics_dir=tmp_path))

    channels = json.loads((tmp_path / "notifications.json").read_text())["channels"]
    by_key = {(r["channel"], r["status"]): r for r in channels}

    email_sent = by_key[("email", "sent")]
    assert email_sent["count"] == 2
    assert email_sent["total_retries"] == 0

    email_failed = by_key[("email", "failed")]
    assert email_failed["count"] == 1
    assert email_failed["total_retries"] == 2

    webhook_sent = by_key[("webhook", "sent")]
    assert webhook_sent["count"] == 1
