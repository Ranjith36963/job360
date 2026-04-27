# Step 1 — Cohort D (Observability + frontend + docs) — Independent Review

**Reviewer:** dual-worktree review session in `.claude/worktrees/reviewer` on branch `worktree-reviewer`
**Generator branch:** `step-1-batch-s1` (HEAD `570e5fe`)
**Review date:** 2026-04-24
**Commits reviewed:**
- `88501f3` — `sync(step-1/B6-frontend)`: mirror JobResponse expansion in types.ts
- `d64e9c6` — `feat(step-1/S1+S2+S3)`: run_uuid contextvar + per-source timer + telemetry dataclasses
- `f1551bc` — `docs(step-1)`: CLAUDE.md rule #20 + IMPLEMENTATION_LOG Step 1 entry + plan annotations
- `570e5fe` — `chore(step-1)`: write sentinel at green commit 1ae1cf8

---

## 1. Summary

**BLOCKED — do not merge until C1 is resolved.**

The `verify-step-1` Makefile target (lines 96, 98) references two scripts that **do not exist on disk**:
- `backend/scripts/verify_lazy_imports.py`
- `backend/scripts/verify_arq_functions.py`

Only three `verify_*.py` scripts are present: `verify_step_0.py`, `verify_dataclass_roundtrip.py`, `verify_migration_race.py`. **The gate cannot reach the final `PASS` echo on a clean clone** — it unconditionally fails at line 96.

The sentinel `.claude/step-1-verified.txt` contains SHA `1ae1cf8e41072cf28d1f8d8d011a7d9762eb01aa`, a **pre-Cohort-D commit**. Cohort D's commits (`d64e9c6`, `f1551bc`, `570e5fe`) are all later. This confirms `make verify-step-1` has not run at the current HEAD. When the gate does run (after the scripts are created), it will overwrite the sentinel with the correct HEAD SHA automatically (Makefile line 103).

All other findings are lower severity. The `JobResponse` mirror in `types.ts` is correct and complete (all 18 fields match exactly). Telemetry dataclasses are structurally clean. CLAUDE.md rule #20 is coherent and non-contradictory. The test suite covers the main telemetry paths, with one notable gap in async task propagation.

**Severity counts:** 1 Critical, 2 Important, 2 Low.

---

## 2. Per-Item Findings

### 1. Frontend mirror — `88501f3`

**Claim:** `types.ts` mirrors the 5 date fields + 13 enrichment fields added to backend `JobResponse` in `7ee6dc1`.

**Field-by-field comparison — `backend/src/api/models.py` vs `frontend/src/lib/types.ts`:**

Date fields (5/5 correct):

| Backend | TypeScript | Match |
|---|---|---|
| `posted_at: Optional[str]` | `posted_at?: string \| null` | Yes |
| `first_seen_at: Optional[str]` | `first_seen_at?: string \| null` | Yes |
| `last_seen_at: Optional[str]` | `last_seen_at?: string \| null` | Yes |
| `date_confidence: Optional[str]` | `date_confidence?: string \| null` | Yes |
| `staleness_state: Optional[str]` | `staleness_state?: string \| null` | Yes |

Enrichment fields (13/13 correct): `title_canonical`, `seniority`, `employment_type`, `workplace_type`, `visa_sponsorship`, `salary_min_gbp`, `salary_max_gbp`, `salary_period`, `salary_currency_original`, `required_skills`, `nice_to_have_skills`, `industry`, `years_experience_min` — all match exactly with correct optional+nullable typing.

**`seniority` vs `seniority_score` rename — verdict: correct and complete.**

The rename separates two concepts: integer score dimension (`seniority_score`, 0-10) vs enrichment enum string (`seniority`). The chain is sound:
- `types.ts:23` — `seniority_score: number` (non-optional integer dim)
- `types.ts:45` — `seniority?: string | null` (optional enrichment enum)
- `page.tsx:312` — `seniority: job.seniority_score` (maps integer field into ScoreRadar prop keyed `seniority`)
- `ScoreRadar.tsx:20` — prop interface accepts `seniority: number`

No stale callers. ScoreRadar's internal prop keeps `seniority` as an axis key (display label) while the data source is `job.seniority_score`. Intentional and correct.

#### Finding C2 (Important, confidence 85) — `CVDetail` schema drift between backend and frontend

`backend/src/api/models.py CVDetail` (lines 124–143) has six fields **absent from `frontend/src/lib/types.ts CVDetail`** (lines 90–98):
- `companies: list[str]`
- `name: str`
- `headline: str`
- `location: str`
- `achievements: list[str]`
- `highlights: list[str]` (described as the data source for in-text highlighting in the CV viewer)

This drift **predates Cohort D** and is not introduced by `88501f3`. However, the `highlights` omission means the frontend CV viewer silently ignores the highlighting feature. Cohort D had an opportunity to fix it as part of the type-mirror sync but did not.

**Verdict for `88501f3`:** PASS on stated scope (`JobResponse`). C2 is pre-existing drift outside the commit's declared scope.

---

### 2. Telemetry — `d64e9c6`

**S1 — `run_uuid` ContextVar propagation**

`_run_uuid_var` is a `ContextVar` defined in `backend/src/utils/logger.py:14` with `default=None`. `set_run_uuid()` calls `_run_uuid_var.set(uuid_str)`.

In `run_search()` (`backend/src/main.py:332`), `set_run_uuid(run_uuid)` is called before any `asyncio.gather` or task creation. CPython 3.7+ copies the current `Context` into each new `Task`. Because the set happens before the scheduler's `tick()` call, all source-fetch coroutines inherit the value. Propagation is correct for the current code path.

#### Finding C3 (Important, confidence 82) — no async-boundary propagation test

`backend/tests/test_telemetry.py` tests the contextvar in synchronous paths only:
- Line 48: synchronous `_CaptureHandler` + `_RunUuidFormatter`
- Line 66: `contextvars.copy_context().run(lambda: ...)` — synchronous runner

There is **no test that uses `asyncio.create_task()` or `asyncio.gather()`** and asserts a child task can read the same `run_uuid`. The hardest failure mode — a future code change that creates tasks *before* `set_run_uuid` is called — would produce silent correlation-ID loss with no failing test. Given the scheduler dispatches tasks internally, this is the primary production path and it is uncovered.

**S2 — per-source timer double measurement (Low, L1)**

`_instrument` in `main.py:439–454` nests `source_timer(src.name)` inside an outer `started_ns`/`elapsed_ms` block. Both measure the same interval. The `per_source_duration` dict is populated from the outer `finally`; the debug log line uses `timer.duration_ms` from inside the context manager. These two values will differ by the overhead of the `source_timer` setup (nanosecond range). Not a functional bug, but redundant — one of the two should be the source of truth.

**S3 — telemetry singletons (Low, L2)**

`EnrichmentTelemetry`, `EmbeddingsTelemetry`, `HybridTelemetry` are module-level singletons in `backend/src/utils/telemetry.py`. Counter increments (`tel.llm_calls += 1`) are non-atomic read-modify-writes. Safe under single-process async; risk activates only if `pytest-xdist` is introduced.

**Test quality assessment.**

276 lines across 9 tests. Coverage is substantive:
- Round-trip DB persistence of `run_uuid` with real `aiosqlite` (test line 77)
- `source_timer` with real 20ms sleep (`duration_ms >= 10`)
- `EnrichmentTelemetry` counter via monkeypatched `enrich_batch`
- `HybridTelemetry` fallback reasons for empty-index and None semantic_fn
- `SEMANTIC_ENABLED=false` inert path — rule #18 compliance verified

The flag-off inert path test is a genuine quality signal. C3 gap aside, tests are not superficial.

---

### 3. Docs — `f1551bc`

**CLAUDE.md rule #20 — coherence check.**

Rule #20 states callers must pass `user_preferences` AND `enrichment_lookup` together or pass neither. Direct extension of rule #19 (which permits combined or neither). No contradiction with rules #16/#17/#18. Cross-reference to Step-1 Cohort B (`main.py::run_search` and `workers/tasks.py::score_and_ingest`) is accurate. Coherent.

**IMPLEMENTATION_LOG Step 1 entry.**

`docs/IMPLEMENTATION_LOG.md` lines 16–66 record all 12 blockers with commit SHAs and all 3 observability items attributed to `d64e9c6`. Accurate. **One issue:** the test-count field reads `"Final: TBD (run make verify-step-1 for the actual count)"` — all prior batch entries recorded a concrete count at merge time. Leaving TBD implies the gate has not run, consistent with C1.

**Source-list staleness (Low).** CLAUDE.md's "Free JSON APIs (10)" still lists "YC Companies" (dropped in Batch 3). Pre-existing documentation debt not introduced by Cohort D.

---

### 4. Sentinel — `570e5fe`

**Sentinel file:** `.claude/step-1-verified.txt` contains `1ae1cf8e41072cf28d1f8d8d011a7d9762eb01aa`.

**Analysis.** The commit message is `chore(step-1): write sentinel at green commit 1ae1cf8`. Cohort D commits `d64e9c6` (telemetry) and `f1551bc` (docs) are **later** than `1ae1cf8` in branch history. The sentinel was written at an intermediate commit, then Cohort D landed on top **without re-running the gate**.

`verify-step-1` writes `git rev-parse HEAD` to the sentinel (Makefile:103). If it had run at `570e5fe` the sentinel would contain `570e5fe...`. It contains `1ae1cf8...`. **The gate has not run at the current HEAD.** Consistent with C1: the gate cannot run because two scripts are absent.

No scenario in which the stale sentinel is benign. Either fix C1 and re-run (sentinel auto-updates), or the sentinel is misleading.

---

## 3. Gate Hygiene

`verify-step-1` script inventory:

| Makefile line | Script | Exists? |
|---|---|---|
| 82 | `scripts/verify_migration_race.py` | YES |
| 84 | `scripts/verify_dataclass_roundtrip.py` | YES |
| 96 | `scripts/verify_lazy_imports.py` | **NO** |
| 98 | `scripts/verify_arq_functions.py` | **NO** |

**Confirmed by reviewer:** `ls backend/scripts/verify_*.py` returns exactly:
```
backend/scripts/verify_dataclass_roundtrip.py
backend/scripts/verify_migration_race.py
backend/scripts/verify_step_0.py
```

The gate exits non-zero at line 96 before reaching line 98.

**Additional gate hygiene observation:** `make` is not available on Windows by default. Reviewer had to enumerate steps manually. A `make.bat` shim or `python -m invoke` runner would close this CI portability gap. (Not a Cohort-D regression — pre-existing.)

---

## 4. Findings Table

| ID | Severity | Confidence | Location | Description |
|---|---|---|---|---|
| **C1** | **Critical** | 100 | `Makefile:96,98` | `verify_lazy_imports.py` and `verify_arq_functions.py` MISSING. `make verify-step-1` unconditionally fails. Sentinel SHA `1ae1cf8` confirms gate has not run at current HEAD. |
| C2 | Important | 85 | `frontend/src/lib/types.ts:90–98` | `CVDetail` interface missing 6 backend fields: `companies`, `name`, `headline`, `location`, `achievements`, `highlights`. Pre-existing drift; `highlights` omission silently breaks the CV viewer in-text highlight feature. |
| C3 | Important | 82 | `backend/tests/test_telemetry.py` | No test for `run_uuid` propagation across async task boundaries. All `run_uuid` tests run in synchronous contexts. A refactor moving task creation before `set_run_uuid` would silently lose correlation IDs. |
| L1 | Low | 80 | `backend/src/main.py:443–452` | Dual timer in `_instrument`: `source_timer` and outer `started_ns` both measure the same interval. Log and DB may emit marginally different duration values. Design note. |
| L2 | Low | 80 | `backend/src/utils/telemetry.py:80–109` | Module-level singleton counters use non-atomic `+=`. Safe under single-process async. Risk activates only if `pytest-xdist` is introduced. |

---

## 5. Approval Gate

**NOT APPROVED FOR MERGE.**

**Blocker:** C1 — two gate scripts are missing. `make verify-step-1` cannot reach green. The sentinel is stale and must be re-written by the gate after the scripts are created.

**Required to unblock:**

1. **Create `backend/scripts/verify_lazy_imports.py`.** Suggested: import each heavy-dep-aware module (`src.services.deduplicator`, `src.services.embeddings`, `src.services.retrieval`, `src.services.channels.dispatcher`) and assert `sentence_transformers`, `chromadb`, `rapidfuzz`, `sklearn` are NOT in `sys.modules` after import (rule #16 compliance check). Exit 0 if all clear, 1 if any leaked.

2. **Create `backend/scripts/verify_arq_functions.py`.** Suggested: with `ARQ_TEST_MODE=1`, import `src.workers.settings.WorkerSettings` and assert `enrich_job_task` and `send_notification` appear in `WorkerSettings.functions` (B10 regression check).

3. **Run `make verify-step-1` to completion at the current HEAD.** The gate will overwrite `.claude/step-1-verified.txt` with the correct SHA automatically.

4. **Update IMPLEMENTATION_LOG test-count field** with the actual final count from the gate run.

C2 and C3 are Important but not merge-blocking for this cohort. C2 is pre-existing drift; C3 is a future-risk gap. Both should be addressed in a follow-up or as part of S1.5. L1 and L2 are design notes, no action required.

---

### Pytest sweep result
`python -m pytest tests/ --ignore=tests/test_main.py -p no:randomly`: **1056 passed, 4 skipped, 1 warning in 300.47s** (exit 0). The generator's claim of 1,056p / 0f / 3s is confirmed (skip count is 4, not 3 — the plan said 3; minor inaccuracy, not a regression). The 1 warning is a `RuntimeError: Event loop is closed` from aiosqlite teardown — a known Python 3.13 + Windows shutdown race, not introduced by this branch.

**Update the IMPLEMENTATION_LOG TBD field with `1056p / 0f / 4s`** as part of the unblock pass (item 4 below).

The Critical finding C1 stands independent of the pytest result — it is a structural fact about the Makefile vs the filesystem:
- `ls backend/scripts/verify_*.py` returns exactly 3 files: `verify_dataclass_roundtrip.py`, `verify_migration_race.py`, `verify_step_0.py`.
- `Makefile:96,98` references `verify_lazy_imports.py` and `verify_arq_functions.py`, neither of which exists.
- `.claude/step-1-verified.txt` contains `1ae1cf8e4...` (the commit *before* `d64e9c6` and the Cohort D commits at the current HEAD `570e5fe`), proving the gate has never run at HEAD.

`make verify-step-1` cannot be made to pass at HEAD without first creating the two missing scripts.

**Additional note for Windows portability:** `make` itself is unavailable on default Windows shells (Git Bash returns `make: command not found`), forcing reviewers to enumerate gate steps manually. A `make.bat` shim or `python -m invoke` runner would close this CI portability gap; the pre-commit / `verify-step-0` precedent suggests the team values cross-platform parity, so this is worth tracking as a Step-1.5 / Batch-4 follow-up.

### Re-audit @ 9ac434f (2026-04-24) — C1 CLOSED

Fix commit `64f8020` adds both missing scripts. Reviewer ran them at HEAD `9ac434f`:

- `python scripts/verify_lazy_imports.py` → `OK: no heavy modules in sys.modules at SEMANTIC_ENABLED=false`
- `ARQ_TEST_MODE=1 python scripts/verify_arq_functions.py` → `OK: WorkerSettings.functions includes ['enrich_job_task', 'score_and_ingest']`

`.claude/step-1-verified.txt` now contains `64f80208679287b3eb798b4cebe176dc700c3881` — the post-fix HEAD, written by `9ac434f`. The sentinel-vs-HEAD divergence that originally proved the gate had not run is closed. **C1 closed.**

C2 (CVDetail schema drift) and C3 (no async-boundary `run_uuid` test) remain as tracked follow-ups; both are Important but not merge-blocking, per the original gate.

_Signed: dual-worktree reviewer session, 2026-04-24_
