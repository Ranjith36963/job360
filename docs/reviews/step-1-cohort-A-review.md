# Step 1 — Cohort A (Foundation) — Independent Review

**Reviewer:** dual-worktree review session in `.claude/worktrees/reviewer` on branch `worktree-reviewer`
**Generator branch:** `step-1-batch-s1` (HEAD `570e5fe`)
**Review date:** 2026-04-24
**Reviewer agent:** `feature-dev:code-reviewer` dispatched from `worktree-reviewer`
**Commits reviewed:**
- `cec914f` — `fix(step-1/B1+B2)`: surface first_seen_at/last_seen_at/staleness_state on Job + preserve caller timestamps in insert_job
- `acb9216` — `fix(step-1/B11)`: make migration runner idempotent under concurrent boot
- `9100d6d` — `feat(step-1/B3+B4)`: JobScorer.score returns ScoreBreakdown dataclass + filter compat
- `1ae1cf8` — `fix(step-1/test-debt)`: adapt 3 stale assertions to ScoreBreakdown return type

---

## 1. Summary

**APPROVE WITH NITS.**

All five Cohort A blockers (B1, B2, B3, B4, B11) close correctly. The dataclass field additions, timestamp preservation fix, ScoreBreakdown return-type change, filter compat patch, and migration-runner idempotency fix each do exactly what the plan prescribed. No CLAUDE.md rules are violated. The three test-debt adaptations in `1ae1cf8` are honest: the old tests would have raised `TypeError` under the new `ScoreBreakdown` return type (you cannot compare a dataclass with `>` unless `__gt__` is defined), so changing them to `.match_score` is a genuine adaptation, not a regression cover-up.

Two non-blocking findings are flagged: one Medium (misleading comment in the `IntegrityError` catch of `runner.py`) and one Low (silent `staleness_state` discard in `insert_job`). Both are safe in the current codebase but could mislead future contributors.

---

## 2. Per-Blocker Findings

### B1 + B2 — `cec914f`: Job dataclass fields + insert_job timestamp preservation

**What changed.** Three new `Optional[str]` fields (`first_seen_at`, `last_seen_at`, `staleness_state`) appended to the `Job` dataclass in `backend/src/models.py`. `insert_job` in `backend/src/repositories/database.py` now resolves caller-supplied values against `now` only when the field is `None`, closing the silent-overwrite bug identified in the R2 audit.

**Correctness assessment.** The fix closes B1 and B2 as specified. The plan anchor (`database.py` on the old code using `datetime('now')`) is resolved: those positions now use Python-computed `first_seen_at` / `last_seen_at` variables that respect caller intent. The `staleness_state` field correctly defaults to `None` on the model — `insert_job` does not write it to the INSERT column list, so the DB schema default `'active'` applies on fresh inserts.

**Rule compliance.**
- Rule #1 (`normalized_key`): unchanged. The three new fields are appended after all existing fields; `normalized_key()` is not touched.
- Rule #10 (no `user_id` on `jobs` table): the INSERT does not add a `user_id` column. Satisfied.

**Test coverage.** Two new tests in `test_models.py` (constructor round-trip + None defaults) and two new tests in `test_database.py` (caller-supplied timestamp preservation + None fallback to now). Coverage verdict: adequate.

**Gap (Low, F-L1):** `staleness_state` is not round-tripped by `insert_job`. A caller setting `staleness_state='stale'` will silently get `'active'` in the DB. Correct for new inserts; undocumented as an exclusion.

---

### B11 — `acb9216`: Concurrent-boot migration idempotency

**What changed.** `backend/migrations/runner.py::up()` wraps each per-migration apply cycle in an explicit `BEGIN IMMEDIATE` / re-check / commit pattern. A cheap outer check avoids acquiring the write lock for already-applied stems. Inside the lock, a second read of the applied set catches the race where two processes both saw "pending" before either took the lock. A `sqlite3.IntegrityError` catch on the `_schema_migrations` INSERT acts as belt-and-braces.

**Correctness assessment.** The `BEGIN IMMEDIATE` pattern correctly serialises concurrent writers at the SQLite level. The `busy_timeout=5000` ensures the losing process blocks rather than immediately returning `SQLITE_BUSY`. The re-check inside the transaction is the real concurrency guard.

**Test coverage.** `test_concurrent_up_is_race_safe` added to `test_migrations.py` — runs `asyncio.gather(runner.up(...), runner.up(...))` against the same on-disk temp file, confirms neither coroutine raises, asserts each stem appears exactly once.

**Note.** Plan specified `tests/test_migration_runner_concurrency.py` — landed in existing `tests/test_migrations.py` instead. Functionally equivalent; arguably cleaner.

**Gap (Medium, F-M1).** The `IntegrityError` catch block calls `await db.rollback()`, which undoes not just the failed INSERT but also the entire `_apply_up_sql` body that ran since `BEGIN IMMEDIATE`. The comment "the migration's net effect is already applied" is only true because every current migration SQL file uses `IF NOT EXISTS` guards. A future migration with a bare `CREATE TABLE` would fail on re-execution after this rollback.

---

### B3 + B4 — `9100d6d`: ScoreBreakdown return type + filter compat

**What changed.** `ScoreBreakdown` frozen dataclass (8 dimension fields + `match_score`) added to `backend/src/services/scoring_dimensions.py`. `JobScorer.score()` in `skill_matcher.py` now returns `ScoreBreakdown` instead of `int`. `backend/src/main.py` extracts `breakdown.match_score` and assigns to `job.match_score` before the `MIN_MATCH_SCORE` filter — the filter itself is unchanged. `backend/src/workers/tasks.py` does the same extraction.

**Correctness assessment.** Both code paths (gate-suppressed and full linear) return a `ScoreBreakdown`. The gate-suppressed path correctly returns zeros for non-computed dimensions, matching pre-B3 `score_job()` behaviour.

**Rule compliance.**
- **Rule #19** (legacy callers get legacy formula): explicitly verified. The 7-dim path only activates when both `user_preferences` AND `enrichment_lookup` are passed. Legacy callers passing only `config` see `seniority_score=salary_score=visa_score=workplace_score=0`, and `match_score` equals `title + skill + location + recency ± penalties`, byte-identical to the pre-Step-1 int. The `TestScoreBreakdown.test_legacy_path_match_score_equals_sum_of_four_legacy_components` test confirms this numerically.
- **Rule #16** (lazy heavy deps): no `sentence_transformers`, `chromadb`, or `sklearn` import at module level in the changed files. Satisfied.

**Test coverage.** Six new tests in `TestScoreBreakdown` class (`test_scorer.py`):
- Type check + 9 dimension fields present
- Legacy path: `match_score == sum of 4 legacy components`
- Legacy path: 4 new dim fields all 0
- Prefs-only (no enrichment_lookup) → still 0 dims (AND semantics)
- Gate-suppressed path returns ScoreBreakdown not int

Coverage verdict: thorough.

---

### Test-debt fix — `1ae1cf8`: Adapt 3 stale assertions to ScoreBreakdown

**Honest adaptation vs regression cover-up.** The adaptations are honest. `ScoreBreakdown` is a frozen dataclass without `__lt__`/`__gt__`/`__add__` defined, so any pre-existing code that used `scorer.score(job) > N` would have raised `TypeError` immediately when `score()` changed return type. These were not silently-passing tests masking wrong behaviour — they were tests that would have been hard errors. Routing through `.match_score` preserves the original invariant being tested. No test was silenced, weakened, or removed.

---

## 3. Cross-Cutting Concerns

### ScoreBreakdown ripple to all callers — verified clean

Every call site of `JobScorer.score()` checked:

| File | Pattern | Correct? |
|---|---|---|
| `backend/src/main.py` | `breakdown = scorer.score(job); job.match_score = breakdown.match_score` | Yes |
| `backend/src/workers/tasks.py` | `int(_scorer_for(user_id).score(job).match_score)` | Yes |
| `backend/tests/test_scorer.py` | `.match_score` access throughout | Yes |
| `backend/tests/test_profile.py` | `.match_score` access | Yes |
| `backend/tests/test_main.py` | `from src.services.skill_matcher import ScoreBreakdown` + isinstance | Yes |
| `backend/tests/test_worker_tasks.py` | `from src.services.skill_matcher import ScoreBreakdown` | Yes |

The deduplicator reads `j.match_score` directly from the `Job` object — populated from `breakdown.match_score` in `main.py` before dedup. Pipeline correctness preserved.

The module-level `score_job()` legacy function still returns `int` and is unchanged.

### `staleness_state` in insert_job — silent discard

`insert_job` accepts a Job with `staleness_state` set, but does not include it in the INSERT column list. DB default `'active'` applies. Correct for new inserts. Documented gap for future "restore from backup" flows.

### `normalized_key()` — unchanged. Rule #1 satisfied.
### `jobs` table — no `user_id` added. Rule #10 satisfied.

---

## 4. Findings Table

| ID | Severity | File:Line | Description | Suggested fix |
|---|---|---|---|---|
| F-M1 | Medium | `backend/migrations/runner.py:197-205` | `IntegrityError` catch rolls back the entire `BEGIN IMMEDIATE` transaction including the migration body. Comment "migration's net effect is already applied" is only true because all current migration SQL uses `IF NOT EXISTS` guards. A future migration with a bare `CREATE TABLE` would leave DB inconsistent. | Revise comment to make the IF-NOT-EXISTS dependency explicit; add a doc note in the migrations README that new migrations MUST use idempotent DDL. |
| F-L1 | Low | `backend/src/repositories/database.py:154-199` | `insert_job` does not pass `job.staleness_state` to the INSERT column list. A caller with non-default `staleness_state` silently gets `'active'`. | Add a one-line comment: `# staleness_state is intentionally excluded — DB default 'active' is correct for all new inserts.` |
| F-N1 | Nit | `backend/migrations/runner.py:199-201` | Comment is misleading per F-M1. | Same as F-M1. |
| F-N2 | Nit | `backend/scripts/verify_dataclass_roundtrip.py:40` | Script passes `staleness_state="active"` but does not assert it round-trips. Latent gap. | Optionally add `assert row["staleness_state"] == "active"` (would pass via DB default). |

---

## 5. Approval Gate

**Cohort A is mergeable as-is.**

The two substantive findings (F-M1, F-L1) are non-blocking:

- F-M1 is a future-proofing concern. All existing migration files in the repo use `IF NOT EXISTS`. The risk only materialises if a future migration author drops the idiom — a documentation problem, not a code correctness problem today.
- F-L1 is intentional by design and simply undocumented.

Neither finding causes a test failure, data corruption risk, or violation of any CLAUDE.md rule under current usage.

**Cohort A test contribution:** ~11 new tests across `test_models.py` (+2), `test_database.py` (+2), `test_migrations.py` (+1), `test_scorer.py` (+6). Proportionate against the plan's 25-40 budget across all four cohorts.

---

### Pytest sweep result
`python -m pytest tests/ --ignore=tests/test_main.py -p no:randomly` (mirrors the Makefile sweep): **1056 passed, 4 skipped, 1 warning in 300.47s** (exit 0). The generator's claim of 1,056p stands. The lone warning is a `RuntimeError: Event loop is closed` from aiosqlite teardown — a known Python 3.13 + Windows shutdown race in the aiosqlite library, not a regression introduced by this branch. Cohort A's findings are non-blocking and the suite is green for foundation work.

### Re-audit @ 9ac434f (2026-04-24)
No Cohort-A files were touched in the fix bundle (`64f8020`). Cohort A remained APPROVE-WITH-NITS through the round-trip. F-M1 / F-L1 / F-N1 / F-N2 are still open as documentation/comment improvements; none block merge.

_Signed: dual-worktree reviewer session, 2026-04-24_
