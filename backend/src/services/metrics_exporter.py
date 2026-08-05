"""Metrics exporters — dump pipeline + notification snapshots to JSON files.

⚠️ NOTHING READS THESE FILES. Audited 2026-08-03: `pipeline.json` and
`notifications.json` are written here (called from `src/main.py`) and grepping
the whole repo — scripts, workflows, backend, frontend — finds no reader. On
Railway they land on the container's ephemeral filesystem and are wiped by every
deploy, so even a human cannot go and look at yesterday's.

This repo has a law, learned by losing four artifacts to it: **an artifact with
no notifier dies.** This is one. It is left in place rather than deleted because
it is genuinely useful for LOCAL debugging (`data/metrics/`) and it has tests —
but do not mistake it for monitoring. If you want these numbers watched, the
consumer belongs in `scripts/product_assertions.py` or
`scripts/data_invariants.py`, which run against production on a schedule and
raise an issue that the triage → repair chain picks up.

The live equivalents that ARE consumed: `run_log` (read by
`scripts/absence_check.py` every morning) and the per-user funnel in
`scripts/user_journey_audit.py`.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from src.core.settings import METRICS_DIR
from src.repositories import pg

_DEFAULT_METRICS_DIR = METRICS_DIR


def _atomic_write(dest: Path, text: str) -> None:
    """Write *text* to *dest* atomically via a unique temp file.

    Uses a per-call unique name (via NamedTemporaryFile) so concurrent
    pipeline runs writing to the same metrics directory never clobber
    each other's in-flight tmp file.  ``os.replace`` is atomic on POSIX
    and near-atomic on Windows NTFS.
    """
    fd, tmp_path = tempfile.mkstemp(dir=dest.parent, prefix=dest.stem + "_", suffix=".tmp")
    try:
        os.write(fd, text.encode())
        os.close(fd)
        os.replace(tmp_path, dest)
    except Exception:
        os.close(fd)
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


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
    async with pg.connect(db_path) as db:
        db.row_factory = pg.Row
        cursor = await db.execute(
            "SELECT timestamp, total_found, new_jobs, sources_queried, "
            "total_duration, run_uuid, per_source_errors "
            "FROM run_log ORDER BY id DESC LIMIT 50"
        )
        rows = await cursor.fetchall()

    records = [dict(row) for row in rows]
    out = metrics_dir / "pipeline.json"
    _atomic_write(out, json.dumps({"runs": records}, indent=2, default=str))


async def export_notification_metrics(
    db_path: str,
    metrics_dir: Path = _DEFAULT_METRICS_DIR,
) -> None:
    """Write per-channel notification stats to ``metrics_dir/notifications.json``.

    Groups by (channel, status) and counts rows, tracks the latest
    sent_at, and sums retry_count to surface delivery health at a glance.
    """
    metrics_dir.mkdir(parents=True, exist_ok=True)
    async with pg.connect(db_path) as db:
        db.row_factory = pg.Row
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
    _atomic_write(out, json.dumps({"channels": records}, indent=2, default=str))
