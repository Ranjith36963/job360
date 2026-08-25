"""The two-pass JOB SOURCE ENRICHMENT sweep — step 3 of the Universal Shelf
(docs/pillars/UNIVERSAL_SHELF.md §2 fill chains, §6 dependency order, §7 cost).

JOB SOURCE ENRICHMENT is an LLM **reading a job ad** to extract facts about
the JOB: salary, seniority, workplace mode, visa position, employment type,
category. Those facts are the same for every user who ever sees that job, so
this is CATALOG work, not user work.

**WHY THIS IS AFFORDABLE AT ALL: the catalog is SHARED.** One job is read
once, and the six shelves it fills serve every user who ever matches against
it — today's users, and everyone who signs up afterwards. The bill scales with
how many JOBS we hold, never with how many PEOPLE we have. That is the whole
economic argument for reading ads with a paid model, and it is why rule #10
matters here: put a `user_id` anywhere near this and the cost becomes per-user
overnight.

WHY TWO PASSES. Measured on the live catalog 2026-08-17: visa_status was
filled on 1.6% of jobs, seniority 7.5%, category 7.5%, workplace 23.9%. Almost
no board states sponsorship as a FIELD — it lives in the ad's PROSE, which is
exactly what an LLM can read and a mapper cannot. But an LLM is the last
resort, not the first:

  PASS 1 — FREE. No LLM, no HTTP, no tokens. Re-runs the gate
  (`shelf_gate.fill_shelves`) over rows ALREADY in the catalog: the visa text
  detector, the deadline extractor, the closed-enum normaliser, the
  annualise-then-clamp salary rule. Most of this already runs at ingest, but
  every row stored BEFORE the gate existed never saw it, and nothing else
  goes back for them. Pass 1 also writes back any `job_enrichment` row we
  already PAID for whose facts never reached the catalog columns.

  PASS 2 — LLM, only on what survives three filters: the description is not a
  stub, at least N consumer shelves are still honestly absent, and there is no
  existing `job_enrichment` row (unless `force=True`).

**KNOW THIS BEFORE YOU EXPECT PASS 1 TO MOVE A NUMBER.** Simulated read-only
over the 2,915-job `shelf_after_verify` snapshot (2026-08-17), pass 1 filled
**zero** shelves on **zero** jobs. That is the gate working, not a bug: every
row in that catalog had already been through `fill_shelves` at ingest, so the
free detectors had already had their turn. Pass 1 pays off on exactly two
populations — rows stored BEFORE the gate was wired, and rows whose
already-paid-for `job_enrichment` facts never reached the `jobs` columns (prod
holds thousands of the latter). On a freshly-gated catalog the flat
visa/seniority/category numbers can ONLY be moved by pass 2.

THE STUB BLOCK IS THE EXPENSIVE ONE TO GET WRONG (§6). `enrich_job` is
idempotent per `job_id` — a second call is a no-op unless someone passes
`force=True` — so a confident answer read off a 453-char teaser is CACHED
PERMANENTLY. `shelf_gate.is_stub_description()` exists for exactly this and is
checked here before anything is sent.

COST CONTROL is not advisory. Two hard caps from settings (jobs and estimated
USD), both checked BEFORE each call, and a spend counter written to `run_log`
— because until this shipped, nothing in the system could answer "what did
last night cost?".

Rule #4: no live HTTP in tests — every LLM call goes through an injectable
`llm_extract_validated_fn`, so the suite runs offline with no keys (CI has none).
Rule #10: `job_enrichment` and the `jobs` shelves are shared catalog. No
`user_id` is read or written anywhere in this file.
"""
from __future__ import annotations

import json
import logging
import math
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from src.models import UNIVERSAL_SHELF, Job
from src.repositories import pg
from src.services.job_enrichment import (
    LLMExtractFn,
    _build_prompt,
    enrich_job,
    load_enrichment,
    save_enrichment,
)
from src.services.shelf_gate import (
    absent_shelves,
    apply_enrichment,
    fill_shelves,
    is_stub_description,
    shelf_has_value,
)

logger = logging.getLogger("job360.services.shelf_enrichment")

# Input tokens ARE measured — from the real prompt string `_build_prompt`
# builds — but measured with a divisor rather than a tokeniser: `tiktoken` is
# not a declared dependency of this project, and a budget rail that only works
# where an optional package happens to be installed is not a rail. 4.0 chars
# per token is the standard English approximation; calibrated against the
# 2026-08-17 dry run (2,472,222 real o200k_base tokens over 2,826 real catalog
# descriptions) it runs ~15% HIGH, which is the safe direction: the cap trips
# slightly early rather than slightly late.
_CHARS_PER_TOKEN = 4.0

# The `jobs` columns pass 1 and pass 2 may write. Deliberately explicit rather
# than a `SELECT *` round trip: this sweep must never be able to touch
# `description` (that is the description-backfill's job, and it is what the
# LLM READS), `title`, `company`, `apply_url` or any lifecycle column.
_WRITABLE_COLUMNS = (
    "employment_type",
    "workplace_mode",
    "seniority",
    "category",
    "visa_status",
    "salary_min",
    "salary_max",
    "salary_currency",
    "salary_period",
    "salary_min_gbp_annual",
    "salary_max_gbp_annual",
    "deadline",
    "deadline_source",
)

_SELECTED_COLUMNS = (
    "id", "title", "company", "location", "description", "apply_url", "source",
    "date_found", "employment_type", "workplace_mode", "seniority", "category",
    "visa_status", "salary_min", "salary_max", "salary_currency", "salary_period",
    "salary_min_gbp_annual", "salary_max_gbp_annual", "deadline", "deadline_source",
    "shelf_provenance",
)

# Which SHELF each writable column belongs to — the shelf name is what
# provenance is keyed by, and it is not always the column name (all five
# salary columns are the ONE `salary` shelf; both deadline columns are the one
# `deadline` shelf). Same distinction models.UNIVERSAL_SHELF documents.
_COLUMN_SHELF = {
    "employment_type": "employment_type",
    "workplace_mode": "workplace_mode",
    "seniority": "seniority",
    "category": "category",
    "visa_status": "visa_status",
    "salary_min": "salary",
    "salary_max": "salary",
    "salary_currency": "salary",
    "salary_period": "salary",
    "salary_min_gbp_annual": "salary",
    "salary_max_gbp_annual": "salary",
    "deadline": "deadline",
    "deadline_source": "deadline",
}

# Two spellings of the same column list: bare for a single-table SELECT, and
# `j.`-qualified for the two queries that JOIN `job_enrichment` (where `id`
# alone would be ambiguous). Built from ONE tuple so they can never drift.
_JOB_SELECT = ", ".join(_SELECTED_COLUMNS)
_JOB_SELECT_J = ", ".join(f"j.{c}" for c in _SELECTED_COLUMNS)


@dataclass
class SweepBudget:
    """Hard per-run ceilings. The sweep stops at whichever bites FIRST."""

    max_jobs: int
    max_spend_usd: float
    min_absent_shelves: int
    pass1_max_jobs: int
    input_usd_per_1m: float
    output_usd_per_1m: float
    output_tokens_per_job: int
    # Last, with a default, so every existing positional construction (tests,
    # ad-hoc callers) keeps working. Production always passes the real value
    # via `from_settings`; the default is deliberately far above any real run
    # so a caller that does not care about wall-clock is never capped by
    # surprise — the cap must be an explicit choice, not an accident.
    max_seconds: float = 86_400.0

    @classmethod
    def from_settings(cls) -> SweepBudget:
        from src.core import settings  # noqa: PLC0415 — read at call time, not import time

        return cls(
            max_jobs=settings.SHELF_ENRICHMENT_MAX_JOBS,
            max_spend_usd=settings.SHELF_ENRICHMENT_MAX_SPEND_USD,
            max_seconds=settings.SHELF_ENRICHMENT_MAX_SECONDS,
            min_absent_shelves=settings.SHELF_ENRICHMENT_MIN_ABSENT_SHELVES,
            pass1_max_jobs=settings.SHELF_ENRICHMENT_PASS1_MAX_JOBS,
            input_usd_per_1m=settings.LLM_INPUT_USD_PER_1M,
            output_usd_per_1m=settings.LLM_OUTPUT_USD_PER_1M,
            output_tokens_per_job=settings.LLM_OUTPUT_TOKENS_PER_JOB,
        )

    def cost_of(self, input_tokens: int) -> float:
        """USD for one job: measured input + the fixed-shape JSON output."""
        return (
            input_tokens * self.input_usd_per_1m
            + self.output_tokens_per_job * self.output_usd_per_1m
        ) / 1_000_000


@dataclass
class SweepStats:
    """Everything one sweep did, and what it cost. Serialised into `run_log`."""

    pass1_jobs_scanned: int = 0
    pass1_jobs_updated: int = 0
    pass1_shelves_filled: int = 0
    pass1_shelves_by_name: dict[str, int] = field(default_factory=dict)
    pass2_considered: int = 0
    pass2_blocked_stub: int = 0
    pass2_skipped_no_absent_shelf: int = 0
    pass2_enriched: int = 0
    # ATTEMPTS, not successes. Every cap below is a spend cap, and an LLM call
    # that failed was still made and still billed — so counting only successes
    # makes all three caps go dark in the exact failure mode they exist for
    # (every call failing: `pass2_enriched` stays 0, spend stays 0, and the
    # sweep grinds through every row paying for each one). (CodeRabbit, PR #388.)
    pass2_attempted: int = 0
    pass2_failed: int = 0
    pass2_shelves_filled: int = 0
    pass2_shelves_by_name: dict[str, int] = field(default_factory=dict)
    input_tokens: int = 0
    output_tokens: int = 0
    spend_usd: float = 0.0
    capped: bool = False
    cap_reason: Optional[str] = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "pass1_jobs_scanned": self.pass1_jobs_scanned,
            "pass1_jobs_updated": self.pass1_jobs_updated,
            "pass1_shelves_filled": self.pass1_shelves_filled,
            "pass1_shelves_by_name": self.pass1_shelves_by_name,
            "pass2_considered": self.pass2_considered,
            "pass2_blocked_stub": self.pass2_blocked_stub,
            "pass2_skipped_no_absent_shelf": self.pass2_skipped_no_absent_shelf,
            "pass2_enriched": self.pass2_enriched,
            "pass2_attempted": self.pass2_attempted,
            "pass2_failed": self.pass2_failed,
            "pass2_shelves_filled": self.pass2_shelves_filled,
            "pass2_shelves_by_name": self.pass2_shelves_by_name,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "spend_usd": round(self.spend_usd, 6),
            "capped": self.capped,
            "cap_reason": self.cap_reason,
        }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def estimate_input_tokens(prompt: str) -> int:
    """Estimated input tokens for a prompt. See `_CHARS_PER_TOKEN`."""
    return int(math.ceil(len(prompt) / _CHARS_PER_TOKEN))


# ---------------------------------------------------------------------------
# Row <-> Job
# ---------------------------------------------------------------------------


def _as_dict(value: Any) -> dict[str, Any]:
    """`shelf_provenance` as a dict, whatever shape the driver handed back.

    The column is JSONB, which psycopg decodes for us — but a legacy row, a
    shim path or a plain-TEXT test schema can all hand back the raw string, so
    both shapes are accepted rather than assumed.
    """
    if isinstance(value, (str, bytes)):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return {}
    return value if isinstance(value, dict) else {}


def _job_from_row(row: Any) -> Job:
    """Rebuild the catalog row as a `Job` so both gate entry points can run."""
    provenance = _as_dict(row["shelf_provenance"])

    job = Job(
        title=row["title"] or "",
        company=row["company"] or "",
        apply_url=row["apply_url"] or "",
        source=row["source"] or "",
        date_found=row["date_found"] or "",
        location=row["location"] or "",
        description=row["description"] or "",
    )
    job.id = row["id"]
    job.salary_min = row["salary_min"]
    job.salary_max = row["salary_max"]
    job.salary_currency = row["salary_currency"]
    job.salary_period = row["salary_period"]
    job.salary_min_gbp_annual = row["salary_min_gbp_annual"]
    job.salary_max_gbp_annual = row["salary_max_gbp_annual"]
    job.employment_type = row["employment_type"]
    job.workplace_mode = row["workplace_mode"]
    job.seniority = row["seniority"]
    job.category = row["category"]
    job.visa_status = row["visa_status"]
    job.deadline = row["deadline"]
    job.deadline_source = row["deadline_source"]
    job.shelf_provenance = provenance
    return job


def _filled_shelves(job: Job) -> set[str]:
    """Every UNIVERSAL SHELF currently holding a real value.

    ALL 13, not just the LLM-fillable six — otherwise the "how many shelves did
    the free pass fill?" number would silently omit `deadline`, which pass 1
    fills from the ad text and which the LLM is forbidden to touch at all (§2).
    Counting only what the LLM could have done would make the free pass look
    like it earned less than it did.
    """
    return {shelf for shelf in UNIVERSAL_SHELF if shelf_has_value(job, shelf)}


async def _write_back(conn: pg.Connection, row: Any, job: Job) -> bool:
    """Persist the shelves this sweep changed. **Only ever ADDS.**

    A pass that re-derives values could in principle turn a stored value back
    into NULL (a currency `core/fx` cannot price, a band that fails the
    plausibility clamp). Deleting a value the catalog already had is not this
    sweep's job — it would be a silent data loss disguised as a maintenance
    run — so any column whose new value is NULL while the stored value is not
    keeps the stored value.

    And keeping the VALUE means keeping its PROVENANCE with it. Restoring the
    number while leaving the fresh `absent:implausible` stamp in place would
    produce the one state provenance exists to make impossible: a shelf with a
    value sitting under a note saying it has none. The prior entry is restored
    where there is one; where there is not (a pre-gate row, whose columns could
    only ever have been written by a source mapper) the shelf is stamped
    `source`, which is what actually happened.

    Returns True if anything was written.
    """
    prior = _as_dict(row["shelf_provenance"])
    updates: dict[str, Any] = {}
    restored: set[str] = set()
    for column in _WRITABLE_COLUMNS:
        new_value = getattr(job, column, None)
        old_value = row[column]
        if new_value is None and old_value is not None:
            setattr(job, column, old_value)  # keep the job object honest too
            restored.add(_COLUMN_SHELF[column])
            continue
        if new_value != old_value:
            updates[column] = new_value

    if restored:
        provenance = dict(job.shelf_provenance or {})
        for shelf in restored:
            entry = prior.get(shelf)
            if isinstance(entry, dict) and entry.get("how") in ("source", "derived", "llm"):
                provenance[shelf] = entry
            else:
                provenance[shelf] = {"how": "source", "field": shelf, "at": _now_iso()}
        job.shelf_provenance = provenance

    provenance_json = json.dumps(job.shelf_provenance or {}, sort_keys=True)
    provenance_changed = provenance_json != json.dumps(_as_dict(row["shelf_provenance"]), sort_keys=True)
    if not updates and not provenance_changed:
        return False

    columns = list(updates.keys()) + ["shelf_provenance"]
    values = list(updates.values()) + [provenance_json]
    assignments = ", ".join(f"{c} = ?" for c in columns)
    await conn.execute(
        f"UPDATE jobs SET {assignments} WHERE id = ?",  # noqa: S608 — column names are a module constant
        (*values, row["id"]),
    )
    return True


def _count(counter: dict[str, int], shelves: Any) -> None:
    for shelf in shelves:
        counter[shelf] = counter.get(shelf, 0) + 1


# ---------------------------------------------------------------------------
# PASS 1 — FREE
# ---------------------------------------------------------------------------


async def run_pass1(conn: pg.Connection, *, limit: int, stats: SweepStats) -> None:
    """Fill everything that can be filled at ZERO cost, on rows already stored.

    Two candidate groups, both bounded by `limit`:

      A. Rows the gate has NEVER seen — `shelf_provenance` is still the empty
         default. Every row stored before the gate was wired is in here, and
         nothing else was ever going to go back for them. Self-terminating:
         after this pass the row has provenance, so it stops being selected.

      B. Rows we ALREADY PAID to enrich whose facts never reached the catalog
         columns. `job_enrichment` is the LLM layer's own store; the write-back
         into `jobs` is what makes those facts visible to filters, the
         serializer and the scorer (§3 "Where LLM values land"). Newest first,
         because a row whose enrichment says `unknown` everywhere will keep
         being re-selected and the newest rows are the ones users see.

    Costs: DB reads and writes. No LLM call, no HTTP, no tokens — verified by
    the fact that this function does not import or reference `enrich_job`.

    The per-row `load_enrichment` round trip is deliberate rather than a bulk
    prefetch: `fill_shelves` is pure CPU (regexes over up to 4 KB of ad text
    each), and a few thousand of those in a tight loop with no await would
    block the worker's event loop the way a previous catalog-scale backfill
    did. The await per row keeps the loop breathing, and it keeps peak memory
    to one enrichment rather than the whole batch.
    """
    if limit <= 0:
        return

    conn.row_factory = pg.Row

    cur = await conn.execute(
        f"""
        SELECT {_JOB_SELECT} FROM jobs
        WHERE cast(shelf_provenance AS TEXT) IN ('{{}}', '', 'null')
        ORDER BY id DESC
        LIMIT ?
        """,  # noqa: S608 — _JOB_SELECT is a module constant, no user input
        (limit,),
    )
    rows = list(await cur.fetchall())
    seen = {row["id"] for row in rows}

    remaining = limit - len(rows)
    if remaining > 0:
        cur = await conn.execute(
            f"""
            SELECT {_JOB_SELECT_J} FROM jobs j
            JOIN job_enrichment e ON e.job_id = j.id
            WHERE j.employment_type IS NULL OR j.seniority IS NULL
               OR j.workplace_mode IS NULL OR j.category IS NULL
               OR j.visa_status IS NULL OR j.visa_status = 'unknown'
               OR (j.salary_min IS NULL AND j.salary_max IS NULL)
            ORDER BY j.id DESC
            LIMIT ?
            """,  # noqa: S608 — _JOB_SELECT_J is a module constant, no user input
            (remaining,),
        )
        for row in await cur.fetchall():
            if row["id"] not in seen:
                rows.append(row)
                seen.add(row["id"])

    for row in rows:
        stats.pass1_jobs_scanned += 1
        try:
            job = _job_from_row(row)
            before = _filled_shelves(job)

            # The gate's own free detectors: visa text, deadline text, enum
            # normalisation, annualise-then-clamp salary.
            fill_shelves(job)

            # Already-paid-for LLM facts, written back through the SAME
            # never-overwrite rule as a fresh enrichment (rule #29 / §2).
            enrichment = await load_enrichment(conn, row["id"])
            if enrichment is not None:
                apply_enrichment(job, enrichment, by=_model_label())

            gained = _filled_shelves(job) - before
            if await _write_back(conn, row, job):
                stats.pass1_jobs_updated += 1
            stats.pass1_shelves_filled += len(gained)
            _count(stats.pass1_shelves_by_name, gained)
        except Exception:  # noqa: BLE001 — one poison row must never abort the sweep
            logger.warning(
                "shelf_enrichment_pass1_failed",
                extra={"event": "shelf_enrichment_pass1_failed", "job_id": row["id"]},
                exc_info=True,
            )

    await conn.commit()


def _model_label() -> str:
    """The model name recorded in provenance as `by`.

    Reads settings at CALL time so an `OPENAI_MODEL` override is reflected in
    the audit trail rather than frozen at import.
    """
    from src.core import settings  # noqa: PLC0415

    return getattr(settings, "OPENAI_MODEL", "gpt-4o-mini") or "gpt-4o-mini"


# ---------------------------------------------------------------------------
# PASS 2 — the LLM, on what survives
# ---------------------------------------------------------------------------


async def run_pass2(
    conn: pg.Connection,
    *,
    budget: SweepBudget,
    stats: SweepStats,
    force: bool = False,
    llm_extract_validated_fn: Optional[LLMExtractFn] = None,
) -> None:
    """Read the ads that are worth reading, and write only into the holes.

    Sequential on purpose. `enrich_batch` runs jobs concurrently, which is
    right when the budget is a fixed job count decided up front — but the
    SPEND cap here has to be checked against the running total immediately
    before each call, and a fan-out of ten in-flight calls cannot do that
    without overshooting by up to ten jobs. One at a time makes the cap exact.

    The three filters, in the order they cost least to apply:
      1. no existing `job_enrichment` row (SQL — unless `force`);
      2. description is not a stub (`shelf_gate.is_stub_description`) — this
         is the fabrication guard, and the one that would cost real money to
         get wrong because the wrong answer is cached permanently;
      3. at least `min_absent_shelves` consumer shelves honestly absent.
    """
    if budget.max_jobs <= 0 or budget.max_spend_usd <= 0:
        stats.capped = True
        stats.cap_reason = "budget_zero"
        logger.warning(
            "JOB SOURCE ENRICHMENT: pass 2 skipped — budget is zero "
            "(max_jobs=%s, max_spend_usd=%s). No ad was read.",
            budget.max_jobs,
            budget.max_spend_usd,
        )
        return

    conn.row_factory = pg.Row

    # Over-select: filters 2 and 3 run in Python (they need the gate), so the
    # SQL cannot know how many rows survive. 3x the job cap is the same
    # over-sample ratio the measured catalog justifies (96.9% of rows were
    # eligible), with headroom for a batch that is mostly stubs.
    fetch_limit = max(budget.max_jobs * 3, budget.max_jobs)
    join = "LEFT JOIN job_enrichment e ON e.job_id = j.id"
    where = "e.job_id IS NULL" if not force else "1 = 1"
    cur = await conn.execute(
        f"""
        SELECT {_JOB_SELECT_J} FROM jobs j
        {join}
        WHERE {where}
        ORDER BY j.date_found DESC, j.id DESC
        LIMIT ?
        """,  # noqa: S608 — every interpolated part is a module constant
        (fetch_limit,),
    )
    rows = await cur.fetchall()

    model = _model_label()

    # WALL-CLOCK GUARD — the third cap, and the only one that tracks reality.
    #
    # max_jobs and max_spend_usd both assume a call takes about as long as the
    # last one did. Measured live 2026-08-17, that assumption fails badly: a
    # healthy call runs 2-4s, but with a dead provider key the retry cascade
    # took ~120s PER JOB. So a cap of 500 jobs is somewhere between 25 minutes
    # and 16 hours, and `refresh_catalog` gets 600s total (workers/settings.py:175)
    # of which the source fan-out already measured ~430s.
    #
    # Being killed is not a graceful truncation here. ARQ has retry_jobs on with
    # max_tries=5, so an overrun re-runs the WHOLE task — re-fetching every
    # source and re-spending — up to five times. Stopping ourselves a little
    # early turns a 5x blowout into "fewer ads read tonight, the rest tomorrow",
    # which is exactly the trade Reed's fetch guard already makes in this repo.
    _deadline = time.monotonic() + budget.max_seconds

    for row in rows:
        # ATTEMPTS here too. Gating on successes meant that in a run where
        # every call failed, this never armed either — so the time budget joined
        # the other two in going dark exactly when it was needed.
        if stats.pass2_attempted and (time.monotonic() >= _deadline):
            # `stats.pass2_enriched` gates it so the FIRST ad is always attempted:
            # a guard that can fire before any work turns a misconfigured budget
            # into a permanently silent sweep, which is the failure it exists to
            # prevent.
            stats.capped = True
            stats.cap_reason = "time_budget"
            logger.warning(
                "JOB SOURCE ENRICHMENT: stopping at %s ads — %.0fs wall-clock "
                "budget spent. The remainder is not lost, it is deferred to the "
                "next sweep.",
                stats.pass2_attempted,
                budget.max_seconds,
            )
            break

        stats.pass2_considered += 1

        if is_stub_description(row["description"], row["title"]):
            # HARD BLOCK (§6). Never sent, at any budget, for any reason: the
            # answer would be a fabrication and `enrich_job` caches it forever.
            stats.pass2_blocked_stub += 1
            continue

        job = _job_from_row(row)
        if not job.shelf_provenance:
            # A row pass 1 did not reach. Run the free pass first so "absent"
            # means honestly absent rather than merely unexamined.
            fill_shelves(job)

        absent = absent_shelves(job)
        if len(absent) < budget.min_absent_shelves:
            stats.pass2_skipped_no_absent_shelf += 1
            continue

        input_tokens = estimate_input_tokens(_build_prompt(job))
        cost = budget.cost_of(input_tokens)

        # `break`, not `return`: the commit below still has to run. Every
        # enriched job already committed individually, but leaving the
        # function through a different door than the happy path is exactly how
        # a "we hit the cap" branch quietly grows a data-loss bug later.
        if stats.pass2_attempted >= budget.max_jobs:
            _log_cap(stats, "max_jobs", budget, rows_left=len(rows) - stats.pass2_considered + 1)
            break
        if stats.spend_usd + cost > budget.max_spend_usd:
            _log_cap(stats, "max_spend_usd", budget, rows_left=len(rows) - stats.pass2_considered + 1)
            break

        # BILLED AT THE BOUNDARY, BEFORE THE CALL CAN FAIL. These three lines
        # used to sit after the `except ... continue` below, so a failed call
        # cost money and moved no counter. The comment that stood there —
        # "the call happened, so it is billable whatever we do with the answer"
        # — was exactly right and was in the one place that could not act on it.
        stats.pass2_attempted += 1
        stats.input_tokens += input_tokens
        stats.output_tokens += budget.output_tokens_per_job
        stats.spend_usd += cost

        try:
            enrichment = await enrich_job(job, llm_extract_validated_fn=llm_extract_validated_fn)
        except Exception:  # noqa: BLE001 — one bad ad must never abort the sweep
            stats.pass2_failed += 1
            logger.warning(
                "shelf_enrichment_llm_failed",
                extra={"event": "shelf_enrichment_llm_failed", "job_id": row["id"]},
                exc_info=True,
            )
            continue

        stats.pass2_enriched += 1

        try:
            await save_enrichment(conn, row["id"], enrichment)
            _job, filled = apply_enrichment(job, enrichment, by=model)
            await _write_back(conn, row, job)
            await conn.commit()
            stats.pass2_shelves_filled += len(filled)
            _count(stats.pass2_shelves_by_name, filled)
        except Exception:  # noqa: BLE001 — a write failure must not lose the rest of the sweep
            logger.warning(
                "shelf_enrichment_write_failed",
                extra={"event": "shelf_enrichment_write_failed", "job_id": row["id"]},
                exc_info=True,
            )

    await conn.commit()


def _log_cap(stats: SweepStats, reason: str, budget: SweepBudget, *, rows_left: int) -> None:
    """Say it LOUDLY. A cap that trims silently is a cap nobody can act on.

    `refresh_catalog`'s fan-out cap already learned this lesson: the failure
    mode is not the cap itself, it is discovering months later that a nightly
    run has been quietly dropping most of its work.
    """
    stats.capped = True
    stats.cap_reason = reason
    logger.error(
        "JOB SOURCE ENRICHMENT CAPPED (%s): read %s ad(s) for an estimated $%.4f "
        "(caps: %s jobs / $%.2f). ~%s eligible job(s) were NOT read this run and "
        "will be retried next run. Raise SHELF_ENRICHMENT_MAX_JOBS / "
        "SHELF_ENRICHMENT_MAX_SPEND_USD to go further.",
        reason,
        stats.pass2_enriched,
        stats.spend_usd,
        budget.max_jobs,
        budget.max_spend_usd,
        max(0, rows_left),
    )


# ---------------------------------------------------------------------------
# The spend counter — run_log
# ---------------------------------------------------------------------------


async def log_sweep_run(conn: pg.Connection, stats: SweepStats) -> None:
    """Write one `run_log` row recording what this sweep did AND what it cost.

    Until this shipped, NOTHING in the system could answer "what did last
    night cost?" — enrichment spend left no trace anywhere. The fetch counters
    are zero on purpose: this run fetched no jobs, and inflating them would
    corrupt the catalog-health numbers every other reader of `run_log` uses.
    The `run_uuid` prefix makes the row greppable
    (`WHERE run_uuid LIKE 'shelf-enrichment-%'`).

    Never raises: a bookkeeping failure must not fail a sweep that already
    did its work.
    """
    try:
        await conn.execute(
            "INSERT INTO run_log ("
            " timestamp, total_found, new_jobs, sources_queried, per_source,"
            " run_uuid, enrichment_stats"
            ") VALUES (?, 0, 0, 0, '{}', ?, ?)",
            (
                datetime.now(timezone.utc).isoformat(),
                f"shelf-enrichment-{uuid.uuid4()}",
                json.dumps(stats.as_dict()),
            ),
        )
        await conn.commit()
    except Exception:  # noqa: BLE001
        logger.warning(
            "shelf_enrichment_run_log_failed",
            extra={"event": "shelf_enrichment_run_log_failed"},
            exc_info=True,
        )


# ---------------------------------------------------------------------------
# The sweep
# ---------------------------------------------------------------------------


async def run_shelf_enrichment_sweep(
    conn: pg.Connection,
    *,
    budget: Optional[SweepBudget] = None,
    force: bool = False,
    llm_extract_validated_fn: Optional[LLMExtractFn] = None,
) -> dict[str, Any]:
    """Run pass 1 (free) then pass 2 (LLM, capped), and record the spend.

    The CALLER owns the feature flag. `workers/tasks.refresh_catalog` gates
    this whole call on `ENGINE2_ENABLED OR ENRICHMENT_ENABLED` (rule #18 —
    BOTH names), so with the flags off this function is never entered and
    behaviour is byte-identical to before it existed. Pass 1 spends nothing
    and could reasonably run un-gated one day; that would be its own decision
    with its own flag, not a side effect of this file.

    Args:
        conn: the worker's catalog connection. Shared catalog — rule #10.
        budget: override for tests; production reads settings.
        force: re-read ads that already have a `job_enrichment` row. Costs
            real money and overwrites cached answers — for repairing rows
            enriched from text that has since been recovered.
        llm_extract_validated_fn: injected LLM callable (rule #4 — tests pass
            a mock; CI has no LLM keys).

    Returns the `SweepStats` dict, which is also what lands in `run_log`.
    """
    budget = budget or SweepBudget.from_settings()
    stats = SweepStats()

    # try/finally, NOT a straight line. `log_sweep_run` used to sit after pass 2,
    # so a run that was cancelled part-way recorded NOTHING — proven live
    # 2026-08-17 when a killed sweep had already enriched 16 ads and left zero
    # rows in `run_log`. That is the worst possible failure for a spend counter:
    # it goes blind on exactly the expensive nights, because the nights that
    # overrun are the nights that get cancelled.
    #
    # ARQ makes it concrete. `refresh_catalog` runs under job_timeout=600s
    # (workers/settings.py:175) and the source fan-out alone measured ~430s, so
    # pass 2 inherits ~170s — while the default cap allows 500 SEQUENTIAL LLM
    # calls. With retry_jobs on and max_tries=5 (:196) an overrun does not just
    # truncate: it re-runs the whole task up to five times, re-fetching every
    # source and re-spending, with no ledger. The wall-clock guard inside pass 2
    # is what stops that; this block guarantees the accounting survives even if
    # something still kills us.
    try:
        await run_pass1(conn, limit=budget.pass1_max_jobs, stats=stats)
        await run_pass2(
            conn,
            budget=budget,
            stats=stats,
            force=force,
            llm_extract_validated_fn=llm_extract_validated_fn,
        )
    finally:
        # Best-effort: a failure to WRITE the ledger must not mask the original
        # error that got us here.
        try:
            await log_sweep_run(conn, stats)
        except Exception:  # noqa: BLE001 — ledger is telemetry, never the story
            logger.exception("JOB SOURCE ENRICHMENT: failed to record sweep spend")

    logger.info(
        "JOB SOURCE ENRICHMENT sweep: pass1 filled %s shelf/shelves on %s row(s) for $0.00; "
        "pass2 read %s ad(s) (blocked %s stub(s)) filling %s shelf/shelves for an estimated $%.4f",
        stats.pass1_shelves_filled,
        stats.pass1_jobs_updated,
        stats.pass2_enriched,
        stats.pass2_blocked_stub,
        stats.pass2_shelves_filled,
        stats.spend_usd,
    )
    return stats.as_dict()
