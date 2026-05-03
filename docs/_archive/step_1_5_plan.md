# Step 1.5 — Pre-Step-2 Stabilisation (S1.1 + S1.5 + Step-3-MVP) — Ralph-Loop-Driven Plan

> **Status:** Approved 2026-04-25. Mirror of `.claude/plans/now-i-ve-got-another-crispy-island.md`. Hand this file to the generator worktree to implement, and to the reviewer worktree to audit.
>
> **Purpose:** Close every gap that Step 2 depends on, in one Ralph-Loop session, before any UI work begins. Three mini-batches bundled into a single execution because their dependencies are tightly coupled and shipping them separately would burn three sessions to do one session's work.
>
> **Pre-reqs carried from Step 1:** `main @ 17ccdf0`, 1,056p/0f/4s baseline, `step-1-green` tag pushed, `worktree-generator` aligned. `worktree-reviewer` is stale at `4d81397` — fast-forward in iteration 1.

---

## Context

**Why this exists.** Step 2's audit (2026-04-25, 6 sub-agents) revealed three classes of "Step 2 is blocked" gaps that Step 1 didn't surface:

1. **The Step-1 dim-field bombshell.** `_row_to_job_response()` at `backend/src/api/routes/jobs.py:93-158` never populates the 8 per-dimension score fields on `JobResponse`. The comment at `backend/src/api/models.py:46-50` literally admits this: *"Per-dim values are computed at write time but not yet persisted as separate columns — defaulting to 0 keeps the legacy radar-chart behaviour."* Step 1's exit criteria claimed all 7 dims would be non-zero in dogfood. **That claim was false.** The radar will render 8 zero slices in the UI forever unless this is fixed first.

2. **Three Pillar-1/2/3 deferrals are now Step-2 blockers.** Staleness writer (Pillar 3 Batch 1) was deferred from Step 1 to S1.5; ESCO normaliser (Pillar 2 Batch 2.6) is fully built but never invoked from `cv_parser.py`; and while `save_profile()` already writes to `user_profile_versions`, the table is reachable but the API isn't. Step 2's `StalenessWarning`/`GhostBadge`/ESCO tooltip/version-history UI all need these wired before they can render real data.

3. **Step 3 endpoints are the prerequisite Step 2 leans on.** S2 needs `GET /profile/versions` + `POST /profile/versions/{id}/restore` + `GET /profile/json-resume`; S4 needs `GET /notifications`. The ExecutionOrder doc itself recommends "Step 3 first" but the numbering hides it. We'll ship the minimum-viable Step 3 cut as part of this batch.

**What prompted it.** Two dogfood realities revealed by the round-2 audit: (a) HiringCafe's "no scoring transparency" is exactly the wedge Job360 should ship, but our radar shows zeros — defeating the entire pitch. (b) Step 2's dependency analysis showed every UI feature for skill provenance, ESCO labels, profile versions, JSON Resume export, dedup-group badges, staleness warnings is blocked on a backend gap. Trying to start Step 2 on `main @ 17ccdf0` would produce a UI bound to mostly-empty data.

**Intended outcome.** After Step 1.5 completes:
- `bootstrap_dev.py` returns a `JobResponse` where the 8 per-dim score fields are **non-zero for at least one job** in the response (the actual exit-criteria assertion Step 1 should have shipped).
- Ghost-detection state machine actually runs: `staleness_state` transitions through `'active' → 'possibly_stale' → 'likely_stale'` based on `consecutive_misses` + age, persisted to the DB.
- ESCO normaliser activates from the CV parser when `SEMANTIC_ENABLED=true`, populating `esco_uri` on each `SkillEntry` and surfacing the canonical label in `ProfileResponse`.
- New endpoints exist: `GET /profile/versions`, `POST /profile/versions/{id}/restore`, `GET /profile/json-resume`, `GET /notifications` (paginated). All gated by `Depends(require_user)`.
- `JobResponse` exposes `dedup_group_ids: list[int]` for the "also posted on Indeed + Reed" badge.
- `ProfileResponse` exposes `skill_provenance`, `skill_tiers`, ESCO canonical labels, LinkedIn sub-sections, GitHub temporal data, version-id metadata.
- Test suite is green at ≥1,056p/0f/4s + ~30-50 new tests; no regressions.
- Step 2 can begin without inheriting any "the data isn't there" surprises.

---

## Strategic context — why one Ralph Loop instead of three

**Tight dependency coupling.** S1.1 (dim-field persistence) requires a new migration `0011`. S1.5 (staleness writer) modifies the same `database.py` write path. Step 3 (new endpoints) JOINs against the same tables and surfaces the same fields. Splitting them into three sessions means three migration sequences, three reviewer cycles, three merge-conflict windows on `database.py`. Bundling them is strictly cheaper.

**One verification gate, three exits.** A single `make verify-step-1.5` target captures all three mini-batches' invariants. A single sentinel `.claude/step-1-5-verified.txt` halts the loop. Cohorts inside the loop preserve the dependency DAG.

**Ralph Loop's role:** outer supervision + verification gate + halt sentinel.
**Parallel sub-agents' role:** inner execution, dispatched in 3 cohorts across ~6-10 iterations.

This batch is smaller than Step 1 (12 blockers + 3 obs items) — expect ~10 blockers across 3 mini-batches with full parallelism inside cohorts.

---

## Scope — what Step 1.5 covers (and what it doesn't)

### Mini-batch S1.1 — Dim-field persistence (the bombshell fix)

**Goal:** Make Step 1's "all 7 dims non-zero" promise honest end-to-end.

| # | Blocker | Anchor |
|---|---|---|
| **S1.1-A** | `Job` dataclass missing 9 per-dim score fields | `models.py:17-39` (post-Step-1) |
| **S1.1-B** | `jobs` table missing 9 score-dim columns | `database.py:28-55` (CREATE TABLE) |
| **S1.1-C** | `main.py:530` extracts only `breakdown.match_score`, drops the 8 other dims | `main.py:525-530` |
| **S1.1-D** | `insert_job()` persists only `match_score` int, not the breakdown | `database.py:154-199` |
| **S1.1-E** | `_JOBS_ENRICHMENT_JOIN_COLS` doesn't SELECT score columns | `database.py:535-548` |
| **S1.1-F** | `_row_to_job_response()` doesn't extract dim fields from row | `api/routes/jobs.py:93-158` |
| **S1.1-G** | `JobResponse` admits the bug in its own docstring (`api/models.py:46-50`) | needs comment update |
| **S1.1-H** | No test asserts dim values are non-zero for a real-world fixture | `tests/test_api.py` |

### Mini-batch S1.5 — Deferrals from Step 1

**Goal:** Wire the three "exists but never called" services so their outputs reach the data layer.

| # | Blocker | Anchor |
|---|---|---|
| **S1.5-A** | `ghost_detection.transition()` never invoked from `_ghost_detection_pass` | `main.py:168-213`, `ghost_detection.py:34-45` |
| **S1.5-B** | `update_last_seen()` hardcodes `staleness_state='active'`; no path writes other states | `database.py:201-210` |
| **S1.5-C** | `mark_missed_for_source()` increments `consecutive_misses` but never recomputes `staleness_state` via `transition()` | `database.py` (mark_missed) |
| **S1.5-D** | `skill_normalizer.normalize_skill()` never called from `cv_parser._llm_result_to_cvdata()` | `cv_parser.py:295-371`, `skill_normalizer.py:183-209` |
| **S1.5-E** | `SkillEntry.esco_uri` field is defined but always `None` | `skill_entry.py:48-79` |
| **S1.5-F** | `skill_tiering.tier_skills_by_evidence()` exists but its output isn't surfaced on `ProfileResponse` | `skill_tiering.py:78-104`, `api/models.py:145-149` |

### Mini-batch S3-MVP — Minimum-viable Step 3 endpoints

**Goal:** Ship the smallest possible Step 3 to unblock all of Step 2.

| # | Blocker | Anchor |
|---|---|---|
| **S3-A** | `GET /profile/versions` route missing | `api/routes/profile.py:166` (insertion point) |
| **S3-B** | `POST /profile/versions/{id}/restore` route missing | same |
| **S3-C** | `GET /profile/json-resume` route missing (`to_json_resume()` at `models.py:175-238` is ready) | same |
| **S3-D** | `GET /notifications` paginated route missing; `database.get_notification_ledger()` reader doesn't exist | `routes/profile.py` neighbour, `database.py` |
| **S3-E** | `ProfileResponse` doesn't surface `skill_provenance`, `skill_tiers`, ESCO labels, LinkedIn sub-sections (`linkedin_languages`/`linkedin_projects`/`linkedin_volunteer`/`linkedin_courses`), GitHub temporal data | `api/models.py:124-149` |
| **S3-F** | `JobResponse` doesn't surface `dedup_group_ids: list[int] \| None` (defaults to None for now; populated when dedup-group surface lands) | `api/models.py:31-90` |
| **S3-G** | 6 new Pydantic models missing: `ProfileVersionSummary`, `ProfileVersionsListResponse`, `JsonResumeResponse`, `NotificationLedgerEntry`, `NotificationLedgerListResponse`, `DedupGroupSummary` | `api/models.py:159` (insertion point) |

### Non-scope (explicitly deferred — confirmed by audit)

- **Dedup-group writer** — surfacing `dedup_group_ids` on `JobResponse` ships as `Optional[list[int]] = None`. Actual population (the deduplicator returning groups instead of just winners) is deferred to a follow-up batch because it requires a `job_dedup_groups` table or a redesign of `deduplicator.deduplicate()` return type. Step 2's `DedupGroupBadge` will render fallback "no group info" until the writer lands; not a blocker.
- **Date-confidence ternary fix** — `date_confidence` is binary in practice (low/high). S3 plan's "green/yellow/red pill" will render only green and red. Defer the "infer medium" heuristic to Step 4 (data-quality batch).
- **`mode=hybrid` toggle in default config** — `SEMANTIC_ENABLED=false` is repo default. Step 2's hybrid toggle works only when ops flips the flag. Document it; don't fix in this batch.
- **Notification body in ledger** — `notification_ledger` schema has no `body` column. UI can show status + timestamp + retry count, not message content. Schema change defer.
- **Frontend types mirror** — types.ts updates for new ProfileResponse fields land in Step 2 cohort D (no point updating types.ts here when Step 2 will rewrite the same lines). Step 2's frontend agent picks them up.

---

## Tool orchestration — what each tool does

### Ralph Loop (outer driver)

**Invocation:** `/ralph-loop` with `completion_promise: "STEP-1.5-GREEN"` and `max_iterations: 15` (Step 1 burned ~12 cohort runs in one supervisory iteration; this batch is 30% smaller — expect 6-10 iterations).

**Each iteration:**
1. Check sentinel: does `.claude/step-1-5-verified.txt` exist? If yes, emit `STEP-1.5-GREEN` and halt.
2. Run `make verify-step-1.5` (added in iteration 1 as a new Makefile target).
3. Parse output → identify which blockers are still red.
4. Dispatch the cohort of sub-agents that matches the current dependency stage.
5. Re-run verification, write/update sentinel if all green.
6. Commit partial progress with a conventional-commit prefix.

**Stop criteria (all must hold before emitting `STEP-1.5-GREEN`):**
- All 8 S1.1 blockers + 6 S1.5 blockers + 7 S3-MVP blockers landed (21 commits + ~3 fix-ups expected)
- Pytest gate: `cd backend && python -m pytest tests/ --ignore=tests/test_main.py -q -p no:randomly` returns ≥1,056p / 0f / 4s (baseline preserved; +30-50 new tests acceptable)
- **Critical assertion (the one Step 1 should have shipped):** `bootstrap_dev.py` end-to-end returns a `JobResponse` where at least one job has all 7 dims non-zero. This is the value-presence test, not just schema-presence.
- New endpoints respond 200 with valid bodies under authenticated `httpx.AsyncClient` smoke tests.
- `staleness_state` transitions through at least one `'active' → 'possibly_stale'` cycle in a deterministic test fixture.
- `SEMANTIC_ENABLED=true` smoke: a CV parse populates at least one `SkillEntry.esco_uri` non-null. Skip-gracefully when ESCO data not present (no crash).
- `make verify-step-1.5` exits 0.
- Sentinel file `.claude/step-1-5-verified.txt` contains the final green commit SHA.

### Parallel sub-agents (inner execution — 3 cohorts)

Driven by skill `superpowers:dispatching-parallel-agents`. Cohorts X→Y→Z run in order; within a cohort, agents are parallel.

#### Cohort X — Persistence + state machine wiring (Iterations 1-3)

Foundation: schema + writer changes that everything downstream depends on.

| Agent | Blockers | Files touched | Skills |
|---|---|---|---|
| **Agent-DimPersist** | S1.1-A, B, C, D | `backend/migrations/0011_score_dimensions.up.sql` (NEW), `backend/migrations/0011_score_dimensions.down.sql` (NEW), `backend/src/models.py` (Job dataclass: 9 new fields), `backend/src/main.py:525-530` (capture all 9 fields), `backend/src/repositories/database.py:154-199` (`insert_job()` persists 9 columns) | `/implement` + `superpowers:test-driven-development` |
| **Agent-StalenessWriter** | S1.5-A, B, C | `backend/src/main.py:168-213` (call `transition()` in `_ghost_detection_pass`), `backend/src/repositories/database.py` (new `update_staleness_state()` helper; `mark_missed_for_source()` recomputes via `transition()`), `backend/tests/test_ghost_detection.py` (new integration test) | `/implement` + `superpowers:test-driven-development` |

**Gate before cohort Y:** `pytest tests/test_models.py tests/test_database.py tests/test_ghost_detection.py -v` all green; `git grep "role INTEGER" backend/migrations/` shows 0011 contains all 9 columns; SQLite check shows the columns persisted on a real `bootstrap_dev.py` run.

**Conflict-avoidance:** Agent-DimPersist + Agent-StalenessWriter both touch `database.py`. Sequence them — DimPersist first (migration must land), then StalenessWriter. The other files are conflict-free.

#### Cohort Y — Read-side surfacing + ESCO + tiering (Iterations 4-6)

Depends on cohort X (columns must exist before they can be SELECTed and surfaced).

| Agent | Blockers | Files touched | Skills |
|---|---|---|---|
| **Agent-Serializer** | S1.1-E, F, G, H | `backend/src/repositories/database.py:535-548` (`_JOBS_ENRICHMENT_JOIN_COLS` adds 9 score columns), `backend/src/api/routes/jobs.py:93-158` (`_row_to_job_response()` populates 9 dim fields), `backend/src/api/models.py:46-50` (replace the admission comment), `backend/tests/test_api.py` (add value-presence test) | `/implement` + `superpowers:test-driven-development` |
| **Agent-ESCO** | S1.5-D, E | `backend/src/services/profile/cv_parser.py:295-371` (call `normalize_skill` after line 302; populate `SkillEntry.esco_uri`), gated on `SEMANTIC_ENABLED`, `backend/src/services/profile/skill_entry.py` (no signature change, but new tests), `backend/tests/test_cv_parser_esco.py` (new) | `/implement` + `superpowers:test-driven-development` |
| **Agent-Tiering** | S1.5-F | `backend/src/api/models.py` (extend `ProfileResponse` with `skill_tiers: dict[str, list[str]]` shape), `backend/src/api/routes/profile.py:57-68` (compute tiers via `tier_skills_by_evidence()` in the GET handler), `backend/tests/test_profile_response.py` (extend) | `/implement` + `superpowers:test-driven-development` |

**Gate before cohort Z:** `_row_to_job_response()` value-presence test green; ESCO smoke (with test fixture data dir) populates esco_uri; ProfileResponse round-trip test confirms tiers structure.

**Conflict-avoidance:** Agent-Tiering and Agent-Serializer both touch `api/models.py` (different models — `ProfileResponse` vs `JobResponse`). Safe in parallel — different lines.

#### Cohort Z — New endpoints + ProfileResponse expansion + Pydantic models (Iterations 7-9)

Depends on cohorts X+Y (data must round-trip cleanly before new endpoints surface it).

| Agent | Blockers | Files touched | Skills |
|---|---|---|---|
| **Agent-Endpoints** | S3-A, B, C, D, G | `backend/src/api/routes/profile.py:166` (3 new routes), `backend/src/api/routes/notifications.py` (NEW file with `GET /notifications`), `backend/src/api/main.py` (register the new router), `backend/src/api/models.py:159` (5 new Pydantic models), `backend/src/repositories/database.py` (new `get_notification_ledger()` reader), `backend/tests/test_profile_versions_endpoint.py` (NEW), `backend/tests/test_notifications_endpoint.py` (NEW) | `/implement` + `superpowers:test-driven-development` |
| **Agent-ProfileExpand** | S3-E | `backend/src/api/models.py` (`ProfileResponse` adds `skill_provenance: dict[str, list[str]]`, `linkedin_subsections: dict`, `github_temporal: dict`, `current_version_id: Optional[int]`), `backend/src/api/routes/profile.py:57-68` (populate the new fields), `backend/tests/test_profile.py` (extend) | `/implement` + `superpowers:test-driven-development` |
| **Agent-DedupSurface** | S3-F | `backend/src/api/models.py` (`JobResponse.dedup_group_ids: Optional[list[int]] = None`), no caller changes (defaults to None until writer lands), `backend/tests/test_api.py` (add field-presence test) | `/implement` |

**Gate before STEP-1.5-GREEN:** All 4 new endpoints respond 200 under auth; `ProfileResponse` returns provenance + tiers + sub-sections + temporal; full pytest sweep green; `make verify-step-1.5` exits 0.

### Skills (invoked inside agents)

| Skill | Used by | Purpose |
|---|---|---|
| `superpowers:writing-plans` | this document | already in use |
| `superpowers:executing-plans` | Ralph Loop iteration orchestrator | picks the next unfinished blocker from the cohort DAG |
| `superpowers:dispatching-parallel-agents` | each iteration's agent dispatch | batches cohort X/Y/Z agents |
| `superpowers:test-driven-development` | every agent except DedupSurface | RED-first discipline; agents write the failing test first |
| `superpowers:verification-before-completion` | Ralph Loop gate | cannot emit `STEP-1.5-GREEN` until `make verify-step-1.5` passes including the value-presence assertion |
| `superpowers:systematic-debugging` | Agent-StalenessWriter, Agent-ESCO | if the state machine doesn't transition or ESCO returns no matches, isolate root cause not symptom |
| `superpowers:receiving-code-review` | reviewer worktree feedback cycle | respond with evidence, not performance |
| `commit` | end of each iteration | conventional-commit partial-progress snapshot |
| `update-config` | iteration 1 | add `make verify-step-1.5` target + any new permission allowlist entries |

### MCP servers (nice-to-have inside agents)

| MCP | Used by | Purpose |
|---|---|---|
| Context7 | Agent-Endpoints | fetch current Pydantic v2 + FastAPI route-grouping docs when wiring 4 new endpoints |
| Context7 | Agent-ESCO | sentence-transformers lazy-import patterns for the optional ESCO path |
| IDE diagnostics (`mcp__ide__getDiagnostics`) | every agent post-edit | catch typing errors before commit (especially the 9 new dim columns + 6 new Pydantic models) |
| Chrome DevTools / Playwright | NOT needed in this batch — no UI work |
| coderabbit:code-reviewer | Agent-DimPersist + Agent-StalenessWriter (correctness-critical) | optional second-opinion review on the migration + state machine wiring |

### Subagent types (framework-level)

| Subagent type | Usage |
|---|---|
| `Explore` | already used in 3 audit waves; not needed during execution |
| `Plan` | not used — anchors are pinned, ready to execute |
| `feature-dev:code-reviewer` | Ralph Loop **final iteration only**: review the accumulated S1.5 diff (~25 files, ~1,500 LOC) before emitting `STEP-1.5-GREEN` |
| `codex:codex-rescue` | escape hatch — invoke if three Ralph iterations pass without progress on a specific blocker |

---

## Dependency DAG

```
                    ┌─────────────────────────────────────────┐
                    │              COHORT X                    │
                    │  S1.1-A → S1.1-B → S1.1-C → S1.1-D       │
                    │  S1.5-A → S1.5-B → S1.5-C                │
                    │  (DimPersist runs before StalenessWriter)│
                    │  Migration 0011 must land before reads   │
                    └─────────────┬───────────────────────────┘
                                  │
                    ┌─────────────▼───────────────────────────┐
                    │              COHORT Y                    │
                    │  S1.1-E → S1.1-F → S1.1-G → S1.1-H       │
                    │  S1.5-D → S1.5-E (ESCO from CV parser)   │
                    │  S1.5-F (skill tiering on ProfileResponse│
                    │  Serializer/ESCO/Tiering all parallel    │
                    └─────────────┬───────────────────────────┘
                                  │
                    ┌─────────────▼───────────────────────────┐
                    │              COHORT Z                    │
                    │  S3-A,B,C (profile version + JSON Resume)│
                    │  S3-D (notifications endpoint)           │
                    │  S3-E (ProfileResponse expansion)        │
                    │  S3-F (JobResponse.dedup_group_ids)      │
                    │  S3-G (6 new Pydantic models)            │
                    │  Endpoints/ProfileExpand parallel;       │
                    │  DedupSurface trivial                    │
                    └─────────────────────────────────────────┘
```

Each arrow is a hard dependency. Cohort-X internal sequencing: Agent-DimPersist must finish before Agent-StalenessWriter (both touch `database.py`). Cohort-Y is fully parallel. Cohort-Z is fully parallel except Agent-Endpoints + Agent-ProfileExpand both touch `api/models.py` — sequence them, with Endpoints first (it adds 5 NEW models; ProfileExpand modifies an existing one).

---

## Critical files to modify (with reuse notes)

| File | Action | Reuse from existing code |
|---|---|---|
| `backend/migrations/0011_score_dimensions.up.sql` | **NEW** — `ALTER TABLE jobs ADD COLUMN role/skill/seniority_score/experience/credentials/location_score/recency/semantic/penalty INTEGER DEFAULT 0` | mirror `0010_run_log_observability.up.sql` idempotent ALTER pattern |
| `backend/migrations/0011_score_dimensions.down.sql` | **NEW** — rebuild-and-rename pattern (SQLite < 3.35) | mirror `0010_run_log_observability.down.sql` |
| `backend/src/models.py` | Add 9 score-dim fields to `Job` dataclass: `role: int = 0`, etc. | mirror existing `match_score: int = 0` style |
| `backend/src/main.py:525-530` | Capture every field of `breakdown` to the `Job` dataclass: `job.role = breakdown.title_score; job.skill = breakdown.skill_score; …` | use existing `breakdown = scorer.score(job)` line (already in place) |
| `backend/src/repositories/database.py:154-199` | `insert_job()` persists all 9 dim columns — extend INSERT statement | follow existing `match_score` parameter pattern |
| `backend/src/repositories/database.py:535-548` | `_JOBS_ENRICHMENT_JOIN_COLS` adds 9 `j.role, j.skill, …` columns | mirror existing column list |
| `backend/src/repositories/database.py` | New helper `update_staleness_state(normalized_key, new_state)` | mirror `update_last_seen()` pattern |
| `backend/src/repositories/database.py` | New helper `get_notification_ledger(user_id, limit, offset, channel?, status?)` reader | first SELECT-based reader for this table; pattern from `get_recent_jobs_with_enrichment` |
| `backend/src/repositories/database.py` | `mark_missed_for_source()` post-increment: read each job's `consecutive_misses` + `last_seen_at`, call `transition()`, UPDATE `staleness_state` | the missing wiring point |
| `backend/src/main.py:168-213` | `_ghost_detection_pass()` — after `mark_missed_for_source()`, call `db.recompute_staleness_for_missed(seen, source)` | wraps the new state-machine wiring |
| `backend/src/api/routes/jobs.py:93-158` | `_row_to_job_response()` populates 9 dim fields from row dict — `role=row.get("role", 0), skill=row.get("skill", 0), …` | follow existing `experience_level=row.get("experience_level", "")` pattern |
| `backend/src/api/models.py:46-50` | Replace the admission comment with documentation of the new persisted columns | small comment update |
| `backend/src/services/profile/cv_parser.py` | After line 302 `skills = _coerce_str_list(result.get("skills"))`: optionally call `normalize_skill()` per skill when `SEMANTIC_ENABLED` is set; build `SkillEntry` rows | lazy-import `from src.services.profile.skill_normalizer import normalize_skill`, fail-soft |
| `backend/src/api/routes/profile.py:166` | Add 3 new routes after the `POST /profile/github` block | mirror existing `@router.get/post` pattern; use `Depends(require_user)` + `Depends(get_db)` |
| `backend/src/api/routes/notifications.py` | **NEW file** — `GET /notifications` paginated, filtered by `user.id` | mirror `routes/jobs.py` pagination + auth pattern |
| `backend/src/api/main.py` | Register new `notifications` router | mirror existing router-include pattern |
| `backend/src/api/models.py:124-149` | Extend `ProfileResponse` and `CVDetail` with new fields: `skill_provenance`, `skill_tiers`, `linkedin_subsections`, `github_temporal`, `current_version_id` | extend existing dataclass-style model |
| `backend/src/api/models.py:159` | Add 5 new Pydantic models: `ProfileVersionSummary`, `ProfileVersionsListResponse`, `JsonResumeResponse`, `NotificationLedgerEntry`, `NotificationLedgerListResponse`, `DedupGroupSummary` | mirror existing model-style |
| `backend/src/api/routes/profile.py:57-68` | `GET /profile` populates the new ProfileResponse fields by calling `tier_skills_by_evidence()` and walking `cv_data.linkedin_*` / `github_*` fields | reuse existing `get_profile()` handler shape |
| `Makefile` | Add `verify-step-1.5` target — aggregates the verification gate commands below | use existing `verify-step-1` target as template |
| `CLAUDE.md` | Add rule #21: "When a new field is added to the engine-side scoring/enrichment, a serializer test must verify it round-trips through the API response with non-zero/non-null values for a known fixture. Schema-presence tests are insufficient." | mirror existing rule format |

---

## Verification section

Ralph Loop cannot emit `STEP-1.5-GREEN` until ALL of this passes.

### Gate command

```bash
make verify-step-1.5
```

Which runs (in order):

```bash
# 1. Backend regression — must stay ≥1,056p/0f/4s
cd backend
python -m pytest tests/ --ignore=tests/test_main.py -q -p no:randomly
# Expect: ≥1,056 passed / 0 failed / 4 skipped (new: ~30-50 from cohorts X-Z)

# 2. THE STEP-1 EXIT CRITERIA THAT WAS NEVER VERIFIED
# Bombshell-1 backfill — at least one job in JobResponse has all 7 dims non-zero
python -c "
import asyncio, json
from src.api.dependencies import get_db
from src.api.routes.jobs import _row_to_job_response

async def check():
    db = await get_db()
    rows = await db.get_recent_jobs_with_enrichment(days=30, min_score=30)
    if not rows:
        raise SystemExit('no rows in DB — run bootstrap_dev.py first')
    found_nonzero = False
    for row in rows:
        resp = _row_to_job_response(row)
        if any(getattr(resp, dim) > 0 for dim in
               ('role','skill','seniority_score','experience',
                'credentials','location_score','recency','semantic')):
            found_nonzero = True
            break
    if not found_nonzero:
        raise SystemExit('all jobs have zero dims — Step-1 backfill failed')
    print('OK — at least one job has non-zero dims')
asyncio.run(check())
"
# Expect: exits 0 with 'OK — at least one job has non-zero dims'

# 3. Migration 0011 idempotent — round-trip a Job with dim values
python -c "
import asyncio
from src.models import Job
from src.repositories.database import JobDatabase

async def check():
    db = JobDatabase(':memory:')
    await db.initialize()
    j = Job(
        title='t', company='c', apply_url='u', source='s', date_found='2026-04-25',
        match_score=85, role=35, skill=30, location_score=8, recency=6,
        seniority_score=4, semantic=2,
    )
    await db.insert_job(j)
    rows = await db.get_recent_jobs(days=9999)
    assert rows[0]['role'] == 35, rows[0]
    assert rows[0]['skill'] == 30, rows[0]
    print('OK — dim columns round-trip')
asyncio.run(check())
"
# Expect: exits 0

# 4. Ghost-detection state machine actually transitions
python -m pytest tests/test_ghost_detection_integration.py -v
# Expect: at least one test forces a job through 'active' → 'possibly_stale'
# via consecutive_misses >= 2 + age >= 12h, with DB write verified

# 5. ESCO normalisation activates from CV parser when flag set
SEMANTIC_ENABLED=true python -m pytest tests/test_cv_parser_esco.py -v
# Expect: with fixture ESCO data, at least one SkillEntry.esco_uri is populated

# 6. New endpoints respond 200 under auth
python -m pytest tests/test_profile_versions_endpoint.py tests/test_notifications_endpoint.py -v
# Expect: GET /profile/versions returns list, POST /restore returns restored profile,
# GET /profile/json-resume returns JSON Resume schema, GET /notifications returns paginated

# 7. ProfileResponse expansion round-trip
python -m pytest tests/test_profile_response_expansion.py -v
# Expect: GET /profile returns skill_provenance + skill_tiers + linkedin_subsections + github_temporal

# 8. Bootstrap dogfood — end-to-end proof
python main.py &
BACKEND_PID=$!
sleep 3
ENRICHMENT_ENABLED=false python scripts/bootstrap_dev.py
# Expect: exits 0; final JobResponse has match_score > 0 AND >=1 dim non-zero
kill $BACKEND_PID

# 9. Frontend boot smoke (no UI changes in this batch, but build must pass)
cd ../frontend
npm run build
# Expect: exits 0 — no TS errors from any models.py changes that frontend imports

# 10. Pre-commit gate
cd ..
pre-commit run --all-files
# Expect: all hooks pass (ruff, trailing whitespace, EOF)
```

### Sentinel write (after gate passes)

```bash
echo "$(git rev-parse HEAD)" > .claude/step-1-5-verified.txt
git add .claude/step-1-5-verified.txt
git commit -m "chore(step-1.5): write sentinel at green commit"
# Ralph Loop sees this on next iteration, emits STEP-1.5-GREEN, halts.
```

### End-to-end proof (human check after sentinel)

1. `git log --oneline main..step-1-5-batch` shows ~21 partial-progress commits + 1 final merge
2. `git diff --stat main..step-1-5-batch` shows ~25 files changed, ~1,500 LOC
3. With backend running, `curl localhost:8000/api/jobs/<id>` returns a JSON body where `role`, `skill`, etc. are **non-zero integers**, not 0
4. With backend running, `curl localhost:8000/api/profile/versions -b session-cookie` returns a list with at least one entry (since `save_profile()` writes versions on every CV upload)
5. With backend running + `SEMANTIC_ENABLED=true` + ESCO fixture data, uploading a CV results in `SkillEntry.esco_uri` being populated for known ESCO concepts ("Python" → ESCO concept URI)
6. After 3 consecutive `run_search` cycles where job X is missing, DB shows `staleness_state='possibly_stale'` for job X
7. `docs/IMPLEMENTATION_LOG.md` has a "Step 1.5" entry with test delta + 21-blocker closure table

---

## Execution budget

- Ralph Loop: max 15 iterations (expect 6-10 with full scope)
- Wall-clock: 2-3 sessions (smaller than Step 1; one supervisory iteration with parallel cohorts likely)
- Commits: 1 per blocker (~21) + 1 final merge commit to `main`
- Branch: `step-1-5-batch` off `main @ 17ccdf0`
- Worktree: **dual-worktree** (generator implements; reviewer audits independently). Fast-forward both from previous tip in iteration 1.
- Merge strategy: **fast-forward only** to main; reviewer signs off before merge
- Tag: `step-1-5-green` on the final commit (mirrors Step-1 precedent)

---

## Acknowledged trade-offs

- **One Ralph Loop instead of three** — Bundles three mini-batches that share `database.py` write paths and `api/models.py` schema lines. Splitting them would triple the merge-friction and reviewer overhead for the same set of blockers. Accepted. Cohort gating preserves dependency safety inside the loop.
- **Migration 0011 is irreversible-ish** — SQLite ALTER TABLE ADD COLUMN is forward-only. The down-migration uses the rebuild-and-rename pattern documented in `0010_run_log_observability.down.sql`. Accepted because per-dim score columns are additive — no caller is harmed by them existing as 0 in legacy rows.
- **Dedup-group writer deferred** — `JobResponse.dedup_group_ids` ships as `Optional[list[int]] = None`. The actual writer (deduplicator returning groups + persistence) is a follow-up batch. Step 2's `DedupGroupBadge` will render fallback "no group info" until then. Trade-off: ship the field shape now so Step 2 can wire the type-safe consumer; backfill the data later.
- **ESCO requires `SEMANTIC_ENABLED=true`** — Without the flag, ESCO normalisation skips silently and `SkillEntry.esco_uri` stays `None`. Step 2's ESCO tooltip will only render in flag-on environments. This matches the existing default-off pattern (CLAUDE.md rule #18) and is the right call — no surprise heavy-dep activation.
- **Step-1 reviewer missed Bombshell-1** — Need a CLAUDE.md rule (#21) requiring value-presence assertions, not just schema-presence, for any new engine-side field. Doc commit included in cohort Z.
- **Notifications endpoint without UI** — Ships now as a Step-2 prerequisite, but no consumer exists in this batch. Step 2's S4 will wire the frontend ledger page. Verified via httpx tests in this batch.

---

## Post-Step-1.5 follow-ups (explicitly tracked here)

Created during planning so nothing is lost:

1. **Dedup-group writer batch.** Modify `deduplicator.deduplicate()` return type to `tuple[list[Job], dict[int, list[int]]]` (winners + dropped_id map). Persist to a new `job_dedup_groups` table or carry through the response. Populate `JobResponse.dedup_group_ids`.
2. **Date-confidence ternary fix.** Source-by-source audit of where `date_confidence='medium'` should be inferred. Touches ~14 source files.
3. **Frontend types.ts mirror** for new ProfileResponse + JobResponse fields. Lands in Step 2 cohort D — explicitly NOT in this batch.
4. **Notification body capture.** `notification_ledger` schema needs a `body TEXT` column to make the history page useful. Migration + retrofit of ledger writers.
5. **CLAUDE.md rule #21** (lands in cohort Z docs commit): value-presence assertions required for new engine-side fields.

---

## Handoff

- **Generator worktree** (`worktree-generator` at `.claude/worktrees/generator`): fast-forward to `main @ 17ccdf0`, branch `step-1-5-batch`, invoke `/ralph-loop` with `completion_promise: "STEP-1.5-GREEN"` and `max_iterations: 15`. Cohorts X→Y→Z per this document.
- **Reviewer worktree** (`worktree-reviewer` at `.claude/worktrees/reviewer`): fast-forward from `4d81397` to `main @ 17ccdf0`, run `make verify-step-1.5` after each cohort commit, produce per-cohort audit reports under `docs/reviews/step-1-5-cohort-{X,Y,Z}-review.md`. Use `feature-dev:code-reviewer` + optionally `coderabbit:code-reviewer` for the migration + state-machine wiring.
- **Main session (this one)**: monitors Ralph Loop progress, answers clarifying questions, merges `step-1-5-batch` → `main` via fast-forward + tags `step-1-5-green` after the reviewer signs off.

---

_Plan written 2026-04-25. Anchor verification: 3 Explore agents on `main @ 17ccdf0` post-Step-1 merge. User-confirmed decisions: **(1)** bundle S1.1 + S1.5 + Step-3-MVP into one Ralph-Loop session because their dependencies are tightly coupled; **(2)** use dual-worktree (generator + reviewer); **(3)** value-presence assertion (not schema-presence) becomes the new exit-criteria standard via CLAUDE.md rule #21; **(4)** dedup-group writer + date-confidence ternary deferred to a follow-up batch._
