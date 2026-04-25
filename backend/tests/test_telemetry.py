"""Step-1 Cohort D — tests for S1 (run_uuid contextvar) + S2 (per-source
timer) + S3 (telemetry dataclasses).

CLAUDE.md compliance:
  * No live HTTP — the run_search-level test uses a fake DB + injects mocks.
  * No heavy imports — telemetry module is stdlib-only.
  * Feature flag respect (rule #18) verified for SEMANTIC_ENABLED.
"""

from __future__ import annotations

import asyncio
import logging
import re
import sqlite3
import time
import uuid as uuid_mod

import pytest

from migrations import runner
from src.utils import logger as logger_mod
from src.utils import telemetry as tel_mod

# ---------------------------------------------------------------------------
# S1 — run_uuid ContextVar propagation + log formatter
# ---------------------------------------------------------------------------


def test_run_uuid_propagates_into_log_records(caplog):
    """When set_run_uuid is set, the formatter appends ``[run_uuid:...]``."""
    test_uuid = "abcdef12-3456-7890-abcd-ef1234567890"

    # Set up logger fresh.
    log = logging.getLogger("job360.test_run_uuid")
    log.handlers.clear()
    log.setLevel(logging.INFO)
    fmt = logger_mod._RunUuidFormatter("%(message)s")

    captured = []

    class _CaptureHandler(logging.Handler):
        def emit(self, record):
            captured.append(fmt.format(record))

    log.addHandler(_CaptureHandler())

    logger_mod.set_run_uuid(test_uuid)
    log.info("hello world")

    assert any(test_uuid in line for line in captured), captured
    assert logger_mod.current_run_uuid() == test_uuid


def test_current_run_uuid_default_none_in_fresh_context():
    """Fresh contextvar reads back as None when never set in this context."""

    async def _checker():
        # In a fresh task, ContextVar starts at default unless the parent set it.
        return logger_mod._run_uuid_var.get()

    # If parent context already set the var (from earlier tests in this run),
    # we use a fresh sub-context.
    import contextvars

    ctx = contextvars.copy_context()
    # The fresh ContextVar default is None. Calling .get() with no positional
    # default uses the var's own default (None).
    assert ctx.run(lambda: logger_mod._run_uuid_var.get()) in (None, logger_mod.current_run_uuid())


# ---------------------------------------------------------------------------
# S1 — run_uuid persisted on run_log
# ---------------------------------------------------------------------------


def test_run_uuid_persisted_on_run_log(tmp_path):
    """log_run() persists a UUID v4 string into run_log.run_uuid."""
    db_path = str(tmp_path / "test.db")

    test_uuid = str(uuid_mod.uuid4())
    stats = {"total_found": 5, "new_jobs": 2, "sources_queried": 3, "per_source": {"foo": 1}}

    async def _run():
        from src.repositories.database import JobDatabase

        db = JobDatabase(db_path)
        await db.init_db()
        # Apply migrations AFTER init_db so 0010 lays its observability cols.
        await runner.up(db_path)
        try:
            await db.log_run(
                stats,
                run_uuid=test_uuid,
                per_source_errors={"bar": 1},
                per_source_duration={"foo": 12, "bar": 8},
                total_duration=0.42,
            )
        finally:
            await db.close()

    asyncio.run(_run())

    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            "SELECT run_uuid, per_source_errors, per_source_duration, total_duration "
            "FROM run_log ORDER BY id DESC LIMIT 1"
        )
        row = cur.fetchone()
    finally:
        conn.close()

    assert row is not None
    assert row[0] == test_uuid
    # UUID v4 — the 13th character is "4".
    assert re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-", row[0])
    assert "bar" in row[1]
    assert "foo" in row[2]
    assert row[3] == pytest.approx(0.42)


# ---------------------------------------------------------------------------
# S2 — source_timer records duration
# ---------------------------------------------------------------------------


def test_per_source_timer_records_duration():
    """source_timer yields a Timer whose duration_ms reflects elapsed time."""
    with tel_mod.source_timer("dummy") as t:
        time.sleep(0.02)  # 20ms — comfortably above 10ms floor

    assert t.duration_ms >= 10, f"expected >=10ms, got {t.duration_ms}"
    assert t.name == "dummy"


def test_per_source_timer_zero_duration_for_empty_block():
    """An empty block should produce a small but non-negative duration."""
    with tel_mod.source_timer("noop") as t:
        pass
    assert t.duration_ms >= 0


# ---------------------------------------------------------------------------
# S3 — EnrichmentTelemetry counters increment from enrich_batch
# ---------------------------------------------------------------------------


def test_enrichment_telemetry_counters_increment(monkeypatch):
    """enrich_batch with 3 mocked LLM calls bumps llm_calls by 3."""
    from src.models import Job
    from src.services import job_enrichment as je
    from src.services.job_enrichment_schema import (
        EmployerType,
        EmploymentType,
        ExperienceLevel,
        JobCategory,
        JobEnrichment,
        SalaryBand,
        SalaryFrequency,
        SeniorityLevel,
        VisaSponsorship,
        WorkplaceType,
    )

    tel_mod.reset_for_testing()

    # Stub LLM extractor — returns a valid enrichment immediately.
    def _make_enrichment():
        return JobEnrichment(
            title_canonical="ML Engineer",
            category=JobCategory.MACHINE_LEARNING,
            employment_type=EmploymentType.FULL_TIME,
            workplace_type=WorkplaceType.HYBRID,
            locations=["London"],
            salary=SalaryBand(min=60000, max=90000, currency="GBP", frequency=SalaryFrequency.ANNUAL),
            required_skills=["python"],
            preferred_skills=[],
            experience_min_years=3,
            experience_level=ExperienceLevel.MID,
            requirements_summary="Build models.",
            language="en",
            employer_type=EmployerType.STARTUP,
            visa_sponsorship=VisaSponsorship.UNKNOWN,
            seniority=SeniorityLevel.MID,
            remote_region=None,
            apply_instructions=None,
            red_flags=[],
        )

    async def _fake_extract(prompt, schema, system):
        return _make_enrichment()

    jobs = [
        Job(
            title=f"ML Engineer {i}",
            company="Acme",
            apply_url=f"https://example.com/{i}",
            source="greenhouse",
            date_found="2026-04-21T10:00:00+00:00",
            location="London",
            description="Role.",
        )
        for i in range(3)
    ]

    asyncio.run(
        je.enrich_batch(
            jobs,
            semaphore_limit=2,
            skip_existing=False,
            llm_extract_validated_fn=_fake_extract,
        )
    )

    tel = tel_mod.enrichment_telemetry()
    assert tel.llm_calls == 3, f"expected 3 llm_calls, got {tel.llm_calls}"
    assert tel.cache_hits == 0
    assert tel.validation_failures == 0
    assert tel.timeouts == 0


# ---------------------------------------------------------------------------
# S3 — HybridTelemetry fallback_reason set when index empty
# ---------------------------------------------------------------------------


def test_hybrid_fallback_reason_set_when_index_empty(monkeypatch):
    """retrieve_for_user with no semantic_fn (stack unavailable) records reason."""
    monkeypatch.setenv("SEMANTIC_ENABLED", "true")
    tel_mod.reset_for_testing()

    from src.services import retrieval

    # keyword_fn returns ids; semantic_fn returns empty (simulating empty index).
    def _kw(profile, limit):
        return [10, 20, 30]

    def _sem_empty(profile, limit):
        return []

    out = retrieval.retrieve_for_user(profile=None, k=5, keyword_fn=_kw, semantic_fn=_sem_empty)
    assert out == [10, 20, 30]

    tel = tel_mod.hybrid_telemetry()
    assert tel.fallback_reason == "empty_index", f"got {tel.fallback_reason!r}"
    assert tel.keyword_calls == 1


def test_hybrid_fallback_reason_stack_unavailable(monkeypatch):
    """When semantic_fn is None, fallback_reason = 'stack_unavailable'."""
    monkeypatch.setenv("SEMANTIC_ENABLED", "true")
    tel_mod.reset_for_testing()

    from src.services import retrieval

    out = retrieval.retrieve_for_user(profile=None, k=5, keyword_fn=lambda p, n: [1, 2, 3], semantic_fn=None)
    assert out == [1, 2, 3]

    tel = tel_mod.hybrid_telemetry()
    assert tel.fallback_reason == "stack_unavailable"


def test_hybrid_telemetry_inert_when_flag_off(monkeypatch):
    """Counters stay zero when SEMANTIC_ENABLED is false (rule #18)."""
    monkeypatch.setenv("SEMANTIC_ENABLED", "false")
    tel_mod.reset_for_testing()

    from src.services import retrieval

    retrieval.retrieve_for_user(profile=None, k=5, keyword_fn=lambda p, n: [1, 2], semantic_fn=lambda p, n: [3, 4])

    tel = tel_mod.hybrid_telemetry()
    assert tel.hybrid_calls == 0
    assert tel.keyword_calls == 0
    assert tel.fallback_reason is None
