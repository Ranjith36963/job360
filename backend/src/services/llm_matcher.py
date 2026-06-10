"""LLM matcher — the "judge" of the funnel->judge scoring path.

The keyword engine cheaply shortlists; this module sends each shortlisted job
plus the user's FULL profile (CV + LinkedIn + GitHub merged into cv_data, plus
preferences) to the shared Gemini->Groq->Cerebras chain and persists a per-user
fit verdict onto ``user_feed`` (per-user state — rules #10/#17 keep the shared
catalog tables untouched).

Honest limits, by measurement (scripts/score_enrichment_accuracy.py):
  * fit verdicts measured 10/10 on the labeled sample;
  * the LLM drops structured salaries — this module NEVER writes salary;
    salary stays sourced from the job row / enrichment.

Rule #4: tests inject ``llm_extract_validated_fn``. Rule #18 analog:
``MATCHER_ENABLED`` defaults off; callers gate on the flag, so OFF is a
byte-identical no-op.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Awaitable
from typing import Callable

import aiosqlite
from pydantic import BaseModel, Field

from src.models import Job
from src.services.profile.llm_provider import llm_extract_validated

logger = logging.getLogger("job360.services.llm_matcher")

MATCHER_ENABLED = os.getenv("MATCHER_ENABLED", "false").lower() in {"1", "true", "yes", "on"}
# Loose funnel on purpose: the judge can rescue a mediocre keyword score, but
# it can never see a job the funnel discarded. 30 == the MIN_MATCH_SCORE floor.
MATCHER_THRESHOLD = int(os.getenv("MATCHER_THRESHOLD", "30"))
MATCHER_MAX_JOBS = int(os.getenv("MATCHER_MAX_JOBS", "30"))


class MatchVerdict(BaseModel):
    """The judge's output. ``fit_score`` is the per-user score; ``verdict`` is
    a <=8-word human label; ``reason`` names the deciding dimension."""

    fit_score: int = Field(ge=0, le=100)
    verdict: str = ""
    reason: str = ""


LLMExtractFn = Callable[[str, type, str], Awaitable[MatchVerdict]]
# Type alias used in annotations below — avoids repeating Optional[LLMExtractFn]
_OptLLMFn = LLMExtractFn | None

_SYSTEM_PROMPT = (
    "You are a precise job-fit judge. Return ONLY valid JSON matching the "
    "schema. No prose."
)

# Rubric validated in scripts/compare_enrichment_levels.py (matcher = 10/10
# fit-bucket accuracy on the labeled sample).
_MATCH_RUBRIC = (
    "Score the candidate-job fit 0-100 by weighing FOUR dimensions: "
    "(1) domain/role match, (2) seniority fit vs the candidate's level, "
    "(3) skills overlap, (4) location/remote + visa fit. "
    'Return ONLY JSON: {"fit_score": <0-100 int>, "verdict": "<=8 words", '
    '"reason": "<=200 chars naming the deciding dimension"}.'
)


def profile_to_matcher_text(profile) -> str:
    """The permanent 'left side': titles + skills + summary from cv_data
    (LinkedIn/GitHub are already merged into cv_data by the upload routes)
    plus explicit preferences."""
    cv = getattr(profile, "cv_data", None)
    prefs = getattr(profile, "preferences", None)
    titles = ", ".join(getattr(cv, "job_titles", []) or []) if cv else ""
    skills = ", ".join((getattr(cv, "skills", []) or [])[:30]) if cv else ""
    summary = (getattr(cv, "summary", "") or "")[:600] if cv else ""
    pref_bits = []
    if prefs:
        for attr in ("experience_level", "work_arrangement"):
            v = getattr(prefs, attr, None)
            if v:
                pref_bits.append(f"{attr}={v}")
        smin = getattr(prefs, "salary_min", None)
        if smin:
            pref_bits.append(f"salary_min={smin}")
    return (
        f"Target titles: {titles}\nSkills: {skills}\nSummary: {summary}\n"
        f"Preferences: {', '.join(pref_bits)}"
    )


def _build_match_prompt(profile_txt: str, job: Job, facts: dict | None) -> str:
    """Assemble the full judge prompt: rubric + candidate profile + job facts + description."""
    hint = ""
    if facts:
        hint = (
            "\nPRE-EXTRACTED FACTS about this job (use them, but trust the "
            f"description if they conflict): {json.dumps(facts, default=str)}\n"
        )
    return (
        f"{_MATCH_RUBRIC}\n\n"
        f"CANDIDATE PROFILE:\n{profile_txt}\n{hint}\n"
        f"JOB:\nTitle: {job.title}\nCompany: {job.company}\nLocation: {job.location}\n"
        f"Description:\n{(job.description or '')[:3500]}\n"
    )


def _facts_hint(job: Job, enrichment) -> dict | None:
    """Cheap deterministic hints for the judge. Enrichment wins when present;
    salary always comes from structured fields (never from an LLM)."""
    facts: dict = {}
    if enrichment is not None:
        facts["seniority"] = getattr(enrichment.seniority, "value", enrichment.seniority)
        facts["workplace_type"] = getattr(enrichment.workplace_type, "value", enrichment.workplace_type)
        facts["visa_sponsorship"] = getattr(enrichment.visa_sponsorship, "value", enrichment.visa_sponsorship)
    lvl = getattr(job, "experience_level", "") or ""
    if "seniority" not in facts and lvl:
        facts["seniority"] = lvl
    smin = getattr(job, "salary_min", None)
    smax = getattr(job, "salary_max", None)
    if smin or smax:
        facts["salary"] = {"min": smin, "max": smax}
    return facts or None


async def match_job(
    profile_txt: str,
    job: Job,
    facts: dict | None,
    *,
    llm_extract_validated_fn: _OptLLMFn = None,
) -> MatchVerdict:
    """One judge call. Raises on total provider failure — callers catch."""
    fn = llm_extract_validated_fn or llm_extract_validated
    prompt = _build_match_prompt(profile_txt, job, facts)
    return await fn(prompt, MatchVerdict, _SYSTEM_PROMPT)


async def has_verdict(conn: aiosqlite.Connection, user_id: str, job_id: int) -> bool:
    """True if this user's feed row for the job already carries a verdict."""
    cur = await conn.execute(
        "SELECT 1 FROM user_feed WHERE user_id = ? AND job_id = ? "
        "AND llm_matched_at IS NOT NULL LIMIT 1",
        (user_id, job_id),
    )
    return (await cur.fetchone()) is not None


async def save_verdict(
    conn: aiosqlite.Connection, user_id: str, job_id: int, verdict: MatchVerdict
) -> None:
    """Persist the judge's verdict onto the user's feed row (per-user state)."""
    await conn.execute(
        "UPDATE user_feed SET llm_fit_score = ?, llm_verdict = ?, llm_reason = ?, "
        "llm_matched_at = datetime('now') WHERE user_id = ? AND job_id = ?",
        (verdict.fit_score, verdict.verdict, verdict.reason, user_id, job_id),
    )
    await conn.commit()


async def match_batch(
    jobs: list[Job],
    *,
    user_id: str,
    profile_text: str,
    conn: aiosqlite.Connection,
    semaphore_limit: int = 3,
    skip_existing: bool = True,
    llm_extract_validated_fn: _OptLLMFn = None,
) -> list[MatchVerdict | None]:
    """Judge a shortlist concurrently (bounded — free tiers cap ~30 req/min).
    Per-job errors are swallowed; one bad LLM response can't kill the batch.
    Persists each success onto the user's user_feed row. Order preserved."""
    if not jobs:
        return []
    sem = asyncio.Semaphore(semaphore_limit)

    async def _one(job: Job) -> MatchVerdict | None:
        job_id = getattr(job, "id", None)
        if job_id is None:
            return None
        async with sem:
            try:
                if skip_existing and await has_verdict(conn, user_id, job_id):
                    return None
                enrichment = None
                try:
                    from src.services.job_enrichment import (  # noqa: PLC0415
                        load_enrichment,
                    )

                    enrichment = await load_enrichment(conn, job_id)
                except Exception:  # noqa: BLE001 — hints are optional
                    enrichment = None
                verdict = await match_job(
                    profile_text,
                    job,
                    _facts_hint(job, enrichment),
                    llm_extract_validated_fn=llm_extract_validated_fn,
                )
                await save_verdict(conn, user_id, job_id, verdict)
                return verdict
            except Exception as e:  # noqa: BLE001 — judge failure must not kill the run
                logger.warning("match_batch: judge failed for job %s: %s", job_id, e)
                return None

    return await asyncio.gather(*[_one(j) for j in jobs])
