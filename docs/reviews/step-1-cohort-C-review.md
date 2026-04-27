# Step 1 — Cohort C (Response surface + hybrid + security) — Independent Review

**Reviewer:** dual-worktree review session in `.claude/worktrees/reviewer` on branch `worktree-reviewer`
**Generator branch:** `step-1-batch-s1` (HEAD `570e5fe`)
**Review date:** 2026-04-24
**Commits reviewed:**
- `7ee6dc1` — `feat(step-1/B6)`: JobResponse surfaces 5 date + 13 enrichment fields, JOIN-once prefetch
- `e1c48a6` — `fix(step-1/B9+B12)`: filter expired jobs at read + per-user concurrent search cap
- `658844b` — `feat(step-1/B8)`: wire ?mode=hybrid + VectorIndex.upsert in run_search + restore build script

---

## 1. Summary

**CONDITIONAL APPROVE — 2 P1 blockers require fixes before merge.**

Cohort C delivers real value: the JOIN-once enrichment prefetch is architecturally correct (one SQL query for the list, one for detail), the per-user concurrent search cap is correctly scoped and returns 429, the expired-jobs filter is properly applied to the list path, and hybrid mode degrades cleanly when `SEMANTIC_ENABLED` is off or the vector index is empty.

**Two bugs prevent full approval:**
- The `?action=` filter is applied **after** pagination, producing a mismatched `total` count on every filtered page.
- `GET /jobs/{job_id}` has **no staleness filter**, so jobs marked `expired` by ghost-detection are hidden by the list but fully visible via direct ID lookup.

| Commit | Claim | Verdict |
|---|---|---|
| 7ee6dc1 (B6) | JOIN-once enrichment prefetch; Optional fields | Approved with P2 note (action N+1 remains) |
| e1c48a6 (B9+B12) | Expired filter + per-user cap | B12 fully approved; B9 has a P1 gap on the detail route |
| 658844b (B8) | Hybrid mode + VectorIndex.upsert + build script | Approved with P3 design note |

---

## 2. Per-Blocker Findings

### B6 — JOIN-once enrichment prefetch

**Verdict: Approved with one P2 note.**

`get_recent_jobs_with_enrichment` (`backend/src/repositories/database.py:550-576`) issues exactly one query per request:

```sql
SELECT j.*, je.title_canonical AS enr_title_canonical, je.category AS enr_category, ...
FROM jobs j
LEFT JOIN job_enrichment je ON je.job_id = j.id
WHERE j.first_seen >= ? AND j.match_score >= ?
AND (j.staleness_state IS NULL OR j.staleness_state = 'active')
ORDER BY j.date_found DESC
```

One round-trip for all N jobs. `get_job_by_id_with_enrichment` (lines 578-594) is equally single-query. Enrichment N+1 eliminated.

All 13 enrichment fields in `JobResponse` are typed `Optional[...] = None` (`backend/src/api/models.py:77-89`). All 5 date-model fields are `Optional[str] = None` (lines 69-73). Unenriched jobs pass Pydantic validation cleanly.

The `_JOBS_ENRICHMENT_JOIN_COLS` constant is a class-level string, not user input — the `noqa: S608` suppression is justified.

**P2 finding (confidence 85) — action lookup is still N+1.**

`list_jobs` calls `db.get_action_for_job(row["id"], user.id)` inside the `for row in page` loop (`backend/src/api/routes/jobs.py:369-374`). With `limit=100`, this issues up to 100 individual `SELECT action FROM user_actions WHERE user_id = ? AND job_id = ?` queries per request. Enrichment N+1 was fixed; action N+1 was not. Recommended fix: call `db.get_actions(user.id)` once before the loop, build `{job_id: action}` dict, replace per-row async calls with synchronous dict lookups.

---

### B9 — Expired-jobs filter at read

**Verdict: List path approved; detail path has a P1 gap.**

`get_recent_jobs` (lines 318-334) and `get_recent_jobs_with_enrichment` (lines 550-576) both apply:

```sql
AND (staleness_state IS NULL OR staleness_state = 'active')
```

NULL is treated as "not yet classified → serve" — correct defence-in-depth given the staleness writer is deferred to S1.5. `purge_old_jobs` (lines 311-316) is unchanged. Rule #3 satisfied.

`test_expired_jobs_filtered` covers active, expired, and NULL cases against `get_recent_jobs` and passes.

#### P1 BLOCKER (C-1, confidence 95) — `GET /jobs/{job_id}` serves expired jobs

`get_job_by_id_with_enrichment` (lines 578-594) queries:

```sql
FROM jobs j LEFT JOIN job_enrichment je ON je.job_id = j.id WHERE j.id = ?
```

**No `staleness_state` predicate.** A job marked `expired` by ghost-detection is invisible in `GET /jobs` (list) but fully visible via `GET /jobs/{job_id}` (detail). A frontend that opens a cached job detail URL after ghost-detection has expired that job will receive stale data that the list deliberately hides.

**Fix:** Add `AND (j.staleness_state IS NULL OR j.staleness_state = 'active')` to the WHERE clause, or raise HTTP 410 at the route layer when `row["staleness_state"] == "expired"`.

---

### B12 — Per-user concurrent search cap

**Verdict: Fully approved.**

`_active_run_count_for_user(user.id)` counts only runs owned by the calling user, in `_ACTIVE_STATUSES = frozenset({"pending", "running"})` (`backend/src/api/routes/search.py:32-37`). Completed and failed runs are excluded. The cap check fires before `asyncio.create_task(_run())` — before any expensive work. HTTP 429 is raised with a descriptive `detail`. `test_search_concurrent_cap_per_user` verifies User 1 at cap does not block User 2.

`MAX_CONCURRENT_SEARCHES_PER_USER` is configurable via env var, default 3.

**Rate-limit bypass via parallel sessions:** impossible. `_active_run_count_for_user` keys on `user.id` from the resolved cookie session, shared by all parallel sessions of the same account.

**One non-blocking note (Info C-5):** `_runs` is never pruned; completed/failed records accumulate indefinitely. Memory leak under long-lived API processes; no security or correctness impact.

---

### B8 — `?mode=hybrid` + VectorIndex.upsert + build script

**Verdict: Approved with one P3 design note.**

**Rule #18 (SEMANTIC_ENABLED=false no-op):** `_maybe_apply_hybrid_reorder` imports `SEMANTIC_ENABLED` lazily and returns `rows` unchanged when false (`jobs.py:178-179`). Default path (no `mode` param, or `mode != "hybrid"`) bypasses the function entirely. Pre-Pillar-2 callers unaffected.

**Rule #16 (lazy imports):** `chromadb` is imported inside `_make_client` (`vector_index.py:26`). In `jobs.py`, `retrieval`, `VectorIndex`, and `embeddings.encode_job` are all imported inside the `try:` block within `_maybe_apply_hybrid_reorder` (lines 183-217). In `main.py`, the same modules are imported inside `if SEMANTIC_ENABLED and new_jobs:` (lines 587-589). Rule #16 satisfied throughout.

**Empty-index fallback:** `is_hybrid_available(count)` returns `count > 0` (`retrieval.py:154`). Empty index triggers a WARNING log and returns the original keyword-ordered rows unchanged. `test_mode_hybrid_empty_index_falls_back` verifies 200 status, the WARNING log, and identical response payload to the keyword path.

**VectorIndex.upsert is not batched:** `run_search` calls `vix.upsert(...)` once per new job in a loop (`main.py:598-624`). Acceptable: this runs only for newly-inserted jobs under `SEMANTIC_ENABLED=true`, not in the hot HTTP path.

**Build script:** `backend/scripts/build_job_embeddings.py` is present, tagged with the step-1 B8 docstring ("Restored from Pillar-2 Batch 2.6, adapted to post-restructure backend/scripts/ location"). Idempotent, SEMANTIC_ENABLED-gated, skips jobs with existing audit rows.

#### P3 design note (C-4, confidence 80) — semantic query proxy degrades hybrid quality

The ChromaDB query vector is computed by calling `encode_job` on a stub constructed from the **top keyword result's title**, not a user profile vector (`jobs.py:214-230`, with a code comment acknowledging the limitation). RRF fusion of (keyword order, semantic order anchored to the top keyword hit) will tend to produce a ranking close to keyword order with minor top-N reshuffling. Users enabling `?mode=hybrid` will observe little practical difference from `?mode=keyword` until the follow-up profile-vector path lands.

Not a correctness bug — the fallback logic is sound and the code comment is honest about the limitation. Track as a follow-up before `?mode=hybrid` is surfaced as a UI toggle.

---

## 3. Security Review

**IDOR — `search.py`:** Runs stored with `user_id`; cross-user reads return 404 with existence-hiding (same body for "not found" and "not mine"). Rule #12 satisfied.

**IDOR — `jobs.py`:** Jobs catalog is shared (rule #10 — no `user_id` on `jobs`). All authenticated users legitimately see all jobs. Per-user state (`user_actions`, `applications`) is scoped by `user_id` in every DB method (`get_action_for_job`, `insert_action`, `get_actions`, `create_application`, etc.). Rule #12 satisfied for per-user data.

**Rate-limit bypass:** impossible via parallel sessions — see B12.

**SESSION_SECRET fail-closed:** `auth_deps._secret()` raises `RuntimeError` if env var unset. No committed default. Correct.

---

## 4. Cross-Cutting Concerns

**JOIN-once + expired filter — list path:** composes correctly; the staleness predicate runs before the JOIN, so no enrichment data is returned for expired jobs.

**JOIN-once + expired filter — detail path:** GAP — see C-1 above.

**Hybrid mode + expired filter:** `_maybe_apply_hybrid_reorder` operates on `all_rows` already returned by `get_recent_jobs_with_enrichment`, which excludes expired rows. The semantic RRF layer cannot resurface expired jobs. Composition correct on list path.

**Hybrid mode + `?action=` pagination bug:** `?mode=hybrid&action=saved&offset=50` will produce a wrong `total` and possibly fewer results than `limit`. C-1 and C-2 compound on this path.

---

## 5. Findings Table

| ID | Severity | Confidence | Blocker | File | Lines | Description |
|----|----------|------------|---------|------|-------|-------------|
| **C-1** | **P1** | 95 | **Yes** | `backend/src/repositories/database.py` | 578-594 | `get_job_by_id_with_enrichment` has no staleness filter; `GET /jobs/{id}` serves expired jobs |
| **C-2** | **P1** | 90 | **Yes** | `backend/src/api/routes/jobs.py` | 365-374 | `?action=` filter applied post-pagination; `total` reports pre-filter count, `len(jobs)` is post-filter — mismatch |
| C-3 | P2 | 85 | No | `backend/src/api/routes/jobs.py` | 369-374 | Action lookup is N+1 (one SELECT per job in page); enrichment N+1 was fixed, action N+1 was not |
| C-4 | P3 | 80 | No | `backend/src/api/routes/jobs.py` | 214-230 | Hybrid semantic query anchored to top keyword hit title, not user profile vector; hybrid behaves like keyword in practice |
| C-5 | Info | — | No | `backend/src/api/routes/search.py` | 27, 63 | `_runs` dict never pruned; memory accumulation under long-lived process (no security/correctness impact) |

---

## 6. Approval Gate

**NOT APPROVED FOR MERGE** in current state. Two P1 blockers must be resolved.

**Fix for C-1** (`backend/src/repositories/database.py:585`):
```sql
-- Change:
WHERE j.id = ?
-- To:
WHERE j.id = ? AND (j.staleness_state IS NULL OR j.staleness_state = 'active')
```
Or at the route layer: after fetching, check `row.get("staleness_state") == "expired"` and raise `HTTPException(status_code=410, detail="Job listing has expired")`.

**Fix for C-2** (`backend/src/api/routes/jobs.py:365`):
Move the action filter before pagination. Replace per-row async `get_action_for_job` calls with a pre-fetched map:

```python
# Before pagination:
action_map = {r["job_id"]: r["action"] for r in await db.get_actions(user.id)}
if action is not None:
    all_rows = [r for r in all_rows if action_map.get(r["id"]) == action]

total = len(all_rows)
page = all_rows[offset : offset + limit]

jobs = [_row_to_job_response(row, action_map.get(row["id"])) for row in page]
```

This **also resolves C-3** (action N+1) as a side effect.

Once C-1 and C-2 are fixed with passing tests, resubmit. C-3 is automatically resolved by the C-2 fix. C-4 is a tracked follow-up.

---

### Pytest sweep result
`python -m pytest tests/ --ignore=tests/test_main.py -p no:randomly`: **1056 passed, 4 skipped, 1 warning in 300.47s** (exit 0). All existing tests pass — and yet both C-1 and C-2 are real, independently verified by direct code read:
- **C-1:** `backend/src/repositories/database.py:585` — bare `WHERE j.id = ?` (no staleness predicate), vs the list-path equivalent at line 568 which has `AND (j.staleness_state IS NULL OR j.staleness_state = 'active')`.
- **C-2:** `backend/src/api/routes/jobs.py:365-374` — `total = len(all_rows)` and `page = all_rows[offset:offset+limit]` happen at lines 365-366; the action filter is applied per-row at line 372 inside the page loop.

Neither bug has an existing test, which is why the green sweep does not contradict this review. The two new tests required by the C-1 and C-2 fixes (expired-job-by-id returns 410-or-not-found; `?action=` returns matching `total` and `len(jobs)`) should be added in the same fix commit.

### Re-audit @ 9ac434f (2026-04-24) — C-1 + C-2 + C-3 CLOSED

Fix commit `64f8020`:

- **C-1**: `backend/src/repositories/database.py:591-602` now applies `AND (j.staleness_state IS NULL OR j.staleness_state = 'active')` in BOTH the JOIN-once SQL and the `OperationalError` fallback path. The fallback parity is the right call — a security predicate on the primary path that's missing from the fallback is exactly the trap the original review flagged. New test `test_get_job_by_id_with_enrichment_filters_expired` (test_api_security.py:79) covers the detail-route path. **Closed.**
- **C-2**: `backend/src/api/routes/jobs.py:365-378` now pre-fetches `action_map = {row["job_id"]: row["action"] for row in await db.get_actions(user.id)}` once, then filters `all_rows` by `action_map.get(r["id"]) == action` BEFORE `total = len(all_rows)` and `page = all_rows[offset:offset+limit]`. `total` is now honest. New test `test_jobs_action_filter_runs_before_pagination` (test_api.py:357) asserts the invariant. **Closed.**
- **C-3** (action N+1): resolved as a side effect of C-2 — the `action_map` dict lookup replaces the per-row `await db.get_action_for_job(...)` call. **Closed.**

C-4 (hybrid query proxy) and C-5 (`_runs` dict pruning) remain as tracked follow-ups; both are P3/Info and do not block merge.

_Signed: dual-worktree reviewer session, 2026-04-24_
