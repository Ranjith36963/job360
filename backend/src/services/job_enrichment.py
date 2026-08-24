"""Pillar 2 Batch 2.5 — LLM job enrichment pipeline.

Given a `Job`, produces a `JobEnrichment` via the shared Gemini→Groq→Cerebras
provider chain and persists it to the `job_enrichment` table. Idempotent —
a second call on a `job_id` that already has a row is a no-op unless the
caller passes `force=True`.

CLAUDE.md compliance:
  * Rule #4 — no live HTTP calls during tests. Tests inject a mock
    `llm_extract_validated_fn` to avoid touching providers.
  * Rule #10 — `job_enrichment` is a **shared catalog** table (no user_id
    column). Per-user state continues to live in `user_feed` / `user_actions`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Awaitable
from typing import Callable, Optional

from src.models import Job
from src.repositories import pg
from src.services.job_enrichment_schema import JobEnrichment
from src.services.profile.llm_provider import llm_extract_validated
from src.services.shelf_gate import is_stub_description

logger = logging.getLogger("job360.services.job_enrichment")


class StubDescriptionError(ValueError):
    """Raised when an ad is too thin for JOB SOURCE ENRICHMENT to read honestly.

    Deliberately its own type rather than a bare ValueError: callers must be
    able to tell "we refused to read this" (expected, cheap, skip it) apart
    from "the model failed" (retry-worthy). Conflating them would either
    retry a refusal forever or silently swallow a real provider outage.
    """

# Feature flag — plan Appendix B. Default-off behaviour must exactly match
# pre-Batch-2.5 (no enrichment calls, no DB writes).
ENRICHMENT_ENABLED = os.getenv("ENRICHMENT_ENABLED", "false").lower() in {"1", "true", "yes", "on"}


# The callable type of `llm_extract_validated` — declared so test doubles can
# be passed through `_extract_fn` without the import loop that would occur if
# tests patched the module-level function directly.
LLMExtractFn = Callable[[str, type, str], Awaitable[JobEnrichment]]


_SYSTEM_PROMPT = (
    "You are a job-posting structurer. Return ONLY valid JSON matching the "
    "schema. Do not wrap in prose. If a field is unknown, use the explicit "
    "enum value 'unknown' (never omit required fields)."
)


def _build_prompt(job: Job) -> str:
    """Render a concise prompt for the LLM. Truncation keeps token budget
    bounded for weak providers (Cerebras 8K context).

    The exact JSON keys + allowed enum values are spelled out here on purpose:
    weak free-tier models cannot infer the field names from a schema *name*
    alone (they emit ``job_title`` instead of the required ``title_canonical``,
    failing validation every time). Listing the contract inline fixes that.

    Deliberately excludes ``employer_type`` and ``locations`` — never asked
    for here (both silently rode on their Pydantic defaults) and, per the
    2026-08 measurement across 3,119 live enriched rows, the model never
    produced a usable value for either (`employer_type` 100% 'unknown',
    `locations` 0% populated). Do not add them back to this key list without
    new evidence the model can actually decide them; ``jobs.location``
    already covers geography (validated at ingestion by `uk_gate.py`).
    """
    desc = (job.description or "")[:4000]
    return (
        "Extract structured fields from the job posting below and return ONE JSON "
        "object using EXACTLY these keys and allowed values (use \"unknown\" for "
        "unknown enums, null for unknown numbers; return ONLY the JSON, no prose):\n"
        "{\n"
        '  "title_canonical": "<normalized job title, required, non-empty>",\n'
        '  "category": one of [software_engineering, data_science, machine_learning, '
        "devops_infrastructure, product_management, design, marketing, sales, finance, "
        "legal, hr_people, operations, healthcare, education, academia_research, other],\n"
        '  "employment_type": one of [full_time, part_time, contract, internship, '
        "temporary, apprenticeship, freelance, unknown],\n"
        '  "workplace_type": one of [remote, onsite, hybrid, unknown],\n'
        '  "seniority": one of [intern, junior, mid, senior, staff, principal, director, unknown],\n'
        '  "experience_level": one of [entry, mid, senior, unknown],\n'
        '  "experience_min_years": <integer 0-40 or null>,\n'
        '  "visa_sponsorship": one of [yes, no, unknown],\n'
        '  "salary": {"min": <number or null>, "max": <number or null>, '
        '"currency": "<e.g. GBP or null>", "frequency": one of [hourly, daily, monthly, annual, unknown]},\n'
        '  "required_skills": ["..."],\n'
        '  "preferred_skills": ["..."],\n'
        '  "requirements_summary": "<=250 chars"\n'
        "}\n\n"
        f"Title: {job.title}\n"
        f"Company: {job.company}\n"
        f"Location: {job.location}\n"
        f"Description:\n{desc}\n"
    )


async def enrich_job(
    job: Job,
    *,
    llm_extract_validated_fn: Optional[LLMExtractFn] = None,
) -> JobEnrichment:
    """Call the LLM to structure a job posting.

    Args:
        job: the job to enrich.
        llm_extract_validated_fn: optional override — tests pass a mock here
            to avoid live HTTP. Defaults to the real
            :func:`llm_extract_validated`.

    Raises:
        StubDescriptionError: if the ad is too thin to read honestly (see below).
        RuntimeError: if all LLM providers fail or the response can't be
        validated into the `JobEnrichment` schema after the default retry
        budget. Callers should catch and log; they should NOT use a partial
        enrichment — the row stays absent rather than polluted.

    THE STUB BLOCK LIVES HERE, at the chokepoint, not in the callers.

    JOB SOURCE ENRICHMENT is CACHED: this function is idempotent per job_id and
    nothing re-reads a job that already has a row, so a fact invented from a
    teaser is permanent. Measured live 2026-08-17: a real 452-char Reed teaser
    that says NOTHING about working arrangements produced
    workplace_mode="onsite" — a confident fabrication that would have been
    stored as how:"llm" and never revisited.

    The new sweep checked this before calling; the OLD enrichment_sweep cron did
    not, so the same cache had two doors and only one was guarded. Putting the
    predicate here closes both, and any future caller inherits it — the same
    "one door, every job" reasoning the shelf gate is built on.
    """
    if is_stub_description(job.description, job.title):
        raise StubDescriptionError(
            f"description too thin to read honestly "
            f"({len((job.description or '').strip())} chars) — "
            f"recover the real text before enriching"
        )
    fn = llm_extract_validated_fn or llm_extract_validated
    prompt = _build_prompt(job)
    enrichment = await fn(prompt, JobEnrichment, _SYSTEM_PROMPT)
    return enrichment


async def enrich_batch(
    jobs: list[Job],
    *,
    semaphore_limit: int = 10,
    skip_existing: bool = True,
    conn: Optional[pg.Connection] = None,
    llm_extract_validated_fn: Optional[LLMExtractFn] = None,
) -> list[Optional[JobEnrichment]]:
    """Enrich a batch of jobs concurrently, bounded by a semaphore.

    Step-1 B7. Sequential ``enrich_job()`` calls would block ``run_search``
    on a 200-call cascade against the LLM provider chain. ``enrich_batch``
    parallelises with an ``asyncio.Semaphore`` (default ``semaphore_limit=10``)
    so one slow provider response doesn't head-of-line-block the rest.

    Per-job errors are caught and logged — one bad LLM response cannot kill
    the batch. The corresponding slot in the returned list is ``None``.

    Args:
        jobs: jobs to enrich. Order preserved in the result.
        semaphore_limit: max concurrent in-flight ``enrich_job`` calls.
        skip_existing: when True (default), call ``has_enrichment(conn, job.id)``
            before invoking the LLM and short-circuit to ``None`` if a row
            already exists. Requires ``conn``; if ``conn`` is ``None`` the
            skip check is bypassed (caller may dedupe upstream).
        conn: optional aiosqlite connection used by the ``skip_existing``
            check. ``None`` is fine for tests / one-shot batches.
        llm_extract_validated_fn: forwarded to each ``enrich_job`` call —
            tests inject a mock here to avoid live HTTP (rule #4).

    Returns:
        List of the same length as ``jobs``. ``None`` entries indicate
        enrichment was skipped (already enriched, validation failed, or
        the per-job LLM error was swallowed). Order matches input.
    """
    if not jobs:
        return []

    # Step-1 S3 — telemetry counter increments. Stays inert when
    # ENRICHMENT_ENABLED is false (CLAUDE.md rule #18) — callers gate the
    # entry into ``enrich_batch`` itself on the flag, so reaching this
    # point already implies the flag is ON.
    from src.utils.telemetry import enrichment_telemetry  # local import — keeps the dep cheap

    tel = enrichment_telemetry()

    sem = asyncio.Semaphore(semaphore_limit)

    async def _one(job: Job) -> Optional[JobEnrichment]:
        async with sem:
            if skip_existing and conn is not None:
                job_id = getattr(job, "id", None)
                if job_id is not None:
                    try:
                        if await has_enrichment(conn, job_id):
                            tel.cache_hits += 1
                            return None
                    except Exception as e:  # noqa: BLE001 — never block the batch on a DB hiccup
                        logger.warning(
                            "enrich_batch: has_enrichment check failed for job %s: %s",
                            job_id,
                            e,
                        )
            try:
                # NOT counted before the call. `enrich_job` refuses a stub
                # description BEFORE it reaches any provider, so incrementing
                # here made the counter report LLM calls that never happened —
                # a batch of stubs read as "3 calls made, 3 validation
                # failures" when the true answer is "0 calls, 3 skipped". The
                # counter is the thing we bill and alert on, so it has to count
                # requests, not attempts.
                result = await enrich_job(
                    job,
                    llm_extract_validated_fn=llm_extract_validated_fn,
                )
                tel.llm_calls += 1
                # B7-1 fix: persist successful enrichments. Without this,
                # every LLM call's result was discarded — pure cost, no value.
                if result is not None and conn is not None:
                    job_id = getattr(job, "id", None)
                    if job_id is not None:
                        try:
                            await save_enrichment(conn, job_id, result)
                        except Exception as e:  # noqa: BLE001
                            logger.warning(
                                "enrich_batch: save_enrichment failed for job %s: %s",
                                job_id,
                                e,
                            )
                return result
            except asyncio.TimeoutError:
                tel.timeouts += 1
                logger.warning(
                    "enrich_batch: enrich_job timed out for %s",
                    getattr(job, "id", job.title),
                )
                return None
            except StubDescriptionError as e:
                # A REFUSAL, not a failure. The ad was too thin to read
                # honestly, so no provider was called and nothing was
                # validated. Counting it as a validation failure would blame
                # the LLM for a decision made before it was asked, and would
                # hide the thing actually worth alerting on: how much of the
                # catalog arrives too thin to enrich.
                tel.stub_skipped += 1
                logger.info(
                    "enrich_batch: skipped %s — %s",
                    getattr(job, "id", job.title),
                    e,
                )
                return None
            except Exception as e:  # noqa: BLE001 — one bad job must not kill the batch
                tel.validation_failures += 1
                logger.warning(
                    "enrich_batch: enrich_job failed for %s: %s",
                    getattr(job, "id", job.title),
                    e,
                )
                return None

    return await asyncio.gather(*[_one(j) for j in jobs])


# ---------------------------------------------------------------------------
# Persistence helpers (shared-catalog table, no user_id column — rule #10)
# ---------------------------------------------------------------------------


async def has_enrichment(conn: pg.Connection, job_id: int) -> bool:
    """True if `job_enrichment` already has a row for this job."""
    cur = await conn.execute(
        "SELECT 1 FROM job_enrichment WHERE job_id = ? LIMIT 1",
        (job_id,),
    )
    row = await cur.fetchone()
    return row is not None


async def save_enrichment(
    conn: pg.Connection,
    job_id: int,
    enrichment: JobEnrichment,
) -> None:
    """Insert or replace the enrichment row for a given job.

    JSON-serialises every list/nested-model field. Uses `INSERT OR REPLACE`
    so re-enrichment is a clean upsert without requiring a DELETE first.

    ``employer_type`` and ``locations`` are RETIRED-BUT-PRESENT columns (see
    the module comment below) — deliberately absent from this column list.
    The table's `NOT NULL DEFAULT 'unknown'` / `DEFAULT '[]'` (migration
    0008) fills them on INSERT, and the `ON CONFLICT` clause never touches
    them again, so old rows keep whatever they already had. Measured on
    3,119 live enriched rows: `employer_type` was 100% 'unknown' and
    `locations` was 0% populated — the LLM has never once produced a usable
    value for either, so writing a constant from Python would just be a
    second place encoding the same dead default the DB already owns. DO NOT
    add these columns back to the INSERT/SELECT statements in this file
    without new evidence the LLM can actually decide them (see
    `job_enrichment_schema.py`'s module docstring and `_build_prompt`'s
    docstring above).
    """
    await conn.execute(
        """
        INSERT INTO job_enrichment (
            job_id, title_canonical, category, employment_type, workplace_type,
            salary, required_skills, preferred_skills,
            experience_min_years, experience_level, requirements_summary,
            language, visa_sponsorship, seniority,
            remote_region, apply_instructions, red_flags, enriched_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(job_id) DO UPDATE SET
            title_canonical = EXCLUDED.title_canonical,
            category = EXCLUDED.category,
            employment_type = EXCLUDED.employment_type,
            workplace_type = EXCLUDED.workplace_type,
            salary = EXCLUDED.salary,
            required_skills = EXCLUDED.required_skills,
            preferred_skills = EXCLUDED.preferred_skills,
            experience_min_years = EXCLUDED.experience_min_years,
            experience_level = EXCLUDED.experience_level,
            requirements_summary = EXCLUDED.requirements_summary,
            language = EXCLUDED.language,
            visa_sponsorship = EXCLUDED.visa_sponsorship,
            seniority = EXCLUDED.seniority,
            remote_region = EXCLUDED.remote_region,
            apply_instructions = EXCLUDED.apply_instructions,
            red_flags = EXCLUDED.red_flags,
            enriched_at = EXCLUDED.enriched_at
        """,
        (
            job_id,
            enrichment.title_canonical,
            enrichment.category.value,
            enrichment.employment_type.value,
            enrichment.workplace_type.value,
            enrichment.salary.model_dump_json(),
            json.dumps(enrichment.required_skills),
            json.dumps(enrichment.preferred_skills),
            enrichment.experience_min_years,
            enrichment.experience_level.value,
            enrichment.requirements_summary,
            enrichment.language,
            enrichment.visa_sponsorship.value,
            enrichment.seniority.value,
            enrichment.remote_region,
            enrichment.apply_instructions,
            json.dumps(enrichment.red_flags),
        ),
    )
    await conn.commit()


async def _build_enrichment_lookup(
    conn: pg.Connection,
) -> dict[int, JobEnrichment]:
    """Bulk-load every persisted ``job_enrichment`` row into a dict.

    Used by both the CLI ``run_search`` and the ARQ ``score_and_ingest`` worker
    to wire ``JobScorer(..., enrichment_lookup=...)`` (Pillar 2 Batch 2.9).
    The returned mapping is keyed by ``job_id`` (the same primary key as the
    parent ``jobs`` table). Call sites typically wrap it as
    ``lambda job: lookup.get(getattr(job, 'id', None))`` since
    ``JobScorer._enrichment_lookup`` expects a callable.

    Returns an empty dict when the table is empty or absent — callers should
    treat ``{}`` as "no enrichment available, fall back to legacy 4-component
    scoring" per CLAUDE.md rule #19.
    """
    # employer_type / locations deliberately excluded — see the module
    # comment on save_enrichment's column list above.
    try:
        cur = await conn.execute(
            """
            SELECT job_id, title_canonical, category, employment_type, workplace_type,
                   salary, required_skills, preferred_skills,
                   experience_min_years, experience_level, requirements_summary,
                   language, visa_sponsorship, seniority,
                   remote_region, apply_instructions, red_flags
            FROM job_enrichment
            """
        )
    except pg.OperationalError:
        # Table not yet migrated (e.g. fresh test DB without 0008). Return empty.
        return {}
    rows = await cur.fetchall()
    lookup: dict[int, JobEnrichment] = {}
    for row in rows:
        try:
            (
                job_id,
                title_canonical,
                category,
                employment_type,
                workplace_type,
                salary_json,
                required_json,
                preferred_json,
                experience_min_years,
                experience_level,
                requirements_summary,
                language,
                visa_sponsorship,
                seniority,
                remote_region,
                apply_instructions,
                red_flags_json,
            ) = row
            lookup[int(job_id)] = JobEnrichment(
                title_canonical=title_canonical,
                category=category,
                employment_type=employment_type,
                workplace_type=workplace_type,
                salary=json.loads(salary_json) if salary_json else {},
                required_skills=json.loads(required_json) if required_json else [],
                preferred_skills=json.loads(preferred_json) if preferred_json else [],
                experience_min_years=experience_min_years,
                experience_level=experience_level,
                requirements_summary=requirements_summary or "",
                language=language,
                visa_sponsorship=visa_sponsorship,
                seniority=seniority,
                remote_region=remote_region,
                apply_instructions=apply_instructions,
                red_flags=json.loads(red_flags_json) if red_flags_json else [],
            )
        except Exception as exc:  # noqa: BLE001 — never crash on a single bad row
            logger.warning("Skipping malformed job_enrichment row: %s", exc)
            continue
    return lookup


async def load_enrichment(
    conn: pg.Connection,
    job_id: int,
) -> Optional[JobEnrichment]:
    """Deserialise an enrichment row back into a `JobEnrichment` model.

    Used by the dedup tiebreaker in `services/deduplicator.py` and by the
    Batch 2.6 embedding builder.

    employer_type / locations deliberately excluded — see the module comment
    on save_enrichment's column list above.
    """
    cur = await conn.execute(
        """
        SELECT title_canonical, category, employment_type, workplace_type,
               salary, required_skills, preferred_skills,
               experience_min_years, experience_level, requirements_summary,
               language, visa_sponsorship, seniority,
               remote_region, apply_instructions, red_flags
        FROM job_enrichment
        WHERE job_id = ?
        """,
        (job_id,),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    (
        title_canonical,
        category,
        employment_type,
        workplace_type,
        salary_json,
        required_json,
        preferred_json,
        experience_min_years,
        experience_level,
        requirements_summary,
        language,
        visa_sponsorship,
        seniority,
        remote_region,
        apply_instructions,
        red_flags_json,
    ) = row
    return JobEnrichment(
        title_canonical=title_canonical,
        category=category,
        employment_type=employment_type,
        workplace_type=workplace_type,
        salary=json.loads(salary_json) if salary_json else {},
        required_skills=json.loads(required_json) if required_json else [],
        preferred_skills=json.loads(preferred_json) if preferred_json else [],
        experience_min_years=experience_min_years,
        experience_level=experience_level,
        requirements_summary=requirements_summary or "",
        language=language,
        visa_sponsorship=visa_sponsorship,
        seniority=seniority,
        remote_region=remote_region,
        apply_instructions=apply_instructions,
        red_flags=json.loads(red_flags_json) if red_flags_json else [],
    )
