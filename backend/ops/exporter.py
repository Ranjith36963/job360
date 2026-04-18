"""Prometheus exporter for the freshness KPIs (Pillar 3 Batch 1).

The full Batch 1 deliverable is 6 LIVE KPIs + 4 STUBS. The stubs will
become live once Batch 2 lands the notification / pipeline audit log.

  LIVE (implemented end-to-end in Batch 1):
   1. bucket_accuracy_24h        — target ≥ 90%
   2. bucket_accuracy_48h        — target ≥ 85%
   3. bucket_accuracy_7d         — target ≥ 80%
   4. bucket_accuracy_21d        — target ≥ 80%
   5. date_reliability_ratio     — target ≥ 70%
   6. stale_listing_rate         — target ≤ 5%
   7. crawl_freshness_lag        — per-source; alert if > 2× interval

  STUBS (compute_kpis returns None / {} until Batch 2 audit log exists):
   · notification_latency p50 / p95
   · pipeline_end_to_end_latency p50 / p95
   · notification_delivery_success_rate per-channel

The `prometheus_client` dependency is optional (free-tier stack installs
it separately). `compute_kpis()` returns a plain dict so the metrics can
be measured/asserted even in environments without Prometheus.

Run directly (`python -m ops.exporter`) to start a scrape endpoint on
port 9310. Grafana reads via Prometheus federation.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional

from src.repositories.database import JobDatabase
from src.core.settings import DB_PATH


KPI_PORT = 9310
REFRESH_SECONDS = 300  # 5 minutes


# --------------------------------------------------------------------------
# Core measurements — pure SQL, no Prometheus dependency
# --------------------------------------------------------------------------


async def _date_reliability_ratio(db: JobDatabase) -> float:
    cursor = await db._conn.execute(
        "SELECT date_confidence, COUNT(*) FROM jobs GROUP BY date_confidence"
    )
    rows = dict(await cursor.fetchall())
    total = sum(rows.values())
    if total == 0:
        return 0.0
    trustworthy = (
        rows.get("high", 0)
        + rows.get("medium", 0)
        + rows.get("repost_backdated", 0)
    )
    return trustworthy / total


async def _bucket_accuracy(db: JobDatabase, hours: int) -> float:
    """Fraction of jobs in the `hours`-hour first_seen window whose **trustworthy
    posted_at** falls within that window.

    Per pillar_3_batch_1.md §1 and §5, jobs with date_confidence='low' or
    'fabricated' must NOT appear in the 24h bucket at all. The metric therefore
    measures accuracy over trustworthy-dated rows only. The SQL filters to:

        date_confidence IN ('high', 'medium', 'repost_backdated')
        AND first_seen_at >= window_start

    Low-confidence rows are intentionally excluded from both the numerator and
    the denominator — they do not belong in the bucket in the first place, so
    counting them as "accurate" by using first_seen as a fallback (the prior
    behaviour) produced a circular 100% score.
    """
    now = datetime.now(timezone.utc)
    window_start = (now - timedelta(hours=hours)).isoformat()
    cursor = await db._conn.execute(
        "SELECT posted_at, first_seen_at FROM jobs "
        "WHERE first_seen_at >= ? "
        "AND date_confidence IN ('high', 'medium', 'repost_backdated')",
        (window_start,),
    )
    rows = await cursor.fetchall()
    if not rows:
        return 0.0
    in_window = 0
    for posted_at, first_seen in rows:
        effective = posted_at or first_seen
        if not effective:
            continue
        try:
            eff_dt = datetime.fromisoformat(effective)
            if eff_dt.tzinfo is None:
                eff_dt = eff_dt.replace(tzinfo=timezone.utc)
            if eff_dt >= now - timedelta(hours=hours):
                in_window += 1
        except (ValueError, TypeError):
            continue
    return in_window / len(rows)


async def _stale_listing_rate(db: JobDatabase) -> float:
    cursor = await db._conn.execute("SELECT COUNT(*) FROM jobs")
    (total,) = await cursor.fetchone()
    if total == 0:
        return 0.0
    cursor = await db._conn.execute(
        "SELECT COUNT(*) FROM jobs "
        "WHERE staleness_state IN ('likely_stale', 'confirmed_expired')"
    )
    (stale,) = await cursor.fetchone()
    return stale / total


async def _crawl_freshness_lag(db: JobDatabase) -> dict[str, float]:
    """Per-source: seconds since the last successful scrape (max(last_seen_at))."""
    cursor = await db._conn.execute(
        "SELECT source, MAX(last_seen_at) FROM jobs GROUP BY source"
    )
    rows = await cursor.fetchall()
    now = datetime.now(timezone.utc)
    out: dict[str, float] = {}
    for source, last_seen in rows:
        if not last_seen:
            continue
        try:
            ls = datetime.fromisoformat(last_seen)
            if ls.tzinfo is None:
                ls = ls.replace(tzinfo=timezone.utc)
            out[source] = (now - ls).total_seconds()
        except (ValueError, TypeError):
            continue
    return out


async def compute_kpis(db: Optional[JobDatabase] = None) -> dict:
    """Return a dict of all 10 KPIs. Exposed for tests + CLI measurement."""
    owned = False
    if db is None:
        db = JobDatabase(str(DB_PATH))
        await db.init_db()
        owned = True
    try:
        return {
            "date_reliability_ratio": await _date_reliability_ratio(db),
            "bucket_accuracy_24h": await _bucket_accuracy(db, 24),
            "bucket_accuracy_48h": await _bucket_accuracy(db, 48),
            "bucket_accuracy_7d": await _bucket_accuracy(db, 24 * 7),
            "bucket_accuracy_21d": await _bucket_accuracy(db, 24 * 21),
            "stale_listing_rate": await _stale_listing_rate(db),
            "crawl_freshness_lag_seconds": await _crawl_freshness_lag(db),
            # Notification + pipeline latency KPIs require a notification audit
            # log which is a Batch 2 deliverable — stubbed for now so the KPI
            # surface is complete and the Grafana dashboard doesn't 404.
            "notification_latency_p50_seconds": None,
            "notification_latency_p95_seconds": None,
            "pipeline_e2e_latency_p50_seconds": None,
            "pipeline_e2e_latency_p95_seconds": None,
            "notification_delivery_success_rate": {},
        }
    finally:
        if owned:
            await db.close()


# --------------------------------------------------------------------------
# Prometheus exporter wrapper — optional dependency
# --------------------------------------------------------------------------


def _build_prometheus_gauges():
    from prometheus_client import Gauge  # imported lazily
    return {
        "date_reliability_ratio": Gauge("job360_date_reliability_ratio", "..."),
        "bucket_accuracy_24h":    Gauge("job360_bucket_accuracy_24h", "..."),
        "bucket_accuracy_48h":    Gauge("job360_bucket_accuracy_48h", "..."),
        "bucket_accuracy_7d":     Gauge("job360_bucket_accuracy_7d", "..."),
        "bucket_accuracy_21d":    Gauge("job360_bucket_accuracy_21d", "..."),
        "stale_listing_rate":     Gauge("job360_stale_listing_rate", "..."),
        "crawl_freshness_lag":    Gauge("job360_crawl_freshness_lag_seconds",
                                        "Seconds since last successful scrape",
                                        ["source"]),
        "notification_delivery":  Gauge("job360_notification_delivery_success_rate",
                                        "Delivery success rate",
                                        ["channel"]),
    }


async def _refresh_loop(gauges):
    while True:
        try:
            kpis = await compute_kpis()
            for key in ("date_reliability_ratio", "bucket_accuracy_24h",
                        "bucket_accuracy_48h", "bucket_accuracy_7d",
                        "bucket_accuracy_21d", "stale_listing_rate"):
                gauges[key].set(kpis.get(key) or 0.0)
            for source, lag in kpis.get("crawl_freshness_lag_seconds", {}).items():
                gauges["crawl_freshness_lag"].labels(source=source).set(lag)
            for channel, rate in kpis.get("notification_delivery_success_rate", {}).items():
                gauges["notification_delivery"].labels(channel=channel).set(rate)
        except Exception:
            # Never let observability kill itself — skip this tick and retry
            pass
        await asyncio.sleep(REFRESH_SECONDS)


def main() -> None:
    from prometheus_client import start_http_server  # lazy import
    gauges = _build_prometheus_gauges()
    start_http_server(KPI_PORT)
    asyncio.run(_refresh_loop(gauges))


if __name__ == "__main__":
    main()
