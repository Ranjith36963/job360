# Pillar 3 — Batch 3.5 Stabilisation Plan

> **For agentic workers:** REQUIRED SUB-SKILLS: `superpowers:test-driven-development`, `superpowers:verification-before-completion`. Steps use checkbox syntax.

**Goal:** Close the three Batch-2/3 deferrals that matter most for multi-user safety + ARQ-runtime launchability + measurable scheduler gains.

**Tech stack:** existing — FastAPI, aiosqlite, argon2id sessions (Batch 2), ARQ (lazy-imported), apprise (lazy-imported).

---

## POST-BATCH-3 BASELINE

Run 2026-04-19 on `pillar3/batch-3.5` HEAD (branched from `origin/main` @ `fad1744`, Batch 3 merged).

```
Command: cd backend && python -m pytest tests/ --ignore=tests/test_main.py -q
Result:  [to be filled from /tmp/pytest_baseline_3_5.log on completion]
```

All regression claims below compare against this number. Do not trust counts from prior memory files.

---

## Scope order (strict sequential execution per the user directive)

C → D → E. Each deliverable ends with a commit + baseline re-run before proceeding.

---

## Deliverable C — IDOR fix on legacy routes

**Why it matters.** CLAUDE.md rule #12 is a security invariant. Batch 2 shipped the schema (`user_actions` + `applications` gained `user_id` + `UNIQUE(user_id, job_id)` in migration `0002_multi_tenant`), but the repo methods (`backend/src/repositories/database.py:286-396`) are still tenant-blind. Without the patch, two real users hitting `POST /api/jobs/{id}/action` alias-collapse onto the placeholder tenant — second write wins, first write lost. Today the UI doesn't register users so the path isn't reachable, but every future multi-user flow depends on this wiring.

**Files — routes (add `user: CurrentUser = Depends(require_user)`):**

| Route | File | Endpoints touched |
|---|---|---|
| jobs list/detail | `backend/src/api/routes/jobs.py:108,179` | `GET /jobs`, `GET /jobs/{id}` (user scopes the `action` join, not the `jobs` catalog — catalog stays shared per rule #10) |
| actions | `backend/src/api/routes/actions.py:13,32,38,48` | `POST/DELETE /jobs/{id}/action`, `GET /actions`, `GET /actions/counts` |
| pipeline | `backend/src/api/routes/pipeline.py:35,47,56,65,78` | all 5 endpoints |
| search | `backend/src/api/routes/search.py:19,37` | `POST /search`, `GET /search/{id}/status` (owner-scoped _runs dict key) |
| profile | `backend/src/api/routes/profile.py:57,66,123,146` | all 4 endpoints (legacy `user_profile.json` → scoped read; Batch-3.5 keeps the file-backed implementation but gates it) |

**Files — repo (thread `user_id` param through):**

- `backend/src/repositories/database.py:286-397` — 11 methods total:
  - `insert_action`, `delete_action`, `get_actions`, `get_action_counts`, `get_action_for_job`
  - `create_application`, `advance_application`, `_get_application`, `get_applications`, `get_application_counts`, `get_stale_applications`
  - Each adds `user_id: str` as a required kwarg. Every `WHERE` gets `AND user_id = ?`. Every `INSERT` includes `user_id`.
  - `insert_action` switches from `INSERT OR REPLACE` on `UNIQUE(job_id)` to `ON CONFLICT(user_id, job_id) DO UPDATE` matching Batch 2's `UNIQUE(user_id, job_id)`.

**Files — tests (add):**

- `backend/tests/test_api_idor.py` — new: per-endpoint tests proving
  1. Unauth request → 401
  2. User A cannot read / mutate User B's row (fixture creates two users, verifies isolation)
  3. Same user sees their own row correctly (positive control)

- [ ] **C-Step 1:** Write `test_api_idor.py` RED — authenticate + cross-user tests that will fail on current routes. Fixture: 2 users created via `/api/auth/register`, 2 session cookies captured.
- [ ] **C-Step 2:** Thread `user_id` through all 11 `JobDatabase` methods (repo-level first — routes fail compile if sig mismatch).
- [ ] **C-Step 3:** Add `Depends(require_user)` to every per-user route handler; pass `user.id` into repo calls.
- [ ] **C-Step 4:** Update existing `test_api.py` tests that exercise these routes to register+log in a fixture user (keeps pre-existing 6-failure bucket from growing).
- [ ] **C-Step 5:** Run `pytest tests/test_api_idor.py tests/test_api.py tests/test_tenancy_isolation.py tests/test_auth_routes.py -q` → all GREEN.
- [ ] **C-Step 6:** Full regression run — confirm baseline-24 unchanged.
- [ ] **C-Step 7:** Commit `fix(api): scope per-user routes by user_id (IDOR)`.

---

## Deliverable D — ARQ runtime (send_notification + WorkerSettings)

**Why it matters.** Batch 2 shipped `score_and_ingest`, `mark_ledger_sent`, `mark_ledger_failed`, `idempotency_key` but the fan-out enqueue target `send_notification` was stubbed and there was no `WorkerSettings`. Until both land, ARQ can't boot and the notification path is simulated-only.

**Files — create:**

- `backend/src/workers/tasks.py:send_notification` — new async function:
  - Signature: `async def send_notification(ctx, user_id: str, job_id: int, urgency: str) -> dict`
  - Reads the `user_feed` row (status / bucket / score) to compose title+body
  - Calls `services.channels.dispatcher.dispatch(db, user_id=..., title=..., body=...)` (Batch 2 `dispatcher.py`)
  - For each per-channel `ChannelSendResult`: `mark_ledger_sent` on ok / `mark_ledger_failed` on failure, keyed by `idempotency_key(user_id, job_id, channel="instant")` (blueprint §1)
  - Returns `{sent: int, failed: int}`
- `backend/src/workers/settings.py` — new module:
  - `WorkerSettings` class exposing `.functions = [score_and_ingest, mark_ledger_sent_task, mark_ledger_failed_task, send_notification]` + `redis_settings` from `REDIS_URL` env (default `redis://localhost:6379`)
  - `arq` imported inside `WorkerSettings.load()` / at top of a helper, NOT at module top — mirrors `dispatcher._get_apprise_cls()` pattern. Library-mode tax is deferred.

**Files — tests:**

- `backend/tests/test_worker_send_notification.py` — new:
  1. `test_send_notification_dispatches_each_channel_and_marks_sent` — monkeypatch `apprise.Apprise` to return ok; verify ledger rows for each channel flip to `sent`
  2. `test_send_notification_marks_failed_on_apprise_exception` — monkeypatch raises; verify `mark_ledger_failed` called with the error string
  3. `test_send_notification_uses_idempotency_key` — asserts the ledger row lookup uses `idempotency_key(user_id, job_id, channel)` via `UNIQUE(user_id, job_id, channel)` constraint
  4. `test_send_notification_returns_counts` — returns `{sent: 2, failed: 1}` for mixed results
- `backend/tests/test_worker_settings.py` — new:
  1. `test_worker_settings_functions_includes_send_notification`
  2. `test_worker_settings_redis_settings_from_env` — set `REDIS_URL`, assert host/port parsed
  3. `test_arq_not_imported_at_module_top` — `import src.workers.settings` without arq installed does not raise (delete-attr-in-sys.modules shim)
- **Smoke:** `python -c "from src.workers.settings import WorkerSettings; print(WorkerSettings.functions)"` — cited in the completion entry

- [ ] **D-Step 1:** Write `test_worker_settings.py` RED.
- [ ] **D-Step 2:** Create `backend/src/workers/settings.py` with lazy ARQ import.
- [ ] **D-Step 3:** Verify GREEN + the smoke `python -c`.
- [ ] **D-Step 4:** Write `test_worker_send_notification.py` RED (4 tests).
- [ ] **D-Step 5:** Add `send_notification` to `backend/src/workers/tasks.py`.
- [ ] **D-Step 6:** Register `send_notification` in `WorkerSettings.functions`.
- [ ] **D-Step 7:** Full regression run — confirm baseline + C + D delta.
- [ ] **D-Step 8:** Commit `feat(workers): implement send_notification + WorkerSettings`.

---

## Deliverable E — Wire TieredScheduler into run_search

**Why it matters.** Batch 3 built the scheduler but the CLI path still does `asyncio.gather` at `main.py:356`, giving zero production benefit from the tier map.

**Files — modify:**

- `backend/src/main.py:346-405` — replace the `_fetch_source` / `asyncio.gather` block + the downstream `newly_opened` loop with a `TieredScheduler(sources, registry).tick(force=True)` call. In one-shot mode (`force=True`) the scheduler dispatches every source exactly once, records success/failure into the breaker, and we assemble `per_source` / `results` from its returned `[(source, result|Exception), ...]` list.
- The existing ghost-detection pass at `main.py:407-410` keeps the same shape — it iterates `(source, result)` pairs which the scheduler's return shape provides.

**Files — tests:**

- `backend/tests/test_main_scheduler_wiring.py` — new:
  1. `test_run_search_uses_tiered_scheduler` — monkeypatch `TieredScheduler.tick` to a spy; assert called once with `force=True`
  2. `test_each_registered_source_called_exactly_once` — use 3 fake sources, assert each `fetch_jobs` called exactly once
  3. `test_breaker_open_source_is_skipped` — pre-trip a breaker; assert the skipped source's `fetch_jobs` is NOT called and no exception propagates
- Any existing `test_main.py` test that exercises `run_search` end-to-end stays `--ignore`'d in baseline (live-HTTP leak unchanged).

- [ ] **E-Step 1:** Write `test_main_scheduler_wiring.py` RED (3 tests).
- [ ] **E-Step 2:** Modify `main.py::run_search` to call the scheduler + preserve `per_source` / `results` / ghost-detection shape.
- [ ] **E-Step 3:** Verify GREEN.
- [ ] **E-Step 4:** Full regression run — confirm baseline + C + D + E delta.
- [ ] **E-Step 5:** Commit `feat(scheduler): wire TieredScheduler into run_search`.

---

## STEP 4 — Verify before completion

For every self-claim in the completion entry, cite the `file:line` anchor that proves it. Batch 3 reviewer P2 caught an unverified claim; do not repeat.

- [ ] Full pytest run: `python -m pytest tests/ --ignore=tests/test_main.py -q`
- [ ] Pass / fail / skip vs baseline
- [ ] Per deliverable: file:line anchor for every claim
- [ ] ARQ smoke: `python -c "from src.workers.settings import WorkerSettings"`
- [ ] Push `git push -u origin pillar3/batch-3.5`; report final SHA

---

_Last updated: 2026-04-19_
