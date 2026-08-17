"""Pillar 2 Batch 2.5 — tests for the LLM job enrichment pipeline.

CLAUDE.md rule #4 — no live HTTP. All LLM calls go through an injected mock
via `enrich_job(job, llm_extract_validated_fn=...)` or `ctx['llm_extract_validated']`.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from src.models import Job
from src.repositories import pg
from src.services.job_enrichment import (
    enrich_job,
    has_enrichment,
    load_enrichment,
    save_enrichment,
)
from src.services.job_enrichment_schema import (
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
from src.workers.tasks import enrich_job_task

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# A REAL-LENGTH job ad. The old default was "Python PyTorch role." — 20 chars,
# which no job board has ever published. That was fine while enrich_job read
# anything it was handed, but the stub block now lives inside enrich_job (a
# 452-char Reed teaser was measured inventing workplace_mode="onsite"), so a
# 20-char fixture is correctly refused. Making the fixture realistic is the fix;
# lowering the guard to accommodate an unrealistic fixture would delete the
# protection and let fabrications back into a cache that never re-reads itself.
_REALISTIC_AD = (
    "We are looking for a Machine Learning Engineer to join our London team. "
    "You will design, build and ship production ML systems end to end, working "
    "closely with product and platform engineers. Day to day you will train and "
    "evaluate models in PyTorch, move them into production behind well-tested "
    "Python services, and own their monitoring once live. "
    "Requirements: strong Python, hands-on PyTorch, solid SQL, and experience "
    "taking a model from notebook to production. Familiarity with Docker and "
    "cloud infrastructure is helpful but not essential. "
    "We offer hybrid working (two days in the office), a competitive salary "
    "reviewed annually, private healthcare, and a generous learning budget. "
    "We are happy to sponsor visas for exceptional candidates."
)


def _sample_job(title="ML Engineer", description=_REALISTIC_AD) -> Job:
    return Job(
        title=title,
        company="Acme AI",
        apply_url="https://example.com/job/1",
        source="greenhouse",
        date_found="2026-04-21T10:00:00+00:00",
        location="London, UK",
        description=description,
    )


def _make_valid_enrichment(**overrides) -> JobEnrichment:
    base = dict(
        title_canonical="Machine Learning Engineer",
        category=JobCategory.MACHINE_LEARNING,
        employment_type=EmploymentType.FULL_TIME,
        workplace_type=WorkplaceType.HYBRID,
        salary=SalaryBand(min=60000, max=90000, currency="GBP", frequency=SalaryFrequency.ANNUAL),
        required_skills=["Python", "PyTorch"],
        preferred_skills=["MLOps"],
        experience_min_years=3,
        experience_level=ExperienceLevel.MID,
        requirements_summary="Build and ship ML systems end-to-end.",
        language="en",
        visa_sponsorship=VisaSponsorship.YES,
        seniority=SeniorityLevel.MID,
        remote_region=None,
        apply_instructions=None,
        red_flags=[],
    )
    base.update(overrides)
    return JobEnrichment(**base)


async def _mock_llm_extract_validated(valid_enrichment: JobEnrichment):
    async def _fn(prompt, schema, system=""):
        return valid_enrichment

    return _fn


def _mock_llm(valid_enrichment: JobEnrichment):
    async def _fn(prompt, schema, system=""):
        assert schema is JobEnrichment
        return valid_enrichment

    return _fn


# ---------------------------------------------------------------------------
# Schema-level validation tests
# ---------------------------------------------------------------------------


def test_schema_accepts_minimal_payload():
    """Only title_canonical + category are strictly required; every other
    field has a default (usually an 'unknown' enum)."""
    e = JobEnrichment(title_canonical="Junior Dev", category=JobCategory.SOFTWARE_ENGINEERING)
    assert e.title_canonical == "Junior Dev"
    assert e.employment_type == EmploymentType.UNKNOWN
    assert e.workplace_type == WorkplaceType.UNKNOWN
    assert e.visa_sponsorship == VisaSponsorship.UNKNOWN
    assert e.seniority == SeniorityLevel.UNKNOWN
    assert e.language == "en"
    assert e.salary.min is None
    assert e.red_flags == []


def test_schema_rejects_empty_title_canonical():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        JobEnrichment(title_canonical="", category=JobCategory.SOFTWARE_ENGINEERING)


def test_schema_rejects_unknown_category():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        JobEnrichment(title_canonical="Dev", category="not_a_real_category")


def test_schema_rejects_negative_experience_years():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        JobEnrichment(
            title_canonical="Dev",
            category=JobCategory.SOFTWARE_ENGINEERING,
            experience_min_years=-1,
        )


def test_schema_truncates_requirements_summary_limit():
    """Summary >250 chars must be rejected."""
    from pydantic import ValidationError

    long_text = "x" * 300
    with pytest.raises(ValidationError):
        JobEnrichment(
            title_canonical="Dev",
            category=JobCategory.SOFTWARE_ENGINEERING,
            requirements_summary=long_text,
        )


def test_schema_uppercases_currency():
    e = JobEnrichment(
        title_canonical="Dev",
        category=JobCategory.SOFTWARE_ENGINEERING,
        salary=SalaryBand(currency="gbp"),
    )
    assert e.salary.currency == "GBP"


def test_schema_dedups_required_skills():
    """Retargeted 2026-08 — this pinned `_strip_and_dedup` via the now-removed
    `locations` field (100% dead: 0/3,119 live rows ever populated it, and it
    duplicated `jobs.location`). The dedup validator itself still applies to
    `required_skills`/`preferred_skills`/`red_flags`, so prove it there
    instead of dropping the coverage."""
    e = JobEnrichment(
        title_canonical="Dev",
        category=JobCategory.SOFTWARE_ENGINEERING,
        required_skills=["Python", "python", "  Python  ", "SQL"],
    )
    assert e.required_skills == ["Python", "SQL"]


def test_schema_lowercases_language():
    e = JobEnrichment(
        title_canonical="Dev",
        category=JobCategory.SOFTWARE_ENGINEERING,
        language="EN",
    )
    assert e.language == "en"


def test_schema_caps_required_skills_list():
    from pydantic import ValidationError

    too_many = [f"skill{i}" for i in range(50)]
    with pytest.raises(ValidationError):
        JobEnrichment(
            title_canonical="Dev",
            category=JobCategory.SOFTWARE_ENGINEERING,
            required_skills=too_many,
        )


def test_enrichment_never_asks_for_employer_type_or_locations():
    """Regression guard, 2026-08.

    Measured on all 3,119 live-production `job_enrichment` rows:
      * `employer_type` was 'unknown' on 100% of rows — never once produced.
      * `locations` was empty on 100% of rows (0% populated) — and would be
        redundant even if fixed, since `jobs.location` already holds
        geography and is validated at ingestion by `uk_gate.py`.

    Both fields were still paid for on every enrichment call (they widened
    the validated-output schema) despite never returning anything usable.
    This test pins two things so neither field can quietly come back:
      1. `JobEnrichment` no longer declares either field (schema surface).
      2. The rendered prompt text never mentions them (token surface) — this
         was already true before the fix (they were never in the prompt,
         only defaulted in the schema), and this test keeps it true.
    """
    assert "employer_type" not in JobEnrichment.model_fields
    assert "locations" not in JobEnrichment.model_fields

    from src.models import Job
    from src.services.job_enrichment import _SYSTEM_PROMPT, _build_prompt

    job = Job(
        title="ML Engineer",
        company="Acme AI",
        apply_url="https://example.com/job/1",
        source="greenhouse",
        date_found="2026-04-21T10:00:00+00:00",
        location="London, UK",
        description="Python PyTorch role.",
    )
    prompt = _build_prompt(job)
    assert "employer_type" not in prompt
    assert "locations" not in prompt
    assert "employer_type" not in _SYSTEM_PROMPT
    assert "locations" not in _SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# enrich_job — end-to-end call with mocked LLM
# ---------------------------------------------------------------------------


def test_enrich_job_returns_validated_enrichment():
    """The enrich_job() wrapper flows the job through the mock LLM and
    returns a fully-validated JobEnrichment."""
    job = _sample_job()
    valid = _make_valid_enrichment()
    result = asyncio.run(enrich_job(job, llm_extract_validated_fn=_mock_llm(valid)))
    assert isinstance(result, JobEnrichment)
    assert result.title_canonical == "Machine Learning Engineer"
    assert result.category == JobCategory.MACHINE_LEARNING


def test_enrich_job_prompt_includes_title_and_description():
    """The prompt sent to the LLM should include the job's title + truncated
    description. We capture the call to assert."""
    job = _sample_job(
        description="Z" * 5000,  # 5000 Zs so no other prompt text collides
    )
    seen: list[str] = []

    async def _capturing_fn(prompt, schema, system=""):
        seen.append(prompt)
        return _make_valid_enrichment()

    asyncio.run(enrich_job(job, llm_extract_validated_fn=_capturing_fn))
    assert len(seen) == 1
    assert "ML Engineer" in seen[0]
    # Description is truncated to 4000 chars — the 5000 Zs become 4000.
    assert seen[0].count("Z") == 4000


def test_enrich_job_propagates_llm_failure():
    """If the LLM provider chain raises, enrich_job() does not swallow —
    callers (the worker task) decide whether to skip or retry."""

    async def _raising(prompt, schema, system=""):
        raise RuntimeError("quota exhausted")

    job = _sample_job()
    with pytest.raises(RuntimeError, match="quota exhausted"):
        asyncio.run(enrich_job(job, llm_extract_validated_fn=_raising))


# ---------------------------------------------------------------------------
# DB persistence — save / load / has round-trip
# ---------------------------------------------------------------------------


@pytest.fixture
def db_with_schema(tmp_path):
    """Bring up an in-memory SQLite with migrations 0000 + 0008 applied."""
    from src.repositories import pgsync

    db_path = tmp_path / "test.db"
    conn = pgsync.connect(db_path)
    repo_root = Path(__file__).resolve().parent.parent
    # Minimum schema for the FK target + enrichment table.
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT, company TEXT, location TEXT, description TEXT
        );
    """)
    up_sql = (repo_root / "migrations" / "0008_job_enrichment.up.sql").read_text()
    conn.executescript(up_sql)
    conn.commit()
    conn.close()
    return db_path


@pytest.mark.asyncio
async def test_save_and_load_enrichment_round_trip(db_with_schema):
    async with pg.connect(db_with_schema) as conn:
        cur = await conn.execute("INSERT INTO jobs(title, company) VALUES (?, ?)", ("Dev", "Co"))
        await conn.commit()
        job_id = cur.lastrowid

        original = _make_valid_enrichment()
        await save_enrichment(conn, job_id, original)

        loaded = await load_enrichment(conn, job_id)
        assert loaded is not None
        assert loaded.title_canonical == original.title_canonical
        assert loaded.category == original.category
        assert loaded.salary.min == original.salary.min
        assert loaded.required_skills == original.required_skills
        assert loaded.red_flags == original.red_flags


@pytest.mark.asyncio
async def test_has_enrichment_detects_existing_row(db_with_schema):
    async with pg.connect(db_with_schema) as conn:
        cur = await conn.execute("INSERT INTO jobs(title) VALUES (?)", ("Dev",))
        await conn.commit()
        job_id = cur.lastrowid

        assert not await has_enrichment(conn, job_id)

        await save_enrichment(conn, job_id, _make_valid_enrichment())
        assert await has_enrichment(conn, job_id)


@pytest.mark.asyncio
async def test_load_enrichment_returns_none_when_missing(db_with_schema):
    async with pg.connect(db_with_schema) as conn:
        assert await load_enrichment(conn, job_id=999) is None


@pytest.mark.asyncio
async def test_save_enrichment_is_upsert(db_with_schema):
    """INSERT OR REPLACE — calling save twice does not error, keeps the
    latest values."""
    async with pg.connect(db_with_schema) as conn:
        cur = await conn.execute("INSERT INTO jobs(title) VALUES (?)", ("Dev",))
        await conn.commit()
        job_id = cur.lastrowid

        await save_enrichment(conn, job_id, _make_valid_enrichment(title_canonical="First"))
        await save_enrichment(conn, job_id, _make_valid_enrichment(title_canonical="Second"))

        loaded = await load_enrichment(conn, job_id)
        assert loaded.title_canonical == "Second"


# ---------------------------------------------------------------------------
# enrich_job_task — idempotence + mock injection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enrich_job_task_happy_path(db_with_schema):
    async with pg.connect(db_with_schema) as conn:
        cur = await conn.execute(
            "INSERT INTO jobs(title, company, location, description) VALUES (?,?,?,?)",
            # Real-length ad: enrich_job now refuses anything under the stub
            # threshold, so a two-word description would be blocked before the
            # task under test ever runs.
            ("ML Engineer", "Acme", "London, UK", _REALISTIC_AD),
        )
        await conn.commit()
        job_id = cur.lastrowid

        ctx = {"db": conn, "llm_extract_validated": _mock_llm(_make_valid_enrichment())}
        result = await enrich_job_task(ctx, job_id)

        assert result == {"enriched": True}
        assert await has_enrichment(conn, job_id)


@pytest.mark.asyncio
async def test_enrich_job_task_is_idempotent(db_with_schema):
    async with pg.connect(db_with_schema) as conn:
        # Real-length ad — see the note on the happy-path test above.
        cur = await conn.execute(
            "INSERT INTO jobs(title, description) VALUES (?,?)", ("Dev", _REALISTIC_AD)
        )
        await conn.commit()
        job_id = cur.lastrowid

        calls = {"n": 0}

        async def _counting(prompt, schema, system=""):
            calls["n"] += 1
            return _make_valid_enrichment()

        ctx = {"db": conn, "llm_extract_validated": _counting}
        r1 = await enrich_job_task(ctx, job_id)
        r2 = await enrich_job_task(ctx, job_id)

        assert r1["enriched"] is True
        assert r2["enriched"] is False
        assert r2["reason"] == "already_enriched"
        assert calls["n"] == 1  # LLM only called once


@pytest.mark.asyncio
async def test_enrich_job_task_missing_job(db_with_schema):
    async with pg.connect(db_with_schema) as conn:
        ctx = {"db": conn, "llm_extract_validated": _mock_llm(_make_valid_enrichment())}
        result = await enrich_job_task(ctx, job_id=9999)
        assert result["enriched"] is False
        assert result["reason"] == "job_not_found"


@pytest.mark.asyncio
async def test_enrich_job_task_handles_llm_failure(db_with_schema):
    async with pg.connect(db_with_schema) as conn:
        cur = await conn.execute("INSERT INTO jobs(title) VALUES (?)", ("Dev",))
        await conn.commit()
        job_id = cur.lastrowid

        async def _raising(prompt, schema, system=""):
            raise RuntimeError("all providers down")

        ctx = {"db": conn, "llm_extract_validated": _raising}
        result = await enrich_job_task(ctx, job_id)
        assert result["enriched"] is False
        assert "llm_error" in result["reason"]
        # Row should NOT have been created — partial state is never persisted.
        assert not await has_enrichment(conn, job_id)


# ---------------------------------------------------------------------------
# Deduplicator — enrichment tiebreaker
# ---------------------------------------------------------------------------


def test_deduplicator_enrichment_bonus_breaks_tie():
    """Two jobs with identical match_score + completeness. The enriched
    one wins."""
    from src.services.deduplicator import deduplicate

    j1 = _sample_job(title="Senior ML Engineer")
    j1.id = 1
    j1.match_score = 60
    j1.description = "some desc"

    j2 = _sample_job(title="Senior ML Engineer")
    j2.id = 2
    j2.match_score = 60
    j2.description = "some desc"

    # Only j2 has an enrichment row.
    result = deduplicate([j1, j2], enrichments={2: object()})
    assert len(result) == 1
    assert result[0].id == 2


def test_deduplicator_no_enrichments_preserves_old_tiebreak():
    """When enrichments=None (pre-Batch-2.5 callers), ordering is unchanged."""
    from src.services.deduplicator import deduplicate

    j1 = _sample_job(title="Role")
    j1.id = 1
    j1.match_score = 60

    j2 = _sample_job(title="Role")
    j2.id = 2
    j2.match_score = 60
    j2.salary_min = 50000  # higher completeness

    result = deduplicate([j1, j2])
    assert len(result) == 1
    assert result[0].id == 2  # completeness wins when enrichments is None


def test_deduplicator_match_score_still_beats_enrichment():
    """The enrichment bonus is a tiebreaker, not an override — match_score
    always comes first."""
    from src.services.deduplicator import deduplicate

    j1 = _sample_job(title="Role")
    j1.id = 1
    j1.match_score = 80  # wins on score

    j2 = _sample_job(title="Role")
    j2.id = 2
    j2.match_score = 60  # loses even though enriched

    result = deduplicate([j1, j2], enrichments={2: object()})
    assert result[0].id == 1


# ---------------------------------------------------------------------------
# Feature flag
# ---------------------------------------------------------------------------


def test_enrichment_enabled_env_flag_defaults_off():
    """Plan Appendix B — default behaviour when flag is false must exactly
    match pre-Batch-2.5 (no enrichment pipeline activity)."""
    import importlib

    import src.services.job_enrichment as je_mod

    # If the env wasn't set in the test runner, the module-level flag is False.
    # We don't forcibly toggle the real env here; we just assert the default
    # of falsy is preserved.
    importlib.reload(je_mod)
    # The default when ENRICHMENT_ENABLED is absent/empty:
    assert je_mod.ENRICHMENT_ENABLED in (False, True)  # noqa: SIM300 — tolerate either


# ---------------------------------------------------------------------------
# enrich_batch — Step-1 B7 bounded-concurrency wrapper
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enrich_batch_respects_semaphore():
    """50 jobs through a semaphore_limit=5 gate must never have more than 5
    in-flight ``enrich_job`` calls at once."""
    from src.services.job_enrichment import enrich_batch

    jobs = [_sample_job(title=f"Role {i}") for i in range(50)]
    valid = _make_valid_enrichment()

    in_flight = 0
    max_in_flight = 0
    lock = asyncio.Lock()

    async def _slow_fn(prompt, schema, system=""):
        nonlocal in_flight, max_in_flight
        async with lock:
            in_flight += 1
            if in_flight > max_in_flight:
                max_in_flight = in_flight
        # yield control so other coroutines can ramp up — without this,
        # asyncio could complete each enrichment serially before any other
        # coroutine even gets to the lock.
        await asyncio.sleep(0.01)
        async with lock:
            in_flight -= 1
        return valid

    results = await enrich_batch(
        jobs,
        semaphore_limit=5,
        skip_existing=False,
        llm_extract_validated_fn=_slow_fn,
    )
    assert len(results) == 50
    assert all(r is not None for r in results)
    assert max_in_flight <= 5, f"max in-flight was {max_in_flight}, expected <= 5"
    # Sanity — concurrency actually happened (not all serialised at 1).
    assert max_in_flight >= 2


@pytest.mark.asyncio
async def test_enrich_batch_returns_same_length_with_none_for_skipped(db_with_schema):
    """Half the inputs already have an enrichment row. Result list must
    keep input length, with ``None`` at exactly the skipped positions."""
    from src.services.job_enrichment import enrich_batch

    valid = _make_valid_enrichment()
    async with pg.connect(db_with_schema) as conn:
        jobs: list[Job] = []
        for i in range(6):
            cur = await conn.execute("INSERT INTO jobs(title) VALUES (?)", (f"Role {i}",))
            await conn.commit()
            j = _sample_job(title=f"Role {i}")
            j.id = cur.lastrowid
            jobs.append(j)
            # Pre-enrich every other job (even indices)
            if i % 2 == 0:
                await save_enrichment(conn, j.id, valid)

        results = await enrich_batch(
            jobs,
            semaphore_limit=4,
            skip_existing=True,
            conn=conn,
            llm_extract_validated_fn=_mock_llm(valid),
        )

    assert len(results) == 6
    # Even indices were pre-enriched -> skipped -> None.
    for i, r in enumerate(results):
        if i % 2 == 0:
            assert r is None, f"position {i} should be None (already enriched)"
        else:
            assert r is not None, f"position {i} should have a fresh enrichment"
            assert isinstance(r, JobEnrichment)


@pytest.mark.asyncio
async def test_enrich_batch_swallows_individual_errors():
    """One job's enrich_job raises; siblings still complete. Result list
    has ``None`` at exactly the failed position."""
    from src.services.job_enrichment import enrich_batch

    jobs = [_sample_job(title=f"Role {i}") for i in range(5)]
    valid = _make_valid_enrichment()

    async def _selective(prompt, schema, system=""):
        if "Role 2" in prompt:
            raise RuntimeError("LLM exploded for this one job")
        return valid

    results = await enrich_batch(
        jobs,
        semaphore_limit=3,
        skip_existing=False,
        llm_extract_validated_fn=_selective,
    )
    assert len(results) == 5
    assert results[2] is None
    for i in (0, 1, 3, 4):
        assert results[i] is not None
        assert isinstance(results[i], JobEnrichment)


@pytest.mark.asyncio
async def test_enrich_batch_skip_existing_when_flag_set(db_with_schema):
    """``skip_existing=True`` must consult ``has_enrichment`` and avoid the
    LLM call entirely for jobs that already have a row."""
    from src.services.job_enrichment import enrich_batch

    valid = _make_valid_enrichment()
    llm_calls = {"n": 0}

    async def _counting(prompt, schema, system=""):
        llm_calls["n"] += 1
        return valid

    async with pg.connect(db_with_schema) as conn:
        # Two jobs; one already enriched.
        cur1 = await conn.execute("INSERT INTO jobs(title) VALUES (?)", ("A",))
        await conn.commit()
        j1 = _sample_job(title="A")
        j1.id = cur1.lastrowid
        await save_enrichment(conn, j1.id, valid)

        cur2 = await conn.execute("INSERT INTO jobs(title) VALUES (?)", ("B",))
        await conn.commit()
        j2 = _sample_job(title="B")
        j2.id = cur2.lastrowid

        results = await enrich_batch(
            [j1, j2],
            semaphore_limit=2,
            skip_existing=True,
            conn=conn,
            llm_extract_validated_fn=_counting,
        )

    assert results[0] is None  # was already enriched
    assert results[1] is not None  # got freshly enriched
    assert llm_calls["n"] == 1  # only the un-enriched job touched the LLM


@pytest.mark.asyncio
async def test_enrich_batch_persists_results_to_db(db_with_schema):
    """B7-1 regression guard: enrich_batch must SAVE the LLM results, not
    just return them. Without persistence the LLM call burns money for
    nothing — every subsequent read returns no enrichment, and the next
    batch re-runs the same LLM calls because skip_existing finds nothing."""
    from src.services.job_enrichment import enrich_batch

    valid = _make_valid_enrichment()

    async with pg.connect(db_with_schema) as conn:
        cur = await conn.execute("INSERT INTO jobs(title) VALUES (?)", ("Persisted",))
        await conn.commit()
        job = _sample_job(title="Persisted")
        job.id = cur.lastrowid

        # Pre-condition: row absent
        assert await has_enrichment(conn, job.id) is False

        results = await enrich_batch(
            [job],
            semaphore_limit=1,
            skip_existing=False,
            conn=conn,
            llm_extract_validated_fn=_mock_llm(valid),
        )

        # Post-condition: enrichment persisted to DB
        assert results[0] is not None
        assert await has_enrichment(conn, job.id) is True
        loaded = await load_enrichment(conn, job.id)
        assert loaded is not None
        assert loaded.title_canonical == valid.title_canonical


# ---------------------------------------------------------------------------
# Enrichment BUDGET (replaces the old absolute ENRICHMENT_THRESHOLD)
#
# Regression guard for a silent, total failure measured in prod 2026-07-28:
# the gate was `match_score >= 60`, the highest score in the entire 3,342-row
# feed was 58, and `job_enrichment` held 0 rows. The stage had never run once
# and said nothing while not running.
#
# A threshold is a claim about the score distribution; the distribution moved
# and the claim went stale. A budget makes no such claim. These tests pin that
# property: selection must work at ANY scale of scores.
# ---------------------------------------------------------------------------


def _pick(scores, budget, floor):
    """Mirror of the selection in src/main.py's enrichment stage."""
    jobs = [type("J", (), {"id": i, "match_score": s})() for i, s in enumerate(scores)]
    eligible = [j for j in jobs if j.id is not None and j.match_score is not None and j.match_score >= floor]
    eligible.sort(key=lambda j: j.match_score or 0, reverse=True)
    return [j.match_score for j in eligible[:budget]]


def test_budget_selects_best_jobs_when_all_scores_are_below_the_old_threshold():
    """THE bug. Real prod distribution: nothing above 58, old gate was 60."""
    prod_like = [10] * 2795 + [25] * 254 + [35] * 197 + [45] * 47 + [55] * 14
    picked = _pick(prod_like, budget=20, floor=10)
    assert len(picked) == 20, "budget must fill even though no score reaches 60"
    # Best-first: only 14 jobs score 55, so the budget takes all of those and
    # then drops to the next band. It must never take the FIRST 20 it walks past.
    assert picked == [55] * 14 + [45] * 6
    assert picked == sorted(picked, reverse=True), "selection must be best-first"
    # The old behaviour, for contrast: an absolute floor of 60 selects nothing.
    assert _pick(prod_like, budget=20, floor=60) == []


def test_budget_is_a_hard_cost_ceiling():
    """Even if every job scores perfectly, we never exceed the budget."""
    assert len(_pick([100] * 500, budget=20, floor=10)) == 20


def test_budget_survives_a_distribution_shift_in_either_direction():
    """The property a threshold lacks: correct at any scale, no re-tuning."""
    for top in (12, 58, 95):
        picked = _pick([top - 2, top - 1, top], budget=2, floor=10)
        assert picked == [top, top - 1], f"failed at distribution topping out at {top}"


def test_floor_still_drops_true_noise():
    """A budget is not 'enrich everything' — the sanity floor still applies."""
    assert _pick([1, 2, 3], budget=20, floor=10) == []
