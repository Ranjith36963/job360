"""Metrics exporters — dump pipeline + notification snapshots to JSON files."""
from __future__ import annotations

import json
from pathlib import Path

import aiosqlite

from src.core.settings import METRICS_DIR

_DEFAULT_METRICS_DIR = METRICS_DIR


async def export_pipeline_metrics(
    db_path: str,
    metrics_dir: Path = _DEFAULT_METRICS_DIR,
) -> None:
    """Write the last 50 run_log rows to ``metrics_dir/pipeline.json``.

    Captures: timestamp, total_found, new_jobs, sources_queried,
    total_duration, run_uuid, per_source_errors.  The snapshot is a
    complete overwrite so downstream consumers always see a stable file.
    """
    metrics_dir.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT timestamp, total_found, new_jobs, sources_queried, "
            "total_duration, run_uuid, per_source_errors "
            "FROM run_log ORDER BY id DESC LIMIT 50"
        )
        rows = await cursor.fetchall()

    records = [dict(row) for row in rows]
    out = metrics_dir / "pipeline.json"
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"runs": records}, indent=2, default=str))
    tmp.replace(out)


async def export_notification_metrics(
    db_path: str,
    metrics_dir: Path = _DEFAULT_METRICS_DIR,
) -> None:
    """Write per-channel notification stats to ``metrics_dir/notifications.json``.

    Groups by (channel, status) and counts rows, tracks the latest
    sent_at, and sums retry_count to surface delivery health at a glance.
    """
    metrics_dir.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT channel, status, COUNT(*) AS count, "
            "MAX(sent_at) AS last_sent_at, SUM(retry_count) AS total_retries "
            "FROM notification_ledger "
            "GROUP BY channel, status "
            "ORDER BY channel, status"
        )
        rows = await cursor.fetchall()

    records = [dict(row) for row in rows]
    out = metrics_dir / "notifications.json"
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"channels": records}, indent=2, default=str))
    tmp.replace(out)
