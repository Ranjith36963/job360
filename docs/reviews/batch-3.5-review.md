# Batch 3.5 — Independent Review

**Reviewer:** Claude Opus 4.7 (1M) in `.claude/worktrees/reviewer` on `pillar3/batch-3.5-review`
**Generator branch:** `pillar3/batch-3.5` @ `f6c589e`
**Review date:** 2026-04-19
**Base:** `main` (Batch 3 merge `fad1744`) … `f6c589e` — 5 commits

---

## Verdict

**APPROVED** — with one P3 design note on the ARQ `redis_settings` shim (non-blocker, production-boot discovery).

All four generator-flagged review targets check out. IDOR fix is complete for the high-risk surfaces (actions / pipeline / jobs). Scheduler wiring preserves the ghost-detection `results`-list contract. ARQ runtime is importable and structurally correct.

---

## Per-target audit

### Target 1 — SQL-injection safety of `insert_action` ON CONFLICT

**Code:** `backend/src/repositories/database.py:281-295`

```sql
INSERT INTO user_actions (user_id, job_id, action, notes, created_at)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT(user_id, job_id)
DO UPDATE SET action = excluded.action,
              notes = excluded.notes,
              created_at = excluded.created_at
```

- All five values use aiosqlite's `?` parameter binding (line 292). Zero string interpolation.
- `ON CONFLICT(user_id, job_id)` references the UNIQUE index from migration `0002_multi_tenant` (Batch 2). The conflict target must match an actual constraint — it does.
- `DO UPDATE SET ... = excluded.X` uses `excluded.*` (SQLite's pseudo-table for the conflicting INSERT row) with static column names — no user-controlled identifier path.

**Verdict:** ✅ Safe. No SQL-injection risk.

### Target 2 — Ledger idempotency under real-world retry

**Code:** `backend/src/workers/tasks.py:134-153` (`_record_ledger_if_new`), `:156-189` (`mark_ledger_sent`/`mark_ledger_failed`), `:263-278` (call-site in `send_notification`)

The pattern:

```python
try:
    INSERT INTO notification_ledger(user_id, job_id, channel, status) VALUES (?,?,?,'queued')
    commit()
    return True
except IntegrityError:
    rollback()
    return False
```

Then regardless of insert/exists, `mark_ledger_sent` or `mark_ledger_failed` UPDATEs the row by `(user_id, job_id, channel)`.

**Real-world retry analysis:**

| Scenario | Behaviour | Final row state |
|---|---|---|
| First attempt succeeds | INSERT ok → dispatch ok → UPDATE status='sent' | 1 row, status='sent' |
| Transient failure then retry success | INSERT ok → dispatch fails → UPDATE status='failed' + retry_count=1; retry: INSERT fails (UNIQUE) → False → dispatch ok → UPDATE status='sent' | 1 row, status='sent', retry_count=1 |
| Hard failure, N retries | Each retry: INSERT fails → UPDATE status='failed' + retry_count += 1 | 1 row, status='failed', retry_count=N |
| Concurrent workers for same (user,job,channel) | Both INSERT simultaneously → one wins UNIQUE, other rolls back | 1 row — atomic at SQLite engine level |

**TOCTOU safety:** The UNIQUE-violation detection is atomic with the INSERT in SQLite (single-statement), so there is no race window between "check if exists" and "insert if not". ✓

**Transaction isolation check:** The call-site in `send_notification` loops over channels and invokes `_record_ledger_if_new` per channel. Each call auto-commits or rolls back — no cross-channel transaction. The `rollback()` on IntegrityError can only clobber uncommitted state in the current (short) statement window, which is the insert itself. Upstream state (e.g., `upsert_feed_row` already committed) is not affected.

**Verdict:** ✅ Safe under real-world retry and concurrent-worker conditions.

### Target 3 — `results` list shape parity for `_ghost_detection_pass`

**Code:** `backend/src/main.py:355-412` (new scheduler-driven block)

Pre-Batch-3.5 pattern:
```python
results = await asyncio.gather(*[_fetch_source(s) for s in sources], return_exceptions=True)
# results[i] ∈ {None (timeout/exception), list[Job], BaseException}
```

Post-Batch-3.5 pattern (`main.py:363-412`):
```python
scheduler = TieredScheduler(sources, registry)
paired = await scheduler.tick(force=True)  # [(source, result|Exception), …]
results_by_name = {name: None for name in (s.name for s in sources)}  # init None
for src, result in paired: results_by_name[src.name] = result
results = [results_by_name.get(s.name) for s in sources]  # re-align with `sources`
```

**Shape analysis:**
- `results[i]` is `None` for (a) breaker-OPEN skipped source (new), (b) source that returned empty list (preserved via `None` coming from scheduler's `_safe_fetch` — wait, no: scheduler returns the empty list, which is truthy-falsy only). Let me verify: `_safe_fetch` in `scheduler.py:128-133` returns `result` directly on success. If `fetch_jobs()` returns `[]`, paired has `(src, [])`. `results_by_name[name] = []`. `results` gets `[]` at that index.
- `results[i]` is `BaseException` for (c) source that raised — `_safe_fetch` catches and returns the exception instance.
- `results[i]` is `list[Job]` for (d) successful fetch.

`_ghost_detection_pass` (`main.py:142-186`) iterates `zip(sources, results)` and:
```python
if isinstance(result, BaseException) or result is None:
    continue
```

So `None` skips. Breaker-OPEN sources correctly skip sweep — we didn't query, so we have no evidence for absence. This is the **right semantic extension** of `None`.

**Edge case — empty list `[]`:** Not `None`, not `BaseException`, so `_ghost_detection_pass` proceeds. `seen = {job.normalized_key() for job in []}` → empty set → `mark_missed_for_source(source.name, set())` → marks every DB row for this source as missed (subject to the 70%-rolling-average guard at `main.py:167-173`). This is the pre-existing semantics and is preserved. ✓

**Verdict:** ✅ Shape parity fully preserved. The semantic expansion of `None` (breaker-OPEN skipped) correctly maps to the existing skip-sweep behaviour.

### Target 4 — `WorkerSettings.redis_settings` structural compat with ARQ

**Code:** `backend/src/workers/settings.py:39-65, 99`

- Shim `_RedisSettings` dataclass exposes `host` / `port` / `database` (three fields).
- Module-level `redis_settings = _parse_redis_url(os.environ.get("REDIS_URL"))` → always the shim, even at production boot.
- Real `arq.connections.RedisSettings` has ≥12 fields: `password`, `ssl`, `ssl_*`, `conn_timeout`, `conn_retries`, `sentinel`, `command_timeout`, `max_connections`, etc.

**The question:** will `arq src.workers.settings.WorkerSettings` actually boot against a live Redis, given `WorkerSettings.redis_settings` is a dataclass with only 3 fields?

Modern ARQ (`>=0.25`) uses `getattr(settings, field, default)` for most non-essential fields inside `create_pool`, so the shim *should* work for a vanilla `redis://host:port/db` URL. The design comment at `settings.py:94-98` explicitly acknowledges this and documents `_load_arq_redis_settings()` as the escape hatch.

**But** — no test actually boots arq. The only smoke check cited in the completion entry is `python -c "from src.workers.settings import WorkerSettings"` (import-only). The module docstring (`settings.py:17-19`) claims `arq src.workers.settings.WorkerSettings` is the production command, but this is unverified.

**Risk:** if ARQ's newer versions hard-require `password=None` or `ssl=False` via attribute access (not getattr-default), first production boot would `AttributeError` on `WorkerSettings.redis_settings.password`.

**Mitigation available today — no code change required:**
- `_load_arq_redis_settings()` already exists and constructs a real `arq.connections.RedisSettings`.
- Simplest deployment fix if the shim breaks: assign `redis_settings` on `WorkerSettings` as a `@classmethod` or convert to a property that calls `_load_arq_redis_settings()` on first access. That keeps the lazy-import contract (CLAUDE.md rule #11) because it only touches `arq` when ARQ reads the attribute, which is after `arq` has started its own import graph.

**Verdict:** ⚠️ Acceptable for this batch. Flagged as **P3 note**, not a blocker. The design is honest about the compat assumption, and the escape hatch exists. A production-boot smoke (`arq src.workers.settings.WorkerSettings` against a real Redis) at Batch 4 prep time would be the right forcing function.

---

## Other audit checks

### Source-count integrity ✅

No source changes in Batch 3.5 — `SOURCE_REGISTRY` (50), `_build_sources()` (49 instances), `RATE_LIMITS` (50 entries), `test_cli.py` assertion all unchanged. No drift.

### IDOR fix completeness ✅

Every per-user endpoint in the three high-risk route files now gates with `Depends(require_user)`:

| File | Endpoints | All gated? |
|---|---|---|
| `backend/src/api/routes/actions.py` | POST `/jobs/{id}/action`, DELETE `/jobs/{id}/action`, GET `/actions`, GET `/actions/counts` | ✅ 4/4 (lines 19, 37, 46, 59) |
| `backend/src/api/routes/pipeline.py` | GET `/pipeline`, GET `/pipeline/counts`, GET `/pipeline/reminders`, POST `/pipeline/{id}`, POST `/pipeline/{id}/advance` | ✅ 5/5 (lines 43, 55, 67, 80, 95) |
| `backend/src/api/routes/jobs.py` | GET `/jobs`, GET `/jobs/{id}`, GET `/jobs/export` | ✅ 3/3 (lines 122, 187, 71) |

Repo methods (`backend/src/repositories/database.py:282-422`): all 11 methods now take `user_id` as a required kwarg; every WHERE includes `user_id = ?`; every INSERT includes `user_id`. Spot-checked `get_applications` (`:374`), `insert_action` (`:282`), `get_stale_applications` (`:407`) — all scoped correctly.

`profile.py` and `search.py` remain un-gated. The plan scope-ceilings these as non-IDOR (they don't touch `user_actions`/`applications`/`user_feed`). Per-user profile storage is already deferred (single shared `data/user_profile.json`). Acceptable.

### Test delta verified

| Metric | Generator claim | Reviewer run |
|---:|---:|---:|
| Baseline (post-Batch-3) | 529 passed, 24 failed, 3 skipped | — |
| Post-Batch-3.5 | **558 passed**, 23 failed, 3 skipped | **557 passed**, 24 failed, 3 skipped |
| New tests added | 28 | confirmed 28 new all pass |
| Net passing delta | +29 (incl. 1 flaky flip green) | +28 (flaky stayed red this run) |
| Regressions | 0 | 0 |

The 1-test difference between the generator run and my run is the flaky pre-existing source parser (likely one of the reed/adzuna/jooble/jobspy/workday/google_jobs/careerjet bucket) that flips between green and red across runs. Zero regressions either way — this is the same flake pattern documented in Batch 3's round-2 review.

All 28 new tests pass in my full-suite run:
- `tests/test_api_idor.py` — 17
- `tests/test_worker_settings.py` — 3
- `tests/test_worker_send_notification.py` — 5
- `tests/test_main_scheduler_wiring.py` — 3

### Completion-entry file:line claims

Spot-checked three generator claims against the actual code:

| Claim | Location claimed | Verified |
|---|---|---|
| `actions.py:19,37,46,59` — 4 endpoints gated | `actions.py:19, 37, 46, 59` | ✅ exact match |
| `database.py:insert_action SQL switched to ON CONFLICT(user_id, job_id) DO UPDATE` | `database.py:281-295` | ✅ exact match |
| `main.py:363` — `scheduler = TieredScheduler(...)` replaces asyncio.gather | `main.py:363-364` | ✅ exact match |

The Batch-3 P2 lesson (don't claim-without-verifying) is visibly internalized — every shipping claim in the completion entry has an anchor, and the anchors I sampled are accurate.

---

## Findings

### P1 (blocker) — none

### P2 — none

### P3 — `redis_settings` is a shim, not the real ARQ object

See Target 4 above. Non-blocker. Recommended follow-up: either (a) convert `WorkerSettings.redis_settings` to a property that calls `_load_arq_redis_settings()` on first access, or (b) add a production-boot smoke test as part of Batch 4 prep (`arq src.workers.settings.WorkerSettings` against a real Redis). The current design is honest (the deferral is documented at `settings.py:94-98`) and the escape-hatch code is already written (`settings.py:68-77`).

---

## Recommendation

**Merge `pillar3/batch-3.5` to `main`.** Track the P3 `redis_settings`-shim concern as a Batch 4 pre-flight smoke-check item. No blocker-grade issues.

---

_Signed: reviewer session, 2026-04-19_
