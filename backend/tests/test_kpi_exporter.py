"""Sanity tests for the KPI exporter (Pillar 3 Batch 1).

prometheus_client is NOT required — compute_kpis() runs pure SQL.
"""
import asyncio

import pytest

from src.models import Job
from src.repositories.database import JobDatabase
from ops.exporter import compute_kpis


@pytest.fixture
def db():
    database = JobDatabase(":memory:")
    asyncio.run(database.init_db())
    yield database
    asyncio.run(database.close())


def test_compute_kpis_on_empty_db(db):
    kpis = asyncio.run(compute_kpis(db))
    # All ratios on empty DB are 0 (no division-by-zero)
    assert kpis["date_reliability_ratio"] == 0.0
    assert kpis["bucket_accuracy_24h"] == 0.0
    assert kpis["stale_listing_rate"] == 0.0
    # Crawl lag dict is empty when no sources have seen any jobs
    assert kpis["crawl_freshness_lag_seconds"] == {}


def test_compute_kpis_returns_all_expected_keys(db):
    kpis = asyncio.run(compute_kpis(db))
    expected = {
        "date_reliability_ratio",
        "bucket_accuracy_24h",
        "bucket_accuracy_48h",
        "bucket_accuracy_7d",
        "bucket_accuracy_21d",
        "stale_listing_rate",
        "crawl_freshness_lag_seconds",
        "notification_latency_p50_seconds",
        "notification_latency_p95_seconds",
        "pipeline_e2e_latency_p50_seconds",
        "pipeline_e2e_latency_p95_seconds",
        "notification_delivery_success_rate",
    }
    assert set(kpis.keys()) == expected


def test_date_reliability_ratio_with_mixed_confidence(db):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    async def _run():
        # 2 high-confidence jobs + 3 low-confidence
        for i in range(2):
            await db.insert_job(Job(
                title=f"A{i}", company=f"C{i}", apply_url=f"https://a{i}",
                source="reed", date_found=now,
                posted_at=now, date_confidence="high",
            ))
        for i in range(3):
            await db.insert_job(Job(
                title=f"B{i}", company=f"D{i}", apply_url=f"https://b{i}",
                source="arbeitnow", date_found=now,
                posted_at=None, date_confidence="low",
            ))
    asyncio.run(_run())
    kpis = asyncio.run(compute_kpis(db))
    # 2/5 = 40%
    assert kpis["date_reliability_ratio"] == pytest.approx(0.4, abs=1e-6)


def test_bucket_accuracy_excludes_low_confidence_rows(db):
    """REGRESSION: pre-fix, bucket_accuracy_24h reported ~1.0 for low-confidence
    rows because `effective = first_seen` which was already in the window. Fix
    filters those rows out of the metric — they should never enter the 24h bucket.
    """
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()

    async def _run():
        # Two low-confidence rows with first_seen_at=now. Old code = 100% accuracy.
        for i in range(2):
            await db.insert_job(Job(
                title=f"L{i}", company=f"Co{i}", apply_url=f"https://l{i}",
                source="arbeitnow", date_found=now,
                posted_at=None, date_confidence="low",
            ))
    asyncio.run(_run())
    kpis = asyncio.run(compute_kpis(db))
    # Low-confidence rows filtered out → no trustworthy rows to measure → 0.0
    assert kpis["bucket_accuracy_24h"] == 0.0


def test_bucket_accuracy_high_confidence_today_scores_full(db):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()

    async def _run():
        await db.insert_job(Job(
            title="H", company="Co", apply_url="https://h",
            source="reed", date_found=now,
            posted_at=now, date_confidence="high",
        ))
    asyncio.run(_run())
    kpis = asyncio.run(compute_kpis(db))
    assert kpis["bucket_accuracy_24h"] == 1.0


def test_bucket_accuracy_mixed_confidence_measures_trustworthy_only(db):
    """2 high-confidence rows (both in window) + 3 low-confidence rows
    (all in window). Old buggy code = 5/5 = 100%. New code = 2/2 = 100%
    over ONLY the trustworthy denominator — the low-confidence rows are
    excluded from both numerator and denominator."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()

    async def _run():
        for i in range(2):
            await db.insert_job(Job(
                title=f"H{i}", company=f"HCo{i}", apply_url=f"https://h{i}",
                source="reed", date_found=now,
                posted_at=now, date_confidence="high",
            ))
        for i in range(3):
            await db.insert_job(Job(
                title=f"L{i}", company=f"LCo{i}", apply_url=f"https://l{i}",
                source="arbeitnow", date_found=now,
                posted_at=None, date_confidence="low",
            ))
    asyncio.run(_run())
    kpis = asyncio.run(compute_kpis(db))
    # Denominator is 2 trustworthy rows, both in window → 1.0
    assert kpis["bucket_accuracy_24h"] == 1.0
    # Reliability ratio still reflects the 2/5 mix
    assert kpis["date_reliability_ratio"] == pytest.approx(0.4, abs=1e-6)


def test_crawl_freshness_lag_per_source(db):
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    async def _run():
        await db.insert_job(Job(
            title="A", company="C", apply_url="https://a",
            source="reed",
            date_found=now.isoformat(),
        ))
        # Backdate last_seen_at for the reed row via direct UPDATE
        await db._conn.execute(
            "UPDATE jobs SET last_seen_at = ? WHERE apply_url = ?",
            ((now - timedelta(hours=4)).isoformat(), "https://a"),
        )
        await db._conn.commit()
    asyncio.run(_run())
    kpis = asyncio.run(compute_kpis(db))
    lag = kpis["crawl_freshness_lag_seconds"].get("reed")
    assert lag is not None
    # Approximately 4 hours (14400s), allow a little tolerance
    assert 14000 <= lag <= 14500
