"""Tests for the EDGE check in scripts/product_assertions.py — feed lag.

WHY THIS EXISTS. On 2026-08-15, read live and read-only from production:

    newest job in the shared catalog   2026-08-15T04:05:15.113456+00:00
    newest row in user_feed            2026-08-11T07:02:12.125739+00:00
    jobs ingested in between           1,345
    gap                                93.05 HOURS

Nothing new reached any dashboard for four days, and `product-health.yml`
reported 13/13 GREEN the whole time. It could not do otherwise: every
assertion in that file is a NODE check — "does this table have enough rows /
recent rows / sane values". `jobs` was healthy (10,257 rows, newest hours old).
`user_feed` was healthy too (10,508 rows, far over the floor of 50). The PIPE
between them was severed and no assertion in the system was looking at a pipe.

`feed_lag_hours` is the pure half of that edge check, so the whole thing can be
tested offline with no database at all.

THE CONSTANT MATTERS. These tests pin FEED_LAG_MAX_HOURS to its own value and
prove the pre-existing STALE_FEATURE_DAYS = 14 would have stayed silent right
through the real outage. Reusing that knob would have shipped a guard that
cannot fire — the exact failure this repo keeps repeating.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

# product_assertions.py lives at the REPO ROOT (scripts/), not backend/scripts/,
# so it is not importable by name from here. Load it by path. Its module-level
# imports are stdlib only (psycopg is imported inside main()), so this stays
# offline and cheap.
_PA_PATH = Path(__file__).resolve().parents[2] / "scripts" / "product_assertions.py"

# The real production readings that motivated the check, taken read-only from
# prod on 2026-08-15. Kept verbatim so this test IS the incident, not a
# paraphrase of it.
PROD_NEWEST_JOB = "2026-08-15T04:05:15.113456+00:00"
PROD_NEWEST_FEED = "2026-08-11T07:02:12.125739+00:00"


@pytest.fixture(scope="module")
def pa() -> Any:
    spec = importlib.util.spec_from_file_location("_product_assertions", _PA_PATH)
    assert spec and spec.loader, f"cannot load {_PA_PATH}"
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


# ── The live bug ───────────────────────────────────────────────────────────

def test_the_real_outage_is_measured_in_hours(pa: Any) -> None:
    """The exact prod readings must come back as ~93 hours of lag."""
    lag = pa.feed_lag_hours(PROD_NEWEST_JOB, PROD_NEWEST_FEED)
    assert lag is not None
    assert 93.0 <= lag <= 93.1, f"expected ~93.05h of lag, got {lag}"


def test_the_real_outage_would_have_gone_red(pa: Any) -> None:
    """The guard must FAIL on the live bug it was written for."""
    lag = pa.feed_lag_hours(PROD_NEWEST_JOB, PROD_NEWEST_FEED)
    assert lag is not None and lag > pa.FEED_LAG_MAX_HOURS


def test_stale_feature_days_would_have_stayed_silent(pa: Any) -> None:
    """The whole reason this needed its OWN constant.

    STALE_FEATURE_DAYS is 14 days = 336 hours. A 93-hour freeze is nowhere
    near it, so a guard built on the existing knob would have watched the
    outage go past in silence. If someone later "tidies up" by reusing it,
    this test is what stops them.
    """
    lag = pa.feed_lag_hours(PROD_NEWEST_JOB, PROD_NEWEST_FEED)
    assert lag is not None
    assert lag < pa.STALE_FEATURE_DAYS * 24, "premise: 93h sits inside 14 days"
    assert pa.FEED_LAG_MAX_HOURS < pa.STALE_FEATURE_DAYS * 24
    assert pa.FEED_LAG_MAX_HOURS == 48


# ── Where the 48 comes from ────────────────────────────────────────────────

def test_one_missed_night_is_not_an_alarm(pa: Any) -> None:
    """The catalog refreshes daily, so a single missed run is normal."""
    lag = pa.feed_lag_hours("2026-08-15T04:00:00+00:00", "2026-08-13T22:00:00+00:00")
    assert lag is not None and 0 < lag <= pa.FEED_LAG_MAX_HOURS


def test_two_missed_nights_is_an_alarm(pa: Any) -> None:
    """Two is not normal — that is the whole boundary."""
    lag = pa.feed_lag_hours("2026-08-15T04:00:00+00:00", "2026-08-13T02:00:00+00:00")
    assert lag is not None and lag > pa.FEED_LAG_MAX_HOURS


# ── Not-a-bug shapes that must stay quiet ──────────────────────────────────

def test_feed_ahead_of_catalog_is_zero_not_negative(pa: Any) -> None:
    """A feed row written after the newest job is fine, never a negative lag."""
    lag = pa.feed_lag_hours("2026-08-15T04:00:00+00:00", "2026-08-15T09:00:00+00:00")
    assert lag == 0.0


def test_naive_timestamps_are_read_as_utc(pa: Any) -> None:
    """These are TEXT columns; some rows carry no offset. Assume UTC, like
    _age_days does, rather than crashing or silently comparing wrong."""
    lag = pa.feed_lag_hours("2026-08-15T04:00:00", "2026-08-13T04:00:00")
    assert lag is not None and abs(lag - 48.0) < 0.01


# ── Blindness must not read as health ──────────────────────────────────────

@pytest.mark.parametrize("bad", ["", "1785750903", "27/07/2026", None])
def test_unmeasurable_returns_none(pa: Any, bad: Any) -> None:
    """Garbage in a TEXT timestamp column is real here — data_invariants.py
    exists because `posted_at` once held a Unix epoch. When the edge cannot be
    measured the function says so; the caller then reports BROKEN rather than
    letting "cannot tell" pass as "healthy"."""
    assert pa.feed_lag_hours(PROD_NEWEST_JOB, bad) is None
    assert pa.feed_lag_hours(bad, PROD_NEWEST_FEED) is None


# ── The knob must actually move the verdict ────────────────────────────────

def test_env_override_can_force_the_drill(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every threshold in this file is env-overridable so a canary can force a
    red run. Prove that is true of this one, not just claimed."""
    monkeypatch.setenv("PA_FEED_LAG_MAX_HOURS", "1")
    spec = importlib.util.spec_from_file_location("_pa_reload", _PA_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.FEED_LAG_MAX_HOURS == 1


# ═══════════════════════════════════════════════════════════════════════════
# SIBLINGS OF THE SAME CLASS, found by walking the file after the fix landed.
# ═══════════════════════════════════════════════════════════════════════════

# ── Sibling 1: a freshness guard that could never fire ─────────────────────
#
# product_assertions.py already had a "did this feature stop producing?" check
# for job_enrichment and job_embeddings. It ran `SELECT max(created_at)`, and
# NEITHER TABLE HAS A created_at COLUMN — the real ones are `enriched_at` and
# `embedding_updated_at`. The query raised UndefinedColumn straight into a bare
# `except`, which set the timestamp to None and skipped the check entirely.
# Measured against prod 2026-08-15:
#
#     job_enrichment  max(created_at)         -> UndefinedColumn: does not exist
#     job_embeddings  max(created_at)         -> UndefinedColumn: does not exist
#     job_enrichment  max(enriched_at)        -> 2026-08-08T14:38:24+00:00
#     job_embeddings  max(embedding_updated_at) -> 2026-08-08 14:39:50
#
# So the guard was silent by construction, and the table it printed showed
# "LLM enrichment rows 6560 | ok" the whole time. Same family as the frozen
# feed: a count that reads as health while the freshness question is never
# actually asked.
#
# This test cross-checks the column names against the MIGRATIONS — a different
# artifact from the script — so a rename in either one fails here instead of
# quietly disarming the guard again.

_MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


def _create_table_sql(table: str) -> str:
    """The CREATE TABLE body for `table`, read from the migration files."""
    import re

    for path in sorted(_MIGRATIONS.glob("*.up.sql")):
        sql = path.read_text(encoding="utf-8")
        m = re.search(
            rf"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?{table}\s*\((.*?)\);",
            sql, re.IGNORECASE | re.DOTALL)
        if m:
            return m.group(1)
    raise AssertionError(f"no CREATE TABLE for {table} in {_MIGRATIONS}")


def test_feature_freshness_columns_actually_exist(pa: Any) -> None:
    """Every column the freshness guard reads must exist in the schema."""
    assert pa.FEATURE_FRESHNESS_TABLES, "the guard must name its tables"
    for table, _label, col in pa.FEATURE_FRESHNESS_TABLES:
        body = _create_table_sql(table)
        assert col in body, (
            f"{table} has no column {col!r} — the freshness guard would raise "
            f"UndefinedColumn into a bare except and silently pass forever"
        )


def test_the_original_dead_column_name_cannot_come_back(pa: Any) -> None:
    """`created_at` is the name that disarmed this guard. Pin it out."""
    for table, _label, col in pa.FEATURE_FRESHNESS_TABLES:
        assert col != "created_at", (
            f"{table}.created_at does not exist in production — this is the "
            f"exact bug, do not reintroduce it"
        )


# ── Sibling 2: a "stale catalog" section that only counted rows ────────────
#
# Section 5 of product_assertions.py is headed "STALE CATALOG. Jobs age out; if
# ingest stops the feed rots without anything failing" — and then ran
# `SELECT count(*) FROM jobs` and asserted `> 0`. The comment promised a
# freshness check; the code delivered an existence test. If every source died
# tonight the catalog would hold 10,257 rows forever and this stays green.

def test_catalog_age_uses_hours_not_whole_days(pa: Any) -> None:
    """_age_days floors to whole days, which cannot express a 36-hour gap.
    The catalog check needs hour resolution to sit next to a 48h threshold."""
    age = pa._age_hours("2026-08-15T04:00:00+00:00", now="2026-08-16T16:00:00+00:00")
    assert age is not None and abs(age - 36.0) < 0.01


def test_a_dead_catalog_goes_red(pa: Any) -> None:
    """Ingestion stopped four days ago: rows still present, nothing new."""
    age = pa._age_hours("2026-08-11T04:00:00+00:00", now="2026-08-15T12:00:00+00:00")
    assert age is not None and age > pa.CATALOG_MAX_AGE_HOURS


def test_one_missed_ingest_night_is_not_an_alarm(pa: Any) -> None:
    age = pa._age_hours("2026-08-14T04:00:00+00:00", now="2026-08-15T12:00:00+00:00")
    assert age is not None and age <= pa.CATALOG_MAX_AGE_HOURS


def test_catalog_threshold_is_its_own_constant(pa: Any) -> None:
    """Same lesson as FEED_LAG_MAX_HOURS: 14 days would never fire."""
    assert pa.CATALOG_MAX_AGE_HOURS == 48
    assert pa.CATALOG_MAX_AGE_HOURS < pa.STALE_FEATURE_DAYS * 24
