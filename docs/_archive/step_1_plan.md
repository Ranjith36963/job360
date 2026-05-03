# Step 1 — Batch S1 (Engine→API Seam) — Ralph-Loop-Driven Execution Plan

> **Status:** Approved 2026-04-24. Mirror of `.claude/plans/now-i-ve-got-another-crispy-island.md`. Hand this file to the generator worktree to implement, and to the reviewer worktree to audit.
>
> **Pre-reqs carried from Step 0:** `main @ 51d5c07`, 1,018p/0f/3s baseline, `worktree-generator` + `worktree-reviewer` positioned at `4d81397` (will fast-forward on iteration 1).

---

## Context

**Why this change is being made.** Pillar 2 and Pillar 3 Batch 1 shipped rich engine data — 5-column date model, 7-dimension scoring, LLM enrichment schema, sentence-transformer embeddings, RRF hybrid retrieval — but the API serialisation layer discards almost all of it. Today's `GET /jobs` returns `match_score` plus placeholder zeros for the 8 dimension fields; zero date-model fields; zero enrichment fields. `?mode=hybrid` is accepted as a query parameter, assigned to `_`, and does nothing (`backend/src/api/routes/jobs.py:129`).

**What prompted it.** Step 0 pre-flight closed the reproducibility + onboarding gaps. Step 1 is the first step where engine value becomes visible to the frontend. Without it, Step 2 (UI polish) would be binding React components to zero-valued fields — the wrong layer to debug. Two parallel audit rounds (6 sub-agents × 2 waves = 12 explorations on `main @ 51d5c07`) surfaced **12 critical blockers**, one of which (`insert_job` silently overwriting `first_seen_at` / `last_seen_at` via raw SQL — `database.py:173,175,176`) is a latent data-loss bug that corrupts staleness detection for every write.

**Intended outcome.** After Step 1 completes:
- `bootstrap_dev.py` returns a `JobResponse` with all 7 non-zero scoring dimensions, all 5 date-model fields populated, and (when `ENRICHMENT_ENABLED=true`) enriched fields for high-scored jobs
- `?mode=hybrid` returns fused results when the embedding index is populated and cleanly falls back to keyword when it isn't
- CLI `run_search` and ARQ `score_and_ingest` paths produce **identical** downstream data — no divergence between dogfood and authenticated multi-user
- The `insert_job` silent-overwrite bug is closed
- Ghost-expired jobs no longer leak through `get_recent_jobs`
- Concurrent boot (API + worker) no longer races on migration application
- Per-user rate-limit prevents LLM-quota exhaustion via `/search` spam
- Test suite is green at ≥1,018p/0f/3s (likely +25–40 new tests); no regressions
- Every subsequent step can trust that the engine→API seam is honest

---

## Strategic context — why Ralph Loop for S1

Step 1 is a **wiring batch, not a feature batch**. Round-2 audits revealed an "infrastructure ready but never invoked" pattern in three places: `enrich_job_task()` exists but isn't registered, `JobScorer` multi-dim kwargs exist but no caller passes them, `VectorIndex.upsert()` exists but is never called. Wiring batches have two dangerous properties:

1. **Silent success modes.** The code compiles, the test suite stays green, but nothing actually lights up in production. The only proof is end-to-end behavioural verification.
2. **Sequential dependencies.** Unlike Step 0 (12 mostly-independent edits), S1 has real dependencies — `ScoreBreakdown` dict must exist before multi-dim callers can pass `enrichment_lookup`, which must exist before `_row_to_job_response` can populate dim fields, which must exist before frontend `types.ts` can consume them.

Ralph Loop provides both the **safety harness** (retry failing sub-tasks, idempotent re-run, halt on `STEP-1-GREEN` promise) and a clear **iteration rhythm** that matches the dependency DAG below. Parallel sub-agents still do the inner execution, but they're dispatched in dependency-ordered cohorts, not a free-for-all.

**Ralph Loop's role:** outer supervision + verification gate + halt sentinel.
**Parallel sub-agents' role:** inner execution, dispatched in 4 cohorts across ~8–12 iterations.

---

## Scope — what S1 covers (and what it doesn't)

### In scope — 12 critical blockers (must all land)

| # | Blocker | Anchor | Source |
|---|---|---|---|
| B1 | `Job` dataclass missing `first_seen_at`, `last_seen_at`, `staleness_state` | `models.py:17-39` | Plan + R1 |
| B2 | `insert_job` silently overwrites caller-supplied timestamps with raw SQL `= datetime('now')` | `database.py:173,175,176` | **R2 bombshell** |
| B3 | `JobScorer.score()` returns `int` — no per-dimension breakdown | `skill_matcher.py:380-414` | R1 |
| B4 | `MIN_MATCH_SCORE` filter uses `>=` on scalar — breaks when `score()` returns dict | `main.py:471` | R1 |
| B5 | JobScorer callers don't pass `user_preferences` / `enrichment_lookup` — multi-dim never activates | `main.py:340`, `workers/tasks.py:108-112` | R1 |
| B6 | `JobResponse` missing 5 date fields + 13 enrichment fields | `api/models.py:31-58` | R1 |
| B7 | No `ENRICHMENT_THRESHOLD`; no `enrich_batch()`; serialized LLM calls | `settings.py`, `job_enrichment.py` | R1 |
| B8 | `run_search` never calls `VectorIndex.upsert()` — `?mode=hybrid` is dead on arrival | `main.py:485-495`, `vector_index.py:72-84` | R1 |
| B9 | `get_recent_jobs` serves `staleness_state='expired'` rows | `database.py:308-316` | R2 |
| B10 | ARQ `score_and_ingest` path never enriches, never registers `enrich_job_task` | `workers/tasks.py:108`, `workers/settings.py:87-92` | R2 |
| B11 | Concurrent boot (API + worker) races on `_schema_migrations` INSERT | `migrations/runner.py:133-161` | R2 |
| B12 | No per-user rate limit on POST /search → any authed user can burn LLM quota | `api/routes/search.py:25-55` | R2 |

### Also in scope — 3 should-fix observability items

S1 wires enrichment + embeddings + hybrid. If these fail silently in dogfood, nothing remains to diagnose them. Ship with the minimum viable telemetry.

| # | Should-fix | Anchor |
|---|---|---|
| S1 | `run_uuid` contextvar propagation from `run_search` → sub-operations; persist to `run_log.run_uuid` | `utils/logger.py:8`, `main.py:536` |
| S2 | Wrap source fetch calls with timer + error counter; populate `per_source_errors` + `per_source_duration` | `main.py` scoring phase |
| S3 | Telemetry dataclasses for enrichment + embeddings + hybrid (`llm_calls`, `cache_hits`, `validation_failures`, `upserts_ok`, `fallback_reason`) | new module: `backend/src/utils/telemetry.py` |

### Non-scope (explicitly deferred)

- **Domain classifier wiring** — audit confirms already done at `main.py:294,297-298`. Verified via one dogfood assertion, no code changes.
- **Skill synonyms non-tech expansion** — hurts non-tech enrichment accuracy but not a correctness bug. Defer to Step 4 (ops/data-quality batch).
- **FX rate freshness** — hardcoded Q1-2026 rates, 2–4% drift acceptable. Defer to Step 4.
- **Separate LLM quota pools** (CV vs enrichment) — quota cascades are a dogfood-scale concern. Defer to Step 5 (launch readiness).
- **N+1 enrichment loader** — will naturally disappear when the JOIN-once approach in sub-step 10 lands; no separate item.
- **Staleness writer (ghost-detection output persistence)** — exposes a latent bug (R2 finding). Fix in a follow-up **Batch S1.5** before Step 2 starts. S1 includes the filter (B9) but not the writer, because adding the writer requires a full re-scan pass that would inflate S1 beyond budget.

---

## Tool orchestration — what each tool does

### Ralph Loop (outer driver)

**Invocation:** `/ralph-loop` with `completion_promise: "STEP-1-GREEN"` and `max_iterations: 20` (Step 0 burned ~15; S1 is tighter scope but wiring-dense — expect 10–14 iterations).

**Each iteration:**
1. Check sentinel: does `.claude/step-1-verified.txt` exist? If yes, emit `STEP-1-GREEN` and halt.
2. Run `make verify-step-1` (added in iteration 1 as a new Makefile target).
3. Parse output → identify which blockers are still red.
4. Dispatch the cohort of sub-agents that matches the current dependency stage.
5. Re-run verification, write/update sentinel if all green.
6. Commit partial progress with a conventional-commit prefix.

**Stop criteria (all must hold before emitting `STEP-1-GREEN`):**
- All 12 blockers (B1–B12) have landed commits
- All 3 should-fix observability items (S1–S3) have landed commits
- `cd backend && python -m pytest tests/ --ignore=tests/test_main.py -q -p no:randomly` returns ≥1,018p / 0f / 3s (baseline preserved; +25–40 new tests acceptable)
- `python scripts/bootstrap_dev.py` exits 0 AND prints a `JobResponse` with all 7 dims non-zero AND 5 date fields non-null AND (if `ENRICHMENT_ENABLED=true`) ≥1 enrichment field populated
- `python -c "import asyncio; from src.main import run_search; asyncio.run(run_search(source_filter='arbeitnow'))"` runs without touching `arq` or heavy semantic imports unless `SEMANTIC_ENABLED=true`
- `arq src.workers.settings.WorkerSettings` boots (smoke test — no Redis required when `ARQ_TEST_MODE=1` is set)
- `?mode=hybrid` end-to-end dogfood: populated index returns fused results; empty index returns keyword fallback with a warning log (no 500)
- `make verify-step-1` exits 0 — single source of truth for "green"
- Sentinel file `.claude/step-1-verified.txt` contains the final green commit SHA
- CLI↔ARQ parity fixture test passes: same input → identical `match_score` + `ScoreBreakdown` from both paths

### Parallel sub-agents (inner execution — 4 cohorts)

Driven by skill `superpowers:dispatching-parallel-agents`. Each iteration launches the cohort matching the dependency stage. Cohorts A→D run in order; within a cohort, agents are parallel.

#### Cohort A — Foundation (Iterations 1–3)

Must land before anything else. These changes are prerequisites for every downstream wiring.

| Agent | Blockers | Files touched | Skills |
|---|---|---|---|
| Agent-Dataclass | B1, B2 | `backend/src/models.py`, `backend/src/repositories/database.py::insert_job`, `backend/tests/test_models.py`, `backend/tests/test_database.py` | `/implement` + `superpowers:test-driven-development` |
| Agent-ScoreBreakdown | B3, B4 | `backend/src/services/skill_matcher.py::JobScorer.score`, `backend/src/services/scoring_dimensions.py` (typed union), `backend/src/main.py:471`, `backend/src/workers/tasks.py:113`, `backend/tests/test_scorer.py` | `/implement` + `superpowers:test-driven-development` |
| Agent-Migration-Race | B11 | `backend/migrations/runner.py::up`, `backend/tests/test_migrations.py` | `/implement` |

**Gate before cohort B:** `pytest tests/test_models.py tests/test_database.py tests/test_scorer.py tests/test_migrations.py -v` all green; `git grep 'def score' backend/src/services/skill_matcher.py` confirms return type is `ScoreBreakdown`.

#### Cohort B — Multi-dim activation + enrichment wiring (Iterations 4–6)

Depends on cohort A (`ScoreBreakdown` must exist; dataclass must round-trip).

| Agent | Blockers | Files touched | Skills |
|---|---|---|---|
| Agent-Scorer-Callers | B5 | `backend/src/main.py:339-341,461-464`, `backend/src/workers/tasks.py:104-113`, `backend/tests/test_main.py`, `backend/tests/test_workers_tasks.py` | `/implement` |
| Agent-Enrichment | B7 | `backend/src/core/settings.py` (add `ENRICHMENT_THRESHOLD`), `backend/src/services/job_enrichment.py` (add `enrich_batch()` with semaphore), `backend/src/main.py` (threshold-gated invocation), `backend/tests/test_job_enrichment.py` | `/implement` + `superpowers:test-driven-development` |
| Agent-ARQ-Parity | B10 | `backend/src/workers/settings.py:87-92`, `backend/src/workers/tasks.py::score_and_ingest`, `backend/tests/test_workers_tasks.py` (CLI↔ARQ parity fixture) | `/implement` |

**Gate before cohort C:** CLI↔ARQ parity test passes; `enrich_batch(jobs, semaphore=10)` returns the same number of enrichments as input jobs; `WorkerSettings.functions` list contains `enrich_job_task`.

#### Cohort C — Response surface + hybrid + security (Iterations 7–9)

Depends on cohort B (multi-dim must activate before we can surface dim fields).

| Agent | Blockers | Files touched | Skills |
|---|---|---|---|
| Agent-Response-Schema | B6 | `backend/src/api/models.py::JobResponse` (add 5 date + 13 enrichment fields, all `Optional = None`), `backend/src/api/routes/jobs.py::_row_to_job_response` (JOIN-once enrichment prefetch, populate dims from user scope), `backend/tests/test_api.py` | `/implement` + `superpowers:test-driven-development` |
| Agent-Hybrid | B8 | `backend/src/main.py` (post-insert `VectorIndex.upsert` loop, gated by `SEMANTIC_ENABLED`), `backend/src/api/routes/jobs.py:111-186` (wire `mode=hybrid` to `retrieve_for_user` with keyword fallback), `backend/scripts/build_job_embeddings.py` (restore from frozen worktree), `backend/tests/test_retrieval_integration.py` | `/implement` |
| Agent-Security | B9, B12 | `backend/src/repositories/database.py::get_recent_jobs` (add `staleness_state = 'active'` filter), `backend/src/api/routes/search.py` (per-user concurrent-run cap), `backend/tests/test_api_security.py` | `/implement` |

**Gate before cohort D:** `curl /jobs?mode=hybrid` on populated DB returns fused IDs; `curl /jobs?mode=hybrid` on empty index returns keyword fallback + WARNING log; 4th concurrent `POST /search` from same user returns 429.

#### Cohort D — Observability + frontend + docs (Iterations 10–12)

Depends on cohorts A/B/C (observability wraps the real code paths).

| Agent | Items | Files touched | Skills |
|---|---|---|---|
| Agent-Telemetry | S1, S2, S3 | `backend/src/utils/logger.py` (add `_run_uuid_var: ContextVar`), `backend/src/utils/telemetry.py` (new), `backend/src/main.py` (set context + per-source timing), `backend/src/services/job_enrichment.py` + `embeddings.py` + `retrieval.py` (emit counters), `backend/tests/test_telemetry.py` | `/implement` + `superpowers:test-driven-development` |
| Agent-Frontend | B6 (frontend mirror) | `frontend/src/lib/types.ts` (add 5 date + 13 enrichment fields), `frontend/src/lib/api.ts` (verify `mode` pass-through) | `/sync` |
| Agent-Docs | — | `CLAUDE.md` (add 1 new rule: "multi-dim scoring requires both `user_preferences` + `enrichment_lookup`"), `docs/IMPLEMENTATION_LOG.md` (Step 1 entry with test delta + blocker closure table), `docs/step_1_plan.md` (executed-version annotations) | `/sync` |

**Gate before STEP-1-GREEN:** `make verify-step-1` green; sentinel written; dogfood smoke passes end-to-end.

### Skills (invoked inside agents)

| Skill | Used by | Purpose |
|---|---|---|
| `superpowers:writing-plans` | this document | already in use |
| `superpowers:executing-plans` | Ralph Loop iteration orchestrator | picks the next unfinished blocker from the cohort DAG |
| `superpowers:dispatching-parallel-agents` | each iteration's agent dispatch | batches cohort A/B/C/D agents |
| `superpowers:test-driven-development` | Agent-Dataclass, Agent-ScoreBreakdown, Agent-Enrichment, Agent-Response-Schema, Agent-Telemetry | RED-first discipline for correctness-critical code |
| `superpowers:verification-before-completion` | Ralph Loop gate | cannot emit `STEP-1-GREEN` until `make verify-step-1` passes |
| `superpowers:systematic-debugging` | Agent-Hybrid, Agent-ARQ-Parity | if hybrid returns empty or ARQ parity fails, use this skill to isolate — don't paper over |
| `superpowers:receiving-code-review` | Iteration N review cycle | when the reviewer worktree flags items, respond with evidence not performance |
| `commit` | end of each iteration | conventional-commit partial-progress snapshot |
| `update-config` | iteration 1 | add `make verify-step-1` target + any new `.env.example` vars (`ENRICHMENT_THRESHOLD`) |
| `less-permission-prompts` | iteration 1 | pre-allow the frequent calls (`pytest`, `make verify-step-1`, `python scripts/bootstrap_dev.py`, `python -m migrations.runner status`) |

### MCP servers (nice-to-have inside agents)

| MCP | Used by | Purpose |
|---|---|---|
| Context7 | Agent-Response-Schema | fetch Pydantic v2 `model_validate` + `Optional[...] = None` edge-case docs when the 18-field expansion stumbles |
| Context7 | Agent-Hybrid | verify current ChromaDB persistent-client + RRF-fusion best practices |
| Chrome DevTools | Ralph gate, final iterations | headless smoke: load `http://localhost:3000/jobs` after `npm run dev`; confirm radar renders non-zero dims |
| Playwright | Cohort D verification | scripted smoke across `/dashboard` → `/jobs/:id` → profile flow to confirm the data pipe |
| IDE diagnostics (`mcp__ide__getDiagnostics`) | every agent post-edit | catch typing errors before commit (especially for the 18-field `JobResponse` expansion) |

### Subagent types (framework-level)

| Subagent type | Usage |
|---|---|
| `Explore` | already used in Phase 1 audit (round 1 + round 2); not needed during execution |
| `Plan` | not used — audit was sufficient for this scoped plan |
| `feature-dev:code-reviewer` | Ralph Loop **final iteration only**: review the accumulated S1 diff (expected ~20 files, ~1,000 LOC) before emitting `STEP-1-GREEN` |
| `feature-dev:code-architect` | iteration 1 only: confirm the `ScoreBreakdown` return shape choice (dict vs dataclass) before committing the API contract |
| `codex:codex-rescue` | escape hatch — invoke if three Ralph iterations pass without progress on a specific blocker (prior S0 precedent) |
| `coderabbit:code-reviewer` | optional additional review layer for the correctness-critical files: `insert_job`, `score()`, `retrieve_for_user`, migration runner lock |

---

## Dependency DAG

```
                        ┌─────────────────────────────────┐
                        │         COHORT A                │
                        │  B1 (dataclass) ──► B2 (insert) │
                        │  B3 (ScoreBreakdown) ──► B4    │
                        │  B11 (migration race)           │
                        └─────────────┬───────────────────┘
                                      │
                        ┌─────────────▼───────────────────┐
                        │         COHORT B                │
                        │  B5 (scorer callers)           │
                        │  B7 (enrich_batch + threshold)  │
                        │  B10 (ARQ parity)               │
                        └─────────────┬───────────────────┘
                                      │
                        ┌─────────────▼───────────────────┐
                        │         COHORT C                │
                        │  B6 (JobResponse + serializer)  │
                        │  B8 (hybrid wiring + ingest)    │
                        │  B9 (expired filter)            │
                        │  B12 (rate limit)               │
                        └─────────────┬───────────────────┘
                                      │
                        ┌─────────────▼───────────────────┐
                        │         COHORT D                │
                        │  S1–S3 (telemetry)              │
                        │  B6 frontend mirror             │
                        │  CLAUDE.md + docs               │
                        └─────────────────────────────────┘
```

Each arrow is a hard dependency. Cohort-A conflicts: Agent-Dataclass and Agent-ScoreBreakdown both touch `backend/src/services/skill_matcher.py` (dataclass import) and `backend/tests/test_scorer.py` — they run **sequentially within cohort A**. Everything else in the cohort is conflict-free.

---

## Critical files to modify (with reuse notes)

| File | Action | Reuse from existing code |
|---|---|---|
| `backend/src/models.py:17-39` | Add `first_seen_at`, `last_seen_at`, `staleness_state` as `Optional[str] = None` | mirror existing date-field style at lines 36-38 |
| `backend/src/repositories/database.py:154-189` | Accept optional `first_seen_at` / `last_seen_at` from `Job`; only default to `now` when None | pattern already in place for `posted_at` at line 182 |
| `backend/src/repositories/database.py:308-316` | Add `AND staleness_state = 'active'` to `get_recent_jobs` WHERE clause | no reuse needed — single-line diff |
| `backend/src/services/skill_matcher.py:380-414` | Change `score() -> int` to `score() -> ScoreBreakdown`; return dict or dataclass with per-dim ints | dim scorers at `services/scoring_dimensions.py:62,95,148,179` are ready; no rewrites |
| `backend/src/main.py:339-341` | `JobScorer(search_config, user_preferences=profile.preferences, enrichment_lookup=_build_enrichment_lookup(db))` | `profile.preferences` already loaded at line 320 via `load_profile` |
| `backend/src/main.py:471` | After ScoreBreakdown returned, set `j.match_score = breakdown.match_score`; filter unchanged | follow existing `j.match_score = scorer.score(j)` pattern at line 462 |
| `backend/src/main.py:485-495` | After `insert_job`, if `SEMANTIC_ENABLED`: lazy-import + `encode_job()` + `VectorIndex.upsert()` | lazy import pattern from `services/embeddings.py:44` + `vector_index.py:26` (rule #16) |
| `backend/src/workers/tasks.py:104-113` | Same `JobScorer(...)` signature upgrade as `main.py`; `enqueue_job('enrich_job_task', job_id)` after score | existing enqueue pattern in tasks.py |
| `backend/src/workers/settings.py:87-92` | Add `enrich_job_task` to `functions` list | trivial — one line |
| `backend/src/api/models.py:31-58` | Add 5 date fields + 13 enrichment fields, all `Optional = None` | mirror existing `role: int = 0` default-zero pattern on 9 score dims |
| `backend/src/api/routes/jobs.py:43-65` | `_row_to_job_response` populates new fields; `LEFT JOIN job_enrichment` done once in route handler, dict passed in | existing `_row_to_job_response` signature already takes extra args |
| `backend/src/api/routes/jobs.py:111-186` | Remove `_ = mode` at line 129; wire `mode=hybrid` path via `retrieve_for_user(profile, keyword_fn=..., semantic_fn=...)` with keyword fallback | `is_hybrid_available()` at `retrieval.py:124-130` already safe |
| `backend/src/api/routes/search.py:25-55` | Count active runs per `user.id`; 429 at ≥3 concurrent | existing `_runs[run_id]['user_id']` at line 40 provides the key |
| `backend/src/services/job_enrichment.py` | Add `async def enrich_batch(jobs, *, semaphore_limit=10) -> list[JobEnrichment]` | lazy-import pattern already established |
| `backend/src/core/settings.py` | Add `ENRICHMENT_THRESHOLD = int(os.getenv("ENRICHMENT_THRESHOLD", "60"))` | mirror existing `MIN_MATCH_SCORE` at line 50 |
| `backend/migrations/runner.py:133-161` | Wrap apply loop in `BEGIN IMMEDIATE` → commit per-migration; on `IntegrityError` swallow + reload applied set | sqlite `IntegrityError` swallow pattern exists in other migrations |
| `backend/scripts/build_job_embeddings.py` | Restore from `.claude/worktrees/generator/scripts/` — the frozen worktree version | one-shot backfill script |
| `backend/src/utils/logger.py:8` | Add `_run_uuid_var: ContextVar[str | None] = ContextVar("run_uuid", default=None)`; helpers `set_run_uuid(uuid)` / `current_run_uuid()` | Python stdlib pattern; no external reuse |
| `backend/src/utils/telemetry.py` | **new file.** `@dataclass` counters for enrichment + embeddings + hybrid; context-manager for per-source timing | pattern: `with source_timer(name) as t: ...` emits to logger at close |
| `frontend/src/lib/types.ts:7-37` | Add 5 date + 13 enrichment fields, all `?: string \| null` or `?: number \| null` | existing 9-dim fields already declared — just extend |
| `Makefile` | Add `verify-step-1` target — aggregates the verification gate commands below | existing `verify-step-0` target as template |
| `CLAUDE.md` | Add rule #20: "Multi-dim scoring requires both `user_preferences` AND `enrichment_lookup` kwargs — callers must pass both or neither. Defaults fall back to legacy 4-component formula (see rule #19)." | mirror existing rule #19 wording |

---

## Verification section

Ralph Loop cannot emit `STEP-1-GREEN` until this ALL passes.

### Gate command

```bash
make verify-step-1
```

Which runs (in order):

```bash
# 1. Backend regression — must stay ≥1,018p/0f/3s
cd backend
python -m pytest tests/ --ignore=tests/test_main.py -q -p no:randomly
# Expect: ≥1,018 passed / 0 failed / 3 skipped (new: ~25–40 from cohorts A–D)

# 2. Migration race — two processes try to apply simultaneously
python -c "
import asyncio, tempfile
from migrations.runner import up
async def race():
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        path = f.name
    r1, r2 = await asyncio.gather(up(path), up(path), return_exceptions=True)
    assert not isinstance(r1, Exception), r1
    assert not isinstance(r2, Exception), r2
asyncio.run(race())
"
# Expect: exits 0 — no UNIQUE(id) crash; second process no-ops gracefully

# 3. Dataclass round-trip — insert_job respects caller's first_seen_at
python -c "
import asyncio
from src.models import Job
from src.repositories.database import JobDatabase
async def check():
    db = JobDatabase(':memory:')
    await db.initialize()
    j = Job(title='t', company='c', apply_url='u', source='s', date_found='2026-04-24',
            first_seen_at='2020-01-01T00:00:00Z')
    await db.insert_job(j)
    rows = await db.get_recent_jobs(days=9999)
    assert rows[0]['first_seen_at'].startswith('2020-01-01'), rows[0]['first_seen_at']
asyncio.run(check())
"
# Expect: exits 0 — caller-supplied timestamp is preserved

# 4. Expired-job filter (B9)
python -m pytest tests/test_api_security.py::test_expired_jobs_filtered -v

# 5. CLI↔ARQ parity — same input produces identical ScoreBreakdown from both paths (B10)
python -m pytest tests/test_workers_tasks.py::test_cli_arq_scoring_parity -v

# 6. Enrichment batch concurrency (B7)
python -m pytest tests/test_job_enrichment.py::test_enrich_batch_respects_semaphore -v

# 7. Hybrid mode fallback (B8)
python -m pytest tests/test_retrieval_integration.py::test_mode_hybrid_empty_index_falls_back -v
python -m pytest tests/test_retrieval_integration.py::test_mode_hybrid_populated_index_fuses -v

# 8. Per-user rate limit (B12)
python -m pytest tests/test_api_security.py::test_search_concurrent_cap_per_user -v

# 9. Startup safety — SEMANTIC_ENABLED=false must not import heavy deps
python -c "
import os; os.environ['SEMANTIC_ENABLED'] = 'false'
import sys
from src.api.main import app  # noqa
assert 'sentence_transformers' not in sys.modules
assert 'chromadb' not in sys.modules
assert 'arq' not in sys.modules
"

# 10. Bootstrap dogfood — end-to-end proof
python main.py &
BACKEND_PID=$!
sleep 3
ENRICHMENT_ENABLED=false python scripts/bootstrap_dev.py
# Expect: exits 0; final JobResponse has match_score > 0 AND all 7 dims > 0 AND
# posted_at/first_seen_at/last_seen_at/date_confidence/staleness_state non-null
kill $BACKEND_PID

# 11. ARQ worker smoke (no Redis required in test mode)
ARQ_TEST_MODE=1 python -c "
from src.workers.settings import WorkerSettings
names = [f.__name__ for f in WorkerSettings.functions]
assert 'enrich_job_task' in names, names
assert 'score_and_ingest' in names, names
"

# 12. Frontend boot smoke
cd ../frontend
npm run build
# Expect: exits 0 — types.ts additions don't break tsc

# 13. Pre-commit gate
cd ..
pre-commit run --all-files
# Expect: all hooks pass
```

### Sentinel write (after gate passes)

```bash
echo "$(git rev-parse HEAD)" > .claude/step-1-verified.txt
git add .claude/step-1-verified.txt
git commit -m "chore(step-1): write sentinel at green commit"
# Ralph Loop sees this on next iteration, emits STEP-1-GREEN, halts.
```

### End-to-end proof (human check after sentinel)

1. `git log --oneline main..step-1-batch-s1` shows ~12–15 partial-progress commits + 1 final merge commit
2. `git diff main..step-1-batch-s1 --stat` shows ~20 files changed, ~1,000 LOC added
3. On a fresh Chrome window, load `http://localhost:3000/dashboard` — ScoreRadar renders non-zero slices across all 7 dimensions
4. With `ENRICHMENT_ENABLED=true` + a Gemini key, `/jobs/:id` response includes `title_canonical`, `required_skills`, `salary` (structured), `seniority` (enum), `visa_sponsorship` fields for high-scored jobs
5. `?mode=hybrid` + populated index on the backend → dashboard ordering visibly differs from `?mode=keyword` (fused ranking at work)
6. `docs/IMPLEMENTATION_LOG.md` has a completed "Step 1 — Engine→API Seam" entry with test delta + blocker closure table

---

## Execution budget

- Ralph Loop: max 20 iterations (expect 10–14 with full 12+3 scope)
- Wall-clock: 4–6 sessions
- Commits: 1 per iteration (partial progress) + 1 final merge commit to `main`
- Branch: `step-1-batch-s1` off `main @ 51d5c07`
- Worktree: **dual-worktree** (generator does the implementation; reviewer runs the verification gate in isolation). Fast-forward both from `4d81397` to `51d5c07` in iteration 1 before branching off.
- Merge strategy: **fast-forward only** to main; if the reviewer worktree lags, rebase onto main before FF
- Tag: `step-1-green` on the final commit (mirrors the `pillar2-generator-complete` precedent)

---

## Acknowledged trade-offs

- **Ralph Loop overhead vs one-shot cohort dispatch.** Cohorts A→D have real sequential dependencies. A single parallel-dispatch batch would race on `ScoreBreakdown` return-shape decisions. Ralph Loop's cohort gating + verification-between-cohorts is the right tool. Accepted.
- **Deferring the staleness writer (S1.5).** Round-2 audits surfaced that `staleness_state` has no writer — every job is perpetually `'active'`. S1 filters `'expired'` (B9) for defence-in-depth but does NOT add the writer. This means post-S1, `staleness_state` in `JobResponse` will always be `'active'`. This is an honest intermediate state: the field exists, surfaces correctly, and the filter is in place for when the writer lands in S1.5. Alternative (fold writer into S1) would inflate S1 beyond budget and push the whole engine→API seam later.
- **ARQ parity test requires a harness.** CLI↔ARQ parity is only meaningful if both paths run against the same fixture with the same `SearchConfig`. Cohort B's Agent-ARQ-Parity must build this harness or the parity guarantee is paper-only. The budget above accounts for this.
- **The `ScoreBreakdown` return type is API-facing.** Once `JobScorer.score()` returns a dict/dataclass, every external caller inherits that shape. We're treating this as a one-way door: choose dataclass over dict (better static typing, better mypy coverage), document it in CLAUDE.md rule #20, and don't revisit until Step 4.
- **No new endpoints in S1.** Step 3 adds the user-facing endpoints (version history, skill provenance). S1 deliberately stays in existing routes — the engine→API seam opens the valve, Step 3 adds new taps.
- **Observability is should-fix, not must-fix.** S1–S3 are bundled with S1 because without them, dogfood failures are undiagnosable. But they're not blockers — if cohort D slips, Ralph Loop will still emit `STEP-1-GREEN` when B1–B12 are closed. Observability can ship in S1.5 if budget pressure demands. Preference: ship together.

---

## Post-S1 follow-ups (explicitly tracked here, not implemented in S1)

Created during planning so nothing is lost:

1. **Batch S1.5 — staleness writer + observability dashboards.** If S1 ships without the ghost-detection writer, S1.5 wires `ghost_detection.transition()` result into `update_last_seen()` and adds a nightly cron that marks stale jobs. Also surfaces the `run_uuid` + `per_source_errors` data from S1 telemetry into a `docs/dogfood_first_run.md` one-page dashboard.
2. **Batch S2 prerequisites.** S2 (UI polish) depends on S1's dim fields being non-zero. Before starting S2, run `bootstrap_dev.py` with three distinct profiles (tech / healthcare / academia) and confirm dim variance across profiles — regression signal for multi-dim activation.
3. **Step 4 carry-overs.** Skill-synonyms non-tech expansion, FX rate freshness, LLM quota pool separation, ghost-detection nightly cron.

---

## Handoff

- **Generator worktree** (`worktree-generator` at `.claude/worktrees/generator`): fast-forward to `main @ 51d5c07`, branch `step-1-batch-s1`, invoke `/ralph-loop` with `completion_promise: "STEP-1-GREEN"` and `max_iterations: 20`. Cohorts A→D per this document.
- **Reviewer worktree** (`worktree-reviewer` at `.claude/worktrees/reviewer`): fast-forward to the same tip, run `make verify-step-1` after each cohort commit, produce per-cohort audit reports under `docs/reviews/step-1-cohort-{A,B,C,D}-review.md`. Use `feature-dev:code-reviewer` + optionally `coderabbit:code-reviewer` for the correctness-critical files.
- **Main session (this one)**: monitors Ralph Loop progress, answers clarifying questions, merges `step-1-batch-s1` → `main` via fast-forward + tags `step-1-green` after the reviewer signs off.

_Plan written 2026-04-24. Anchor verification: 6 Explore agents × 2 audit waves on `main @ 51d5c07`. User-confirmed decisions: **(1)** run S1 as a single Ralph-Loop-driven batch with 12 blockers + 3 observability items; **(2)** use dual-worktree (generator + reviewer); **(3)** staleness writer deferred to S1.5, not S1._

---

## Execution log (filled in 2026-04-24 by generator worktree)

- Cohort A landed at: cec914f, acb9216, 9100d6d (B1, B2, B3, B4, B11)
- Cohort B landed at: f2e7d13, 30cf923, 226cf41 (B5, B7, B10)
- Cohort C landed at: 7ee6dc1, e1c48a6, 658844b (B6, B9, B12, B8)
- Cohort D landed at: 88501f3 (frontend mirror), d64e9c6 (S1+S2+S3 telemetry); docs annotations applied in this commit
- Total iterations used: 1 (parallel cohort dispatch collapsed the 10-14 target into a single supervisory iteration)
- Final test count: pending — `make verify-step-1` will write the sentinel
- Final tag: step-1-green @ pending-SHA
