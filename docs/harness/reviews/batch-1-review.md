# Batch 1 Review — 2026-04-18 — commit 99c69c0
<!-- doc: LOG -->

## Verdict

[x] APPROVED   [ ] CHANGES REQUIRED

> **Round-1 verdict** was CHANGES REQUIRED at commit `ebf439b`. The
> generator's 4 review-response commits (`a4425cb`, `f525b3b`, `74914a5`,
> `99c69c0`) address every P1 finding with regression tests and reconcile
> the completion entry with what actually ships. Round-2 verdict is
> APPROVED.

Batch 1 delivers the 5-column date model, Job dataclass extension,
confidence-aware recency scorer, ghost-detection state machine +
**production wiring**, 44 source commits (39 fabricators + 3 wrong-field
+ 2 extras), and a freshness KPI exporter (6 live + 4 stubs). Zero
regressions across both review rounds.

---

## Round-2 status of Round-1 findings

### P1-1 — `run_search()` never calls the ghost-detection helpers

**Round 1:** BLOCKING — state machine and DB helpers were unreferenced
outside their own tests; `consecutive_misses` would stay 0 forever.

**Round 2:** ✅ RESOLVED in commit `a4425cb`
(`feat(main): wire ghost detection into run_search() with 70% completeness gate`).

- `_ghost_detection_pass(db, sources, results, history, completeness_threshold=0.7)`
  added as a module-level async helper in `backend/src/main.py:134-179`.
- Call site wired into `run_search()` at `main.py:386-393`, executed
  after the `asyncio.gather` over `source.fetch_jobs()`, inside its
  own `try/except` so an observability failure can never kill the
  pipeline.
- Gate order is correct:
  1. `isinstance(result, BaseException) or result is None` → skip
     (failed scrape, never interpret as "jobs disappeared").
  2. `rolling_avg > 0 and len(result) < 0.7 * rolling_avg` → skip
     absence sweep, log a warning (pillar_3_batch_1.md §3 Step 1).
  3. Otherwise → `update_last_seen` for every observed key, then
     `mark_missed_for_source` for the rest.
- The `rolling_avg > 0` guard correctly allows the very first scrape
  (no history yet) to run the sweep without a safety net — on first
  scrape there are no existing rows for that source to mark, so this
  is benign.
- Three new integration tests in `test_ghost_detection.py:149-221`
  exercise:
  * gate trips below threshold (`{"reed": [2,2,2]}`, current = 1 →
    50% of avg → skip),
  * empty history disables the gate (sweep runs, A observed so B gets
    marked missed once),
  * `RuntimeError` and `None` results never trigger the sweep.
- Integration respects rules #1–#3 (no changes to `normalized_key`,
  `BaseJobSource`, `purge_old_jobs`) — confirmed via
  `git show a4425cb --stat`: only `main.py` and
  `test_ghost_detection.py` touched.

**Collaterally resolves P2-1** (scrape-completeness gate absent).

### P1-2 — `bucket_accuracy_N` is inflated by circular SQL

**Round 1:** BLOCKING — `SELECT … WHERE first_seen_at >= window`
followed by a per-row check that compared `effective` (= `first_seen_at`
for low-confidence rows) against the same window was tautological;
low-confidence rows scored 100% accuracy by construction and
anti-correlated with `date_reliability_ratio`.

**Round 2:** ✅ RESOLVED in commit `f525b3b`
(`fix(ops): bucket_accuracy no longer measures itself against first_seen`).

- SQL filter in `exporter.py:84-89` now reads:
  ```
  WHERE first_seen_at >= ?
  AND date_confidence IN ('high', 'medium', 'repost_backdated')
  ```
  Low-/fabricated-confidence rows are excluded from both numerator and
  denominator, matching pillar_3_batch_1.md §1 and §5 ("Jobs with
  `date_confidence = 'low'` or `'fabricated'` should never enter the
  'last 24h' bucket").
- Per-row loop simplified to `effective = posted_at or first_seen`
  since every row in the result set now has trustworthy confidence —
  `first_seen` remains as a safety fallback for an edge case where
  `posted_at` is NULL despite trustworthy confidence (shouldn't happen
  by contract, but defensive).
- Docstring rewritten at `exporter.py:62-77` to explain why the metric
  must exclude low-confidence rows — future-Ranjith won't re-introduce
  the circularity.
- Three new regression tests in `test_kpi_exporter.py:74-137`:
  * `test_bucket_accuracy_excludes_low_confidence_rows` — 2 low rows
    with `first_seen_at = now` → `bucket_accuracy_24h == 0.0` (was
    1.0 pre-fix). Explicit REGRESSION docstring.
  * `test_bucket_accuracy_high_confidence_today_scores_full` — 1 high
    row → 1.0 (unchanged behaviour, guards against over-correction).
  * `test_bucket_accuracy_mixed_confidence_measures_trustworthy_only`
    — 2 high + 3 low → 1.0 (denominator is 2, not 5) AND
    `date_reliability_ratio == 0.4` (the 2/5 split is still visible
    in the complementary KPI).

### P1-3 — "10-KPI exporter" overclaim

**Round 1:** `compute_kpis` returned 12 keys with 5 unconditional
stubs; commit `9e7708d` titled "feat(ops): 10-KPI Prometheus exporter"
inflated the shipped surface.

**Round 2:** ✅ RESOLVED in commit `74914a5`
(`docs(ops): reword 10-KPI overclaim; update completion entry`).

- `backend/ops/exporter.py` module docstring rewritten at lines 1-21
  to explicitly list the 6 LIVE KPIs and the 4 STUB families with the
  Batch 2 dependency (notification audit log) spelled out.
- `IMPLEMENTATION_LOG.md §"What shipped"` entries 6 and 7 rewritten:
  * Entry 6 now credits the production wiring in `main.py` and names
    the 70% completeness gate.
  * Entry 7 says "6 live + 4 stubs" explicitly, documents the initial
    `bucket_accuracy` circularity *and* the review-response fix, and
    points to the Batch 2 dependency for the stubs.
- `§"What got deferred"` correctly drops the
  "Scrape-completeness gate integration in `src/main.py`" bullet since
  it shipped in `a4425cb`. Remaining deferrals (direct-URL verification,
  embedding-based repost detection, notification/pipeline latency)
  are accurately described.

---

## Outstanding follow-ups (not blocking merge)

### P2-2 — Direct-URL verification not present

The state machine's `CONFIRMED_EXPIRED` branch (sticky, documented at
`ghost_detection.py:63-64`) is unreachable without a direct-URL
verifier. Jobs can only ratchet to `LIKELY_STALE`; they never resolve
to `CONFIRMED_EXPIRED`. Completion entry flags this as deferred.
Reasonable — Batch 1's scope was freshness, not deliverability;
verification naturally belongs with Batch 2's notification audit log
or Batch 3's URL-health pass.

### P2-3 — Low-confidence `posted_at` is ignored rather than downgraded

`recency_score_for_job` (`skill_matcher.py:195-212`) discards
`posted_at` when `date_confidence != trustworthy`. Safe default;
wastes Jooble/Greenhouse/NHS's genuinely-dated-but-wrong-semantic
fields that are captured in `date_posted_raw`. P3 enhancement —
deferred is fine.

### P3 notes carried over from Round 1

- Async fixture style in new tests is sync `@pytest.fixture` +
  internal `asyncio.run(...)`. Works; inconsistent with
  `pytest_asyncio.fixture` used elsewhere in the suite. No action.
- `b6c088b` commit body would benefit from a one-line rollback note
  (the migration is purely additive so revert is trivial, but
  documenting beats remembering). No action.

---

## Checklist results (Round 2)

### Correctness

- **Every 39 fabricator sources stops calling `datetime.now()` for
  `posted_at`** — PASS. Remaining `datetime.now()` calls are for
  `date_found` (legacy first_seen alias), which the plan explicitly
  allows.

- **Jooble, Greenhouse, NHS use the correct source field** — PASS.
  All three set `posted_at=None`, preserve the wrong field in
  `date_posted_raw`, and tag `date_confidence="low"`.

- **Schema migration backward-compatible** — PASS. All 8 new columns
  nullable or defaulted; `_migrate()` idempotent
  (`test_migration_idempotent`); no changes to `purge_old_jobs`.

- **Ghost-detection state machine transitions covered by tests** —
  PASS. `test_ghost_detection.py` exercises fresh→stale→ghost via
  parametrize, `evaluate_job_state` at the 25h/3-miss boundary,
  `CONFIRMED_EXPIRED` stickiness, rehydration via
  `test_update_last_seen_resets_misses`, AND (new in Round 2) the
  production `_ghost_detection_pass` helper with 3 integration tests
  covering the scrape-completeness gate and failure paths.

- **Recency scorer handles `None` without crashing AND without
  inflating** — PASS. `fabricated` → 0; `None posted_at + date_found`
  → 60% cap; `None + None` → 0.

- **Prometheus exporter returns the 10 KPIs from §KPIs** —
  PASS-WITH-DOCUMENTED-DEFERRAL. 6 live + 4 stubs explicitly
  documented; Grafana dashboard pre-allocates rows for the stubs so
  Batch 2 wiring will not require dashboard migration.

### Constraints

- **`Job.normalized_key()` untouched (rule #1)** — PASS.
- **`BaseJobSource` constructor/helpers untouched (rule #2)** — PASS.
- **`purge_old_jobs()` untouched (rule #3)** — PASS.
- **No `aiohttp.ClientSession` outside `aioresponses` (rule #4)** —
  PASS. New integration tests use `_FakeSource` stubs and `:memory:`
  SQLite; no network.
- **`SOURCE_REGISTRY` count unchanged at 48 (rule #8)** — PASS.
  `main.py` diff from `a4425cb` only adds `_ghost_detection_pass` and
  one call site; the registry dict is untouched.

### Quality

- **All new tests use proper async fixtures** — PARTIAL (P3). Same
  style carried over; not a blocker.
- **No TODO/FIXME/HACK introduced** — PASS.
- **Imports resolve (no stale phase-4 paths)** — PASS. New integration
  tests import `from src.main import _ghost_detection_pass` and
  `from src.models import Job` — both resolve against the phase-4
  layout.
- **Commits are logical units (not WIP dumps)** — PASS. Four
  review-response commits cleanly separate: (1) integration, (2) KPI
  fix, (3) docs/overclaim reword, (4) final test-delta update.
- **`IMPLEMENTATION_LOG.md` completion entry is accurate** — PASS.
  Test deltas updated to 420 / 24 / 3 / 164.80s (new baseline after
  the +6 regression tests). "What shipped" entries 6 and 7 now
  accurately describe production wiring and the 6-live-4-stub shape.
  "What got deferred" correctly drops the shipped scrape-completeness
  gate.

---

## Commit trail

| # | SHA | Subject | Role |
|---|---|---|---|
| 1 | `b6c088b` | feat(db): add 5-column date model + ghost detection hooks | schema |
| 2 | `09cfe2d` | feat(models): add posted_at, date_confidence, date_posted_raw to Job | dataclass + DB helpers |
| 3 | `d0a2ec7` | feat(scorer): honour posted_at + date_confidence in recency scoring | scorer |
| 4 | `c83ad57` | fix(sources): drop wrong-field posted_at mappings in jooble, greenhouse, nhs_jobs | 3 wrong-field |
| 5 | `6beea35` | feat(ghost-detection): add pure state machine + evaluate_job_state | state machine |
| 6 | `9e7708d` | feat(ops): 10-KPI Prometheus exporter + Grafana dashboard + reliability script | exporter skeleton |
| 7–50 | `d184221`…`d73b209` | fix(<source>): remove fabricated posted_at, explicit confidence=<lvl> | 44 source fixes |
| 51 | `ebf439b` | docs: append Batch 1 draft completion entry | round-1 handoff |
| 52 | `a4425cb` | feat(main): wire ghost detection into run_search() with 70% completeness gate | P1-1 fix |
| 53 | `f525b3b` | fix(ops): bucket_accuracy no longer measures itself against first_seen | P1-2 fix |
| 54 | `74914a5` | docs(ops): reword 10-KPI overclaim; update completion entry | P1-3 fix |
| 55 | `99c69c0` | docs: update completion entry with final review-response test deltas | round-2 handoff |

---

## Test deltas (round-2 verification)

| Metric | Clean-main baseline | Round-1 (ebf439b) | Round-2 (99c69c0) | Δ round-1→round-2 |
|---|---:|---:|---:|---:|
| Passing | 371 | 414 (+43) | 420 (+49) | +6 (3 ghost integration + 3 bucket_accuracy regression) |
| Failing | 24 | 24 (same 4 buckets) | 24 (same 4 buckets) | 0 |
| Skipped | 3 | 3 | 3 | 0 |
| Run time | 169.53s | 170.39s | 164.80s | −5.59s |

All deltas accounted for. Failing count unchanged — no new failures
introduced by the P1 fixes.

---

## Review methodology

**Round 1:** fetched `origin/pillar3/batch-1` at `ebf439b`, read plan
+ research + log, ran targeted greps for `datetime.now` and for
ghost-detection callers, manually diff-audited the 5-column schema,
the recency scorer, the wrong-field sources, and the exporter. Three
P1 findings raised.

**Round 2:** fetched the updated branch at `99c69c0`, ran
`git show --stat a4425cb f525b3b 74914a5 99c69c0` to inspect the 4
review-response commits, diff-audited the integration helper + call
site in `main.py`, the SQL change in `exporter.py`, and all 6 new
tests. Confirmed pytest deltas match the completion entry.

`coderabbit:code-review` remains uninvoked — not available in the
reviewer worktree harness; manual audit performed against the plan +
rules above.

**Round 3 (confirmation pass):** re-ran `git fetch origin pillar3/batch-1`
— generator HEAD unchanged at `99c69c0`. Spot-checked the three P1
resolutions with targeted greps:

- P1-1 — `_ghost_detection_pass` defined at `backend/src/main.py:134`
  and called at `backend/src/main.py:391` — PRESENT.
- P1-2 — `date_confidence IN ('high', 'medium', 'repost_backdated')`
  present in both the SQL predicate (`backend/ops/exporter.py:83`) and
  the docstring block explaining why (`exporter.py:70`) — PRESENT.
- P1-3 — exporter module docstring opens with "The full Batch 1
  deliverable is 6 LIVE KPIs + 4 STUBS" and enumerates each — PRESENT.

Constraint greps:
- `def normalized_key` in `models.py` → 1 hit (rule #1 intact).
- `SOURCE_INSTANCE_COUNT = 47` in `main.py` (rule #8 intact; 48 in
  SOURCE_REGISTRY, 47 unique instances because indeed+glassdoor share
  `JobSpySource`).
- `grep -rn "posted_at=datetime.now\|posted_at = datetime.now"
  backend/src/sources/` → 0 hits (no source fabricates `posted_at`).

No new code from the generator since round 2. Round-3 verdict is
unchanged: APPROVED.

---

**Signal:**

REVIEW_COMPLETE pillar3/batch-1 verdict=APPROVED
