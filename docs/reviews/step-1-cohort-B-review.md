# Step 1 — Cohort B (Multi-dim activation + enrichment wiring) — Independent Review

**Reviewer:** dual-worktree review session in `.claude/worktrees/reviewer` on branch `worktree-reviewer`
**Generator branch:** `step-1-batch-s1` (HEAD `570e5fe`)
**Review date:** 2026-04-24
**Commits reviewed:**
- `f2e7d13` — `feat(step-1/B5)`: wire user_preferences + enrichment_lookup at both JobScorer call sites
- `30cf923` — `feat(step-1/B7)`: ENRICHMENT_THRESHOLD + enrich_batch() with semaphore + gated invocation
- `226cf41` — `feat(step-1/B10)`: register enrich_job_task + enqueue from score_and_ingest + parity test

---

## 1. Summary

**NOT APPROVED.** One **P1 blocker**, one P2 structural weakness in the parity test, one P3 no-op assertion.

The multi-dim activation wiring (B5) is correct and rule #19-compliant — legacy callers passing only `config` continue to get the legacy 4-component formula with no silent flip. `ENRICHMENT_THRESHOLD` gating and semaphore concurrency (B7) are correctly implemented. The ARQ task registration and flag-gating (B10) are correct.

**However: `enrich_batch()` calls the LLM for every high-scored job and then discards all results.** No `save_enrichment()` call exists anywhere inside `_one()`, and the `main.py` call site ignores the return value. The `job_enrichment` table stays empty after every CLI run with `ENRICHMENT_ENABLED=true`. Provider quota is burned, multi-dim scoring gains nothing, and every enrichment-backed field in `JobResponse` remains None. The ARQ `enrich_job_task` path correctly persists via `save_enrichment()`, making the CLI and ARQ paths asymmetric in a way that **directly contradicts B10's parity claim**.

---

## 2. Per-blocker findings

### B5 — Wire user_preferences + enrichment_lookup at both JobScorer call sites

**What changed.** `main.py:385–393` branches on `ENRICHMENT_ENABLED` to either bulk-load enrichment rows via `_build_enrichment_lookup(db._conn)` or fall back to an empty dict, then constructs `JobScorer(search_config, user_preferences=profile.preferences, enrichment_lookup=lambda job: enrichment_lookup_dict.get(getattr(job, "id", None)))`. The worker's `_scorer_for()` in `tasks.py:113–122` mirrors this construction with per-user preferences loaded from the profile store.

**Rule #19 compliance — CONFIRMED.** The multi-dim branch in `skill_matcher.py:508` is guarded by `if self._user_preferences is not None:`. A caller passing only `config` receives `user_preferences=None`. The branch is never entered; `ScoreBreakdown.match_score` is byte-identical to the pre-Step-1 int. No silent flip.

**ENRICHMENT_ENABLED=false path — CONFIRMED.** When the flag is off, `enrichment_lookup_dict = {}`. Every lookup returns `None`. Even with `user_preferences` non-None, `enrichment is not None` at `skill_matcher.py:509` is False, and the four new dimension slots contribute 0. Rule #18 satisfied.

**Verdict: PASSES.**

---

### B7 — ENRICHMENT_THRESHOLD + enrich_batch() with semaphore + gated invocation

**What changed.**
- `settings.py:56` — `ENRICHMENT_THRESHOLD = int(os.getenv("ENRICHMENT_THRESHOLD", "60"))`.
- `job_enrichment.py:89–174` — `enrich_batch()` with `asyncio.Semaphore`, per-job error swallowing, `skip_existing` guard, telemetry counters.
- `main.py:546–556` — invocation gated on `ENRICHMENT_ENABLED`, filtered to `match_score >= ENRICHMENT_THRESHOLD`.

**ENRICHMENT_ENABLED default — CONFIRMED OFF.** Rule #18 satisfied.
**ENRICHMENT_THRESHOLD filtering — CONFIRMED.** Not all jobs are sent to the LLM.
**Semaphore correctness — CONFIRMED.** `test_enrich_batch_respects_semaphore` uses lock-based in-flight counter + `asyncio.sleep(0.01)` to force concurrent scheduling. Asserts `2 <= max_in_flight <= 5`. Structurally sound.

#### CRITICAL BUG — P1 BLOCKER: `enrich_batch()` never persists results

Reading `job_enrichment.py:137–174` in full: `_one()` calls `enrich_job(...)` at line 154 and **returns the result**. There is **no** `await save_enrichment(conn, job_id, enrichment)` call anywhere inside `_one()` or in the outer body.

The call site in `main.py:556`:
```python
await enrich_batch(high_scored, semaphore_limit=10, conn=db._conn)
```
**assigns the return value to nothing.**

**Consequence chain when `ENRICHMENT_ENABLED=true`:**
1. LLM is called for every high-scored job — quota consumed.
2. `JobEnrichment` objects returned by `asyncio.gather`.
3. The gathered list is discarded at the call site.
4. The `job_enrichment` table receives **zero rows**.
5. `_build_enrichment_lookup()` at the top of `run_search` (`main.py:386`) always returns `{}`.
6. All four multi-dim dimension scores remain 0 — no behavioural difference from `ENRICHMENT_ENABLED=false`.
7. `JobResponse` enrichment fields (title_canonical, seniority, required_skills, etc.) remain None.
8. `skip_existing=True` inside `enrich_batch` never short-circuits.
9. **Every subsequent run burns LLM quota again for the same jobs.**

The ARQ `enrich_job_task` in `tasks.py:411–461` correctly calls `await save_enrichment(db, job_id, enrichment)` at line 460. The CLI and ARQ paths are therefore asymmetric: the worker persists, the batch function does not. **B10's parity claim is undermined by this asymmetry.**

No existing test catches this because `test_enrich_batch_respects_semaphore` uses `skip_existing=False` and asserts only on the returned list — it never checks for a DB row.

**Suggested fix.** Inside `_one()` in `job_enrichment.py`, after a successful `enrich_job()` result, persist via `save_enrichment` when `conn` and `job_id` are available:

```python
result = await enrich_job(job, llm_extract_validated_fn=llm_extract_validated_fn)
if result is not None and conn is not None:
    job_id_val = getattr(job, "id", None)
    if job_id_val is not None:
        try:
            await save_enrichment(conn, job_id_val, result)
        except Exception as save_exc:
            logger.warning(
                "enrich_batch: save_enrichment failed for job %s: %s",
                job_id_val, save_exc,
            )
return result
```

**Verdict: FAILS — P1 blocker.**

---

### B10 — Register enrich_job_task + enqueue from score_and_ingest + parity test

**What changed.**
- `workers/settings.py:94` — `enrich_job_task` added to `WorkerSettings.functions`.
- `tasks.py:87–157` — `enrichment_enqueued` flag ensures at most one enqueue per job across multiple users; ENRICHMENT_ENABLED + ENRICHMENT_THRESHOLD guards present.
- `tasks.py:411–461` — `enrich_job_task` implemented with idempotency (`has_enrichment` check first), job row fetch, `enrich_job()` call, `save_enrichment()` persistence.

**Registration — CONFIRMED.** `WorkerSettings.functions` contains `enrich_job_task`. Test passes.
**Flag-gating — CONFIRMED.** Tests verify enqueue occurs when ON and is suppressed when OFF.
**One-enqueue-per-job — CONFIRMED.** Two-user test confirms exactly one enqueue.

#### P2 STRUCTURAL WEAKNESS — parity test is a tautology

`test_cli_arq_scoring_parity` at `test_worker_tasks.py:410–481`:

```python
config = SearchConfig.from_defaults()
cli_scorer = JobScorer(config)               # no user_preferences, no enrichment_lookup
arq_scorer = JobScorer(config)               # identical
cli_breakdowns = [cli_scorer.score(j) for j in sample_jobs]
arq_breakdowns = [arq_scorer.score(j) for j in sample_jobs]
for cli_b, arq_b in zip(cli_breakdowns, arq_breakdowns):
    assert cli_b == arq_b
```

Both scorers use no `user_preferences` and no `enrichment_lookup`. **The multi-dim path is never entered.** The assertion proves that `JobScorer(config).score(job)` is deterministic — a property of pure functions that was trivially true before any of these changes. It does not touch the code paths Cohort B actually changed.

The plan's parity claim (step_1_plan.md line 106 — "same input → identical match_score + ScoreBreakdown from both paths") means **both paths with a real profile and enrichment data**. Neither is exercised here.

**Suggested fix.** Inject a fake `UserPreferences` and a fake enrichment callable returning a non-None `JobEnrichment`. Assert breakdown equality AND assert at least one of `seniority_score / salary_score / visa_score / workplace_score` is non-zero to prove the multi-dim branch ran. Pattern in `test_score_and_ingest_passes_user_prefs_and_enrichment_lookup` (line 267) shows how to inject the profile.

**Verdict: CONDITIONALLY PASSES.** Registration and flag-gating are correctly tested. Parity test is P2 (weak, not a runtime blocker) but does not constitute proof of the multi-dim parity guarantee.

---

### P3 — `test_enrichment_enabled_env_flag_defaults_off` no-op

`test_job_enrichment.py:455–467` reloads the module and asserts:
```python
assert je_mod.ENRICHMENT_ENABLED in (False, True)
```

**Every Python bool is `in (False, True)`.** This test can never fail regardless of flag value. Provides no coverage for rule #18.

**Suggested fix.** Clear env var before reloading and assert `is False`:
```python
monkeypatch.delenv("ENRICHMENT_ENABLED", raising=False)
importlib.reload(je_mod)
assert je_mod.ENRICHMENT_ENABLED is False
```

---

## 3. Cross-Cutting Concerns

**`enrich_job_task` arq import — LAZY. CONFIRMED.** `tasks.py:421–425` imports `enrich_job`, `has_enrichment`, `save_enrichment` inside the function body. `arq` is never imported in `tasks.py`. Rule #11 satisfied.

**`enrich_batch` heavy dep imports — LAZY. CONFIRMED.** Only non-stdlib import inside `enrich_batch` is `from src.utils.telemetry import enrichment_telemetry`. No `sentence_transformers`, `chromadb`, `rapidfuzz`, or `sklearn`. Rule #16 satisfied.

**Worker `_user_profile_for` — DEFENSIVE FALLBACK.** `tasks.py:329–337` wraps `load_profile` in bare `except Exception` with `return None` fallback. Failures fall back to legacy 4-component scoring per rule #19. Correct.

---

## 4. Findings Table

| ID | Severity | File:Line | Description | Suggested Fix |
|----|----------|-----------|-------------|---------------|
| **B7-1** | **P1 — Blocker** | `backend/src/services/job_enrichment.py:137–174`; `backend/src/main.py:556` | `enrich_batch._one()` calls `enrich_job()` but never calls `save_enrichment()`. Caller discards return value. LLM quota consumed; zero rows written; multi-dim scoring + all enrichment API fields silently receive nothing. | Add `await save_enrichment(conn, job_id_val, result)` inside `_one()` after a successful result, guarded by `conn is not None` and `job_id_val is not None`. Add a test that verifies the DB row exists post-call. |
| B10-1 | P2 | `backend/tests/test_worker_tasks.py:410–481` | `test_cli_arq_scoring_parity` constructs both scorers with no `user_preferences` and no `enrichment_lookup`. Assertion is tautological (pure-function determinism). The multi-dim path is never entered. The parity guarantee in the plan is unverified. | Inject fake `UserPreferences` and fake enrichment callable returning a non-None `JobEnrichment`; assert breakdown equality; assert at least one of the four new dimension scores is non-zero. |
| B7-2 | P3 | `backend/tests/test_job_enrichment.py:455–467` | `test_enrichment_enabled_env_flag_defaults_off` asserts `x in (False, True)` — always True. Zero coverage for rule #18. | `monkeypatch.delenv` then reload and `assert je_mod.ENRICHMENT_ENABLED is False`. |

---

## 5. Approval Gate

**NOT APPROVED.**

Finding **B7-1 is a P1 blocker.** `enrich_batch()` makes LLM calls and discards all results, making the entire B7 feature a quota-burning no-op in the CLI pipeline. The ARQ path correctly persists, so the two paths are asymmetric in the exact dimension B10's parity test was supposed to guarantee.

**Required before re-review:**
1. Add `save_enrichment` call inside `enrich_batch._one()` after a successful `enrich_job()` result. Add a DB-assertion test that verifies the row exists post-call.
2. Strengthen `test_cli_arq_scoring_parity` to exercise the multi-dim path with non-None preferences and a non-empty enrichment lookup; assert at least one dimension score is non-zero.
3. (Recommended) Fix `test_enrichment_enabled_env_flag_defaults_off` to assert `is False` rather than `in (False, True)`.

B5 is clean and does not need re-verification.

---

### Pytest sweep result
`python -m pytest tests/ --ignore=tests/test_main.py -p no:randomly`: **1056 passed, 4 skipped, 1 warning in 300.47s** (exit 0). All existing tests pass — and yet **B7-1 is real**. The B7-1 P1 blocker is independently verified by direct code read (`backend/src/services/job_enrichment.py:137-172` shows `_one()` returns `enrich_job(...)` with no `save_enrichment` call anywhere; `backend/src/main.py:556` discards the return). The 1,056-passing test count is precisely the failure mode this review surfaces: the test suite covers the happy paths of new features (`enrich_batch` semaphore concurrency, threshold gating, flag-off no-op) but does not assert that a DB row exists post-`enrich_batch`. Add a row-exists test as part of the B7-1 fix.

### Re-audit @ 9ac434f (2026-04-24) — B7-1 CLOSED

Fix commit `64f8020` adds the `save_enrichment` call inside `_one()` at `backend/src/services/job_enrichment.py:158-170` — guarded by `result is not None and conn is not None and job_id is not None`, with a swallow-and-log pattern on save failure that matches the existing semantics of the function ("never block the batch on a DB hiccup"). New test `test_enrich_batch_persists_results_to_db` (test_job_enrichment.py:619) asserts a row exists in `job_enrichment` after `enrich_batch` completes — the row-exists assertion this review demanded. Targeted re-run: 1p / 0f. **B7-1 closed.**

B10-1 (parity-test tautology) and B7-2 (no-op assertion) remain open as recommended-but-deferred follow-ups, per the generator's hand-off note. They are P2/P3 and do not block merge — track them on the Step-1.5 docket.

_Signed: dual-worktree reviewer session, 2026-04-24_
