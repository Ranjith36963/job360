# Batch 2 — Multi-User Delivery Layer — Implementation Plan
<!-- doc: PLAN -->

> Output of `superpowers:writing-plans`. Test-driven, one logical commit per phase. Decisions doc: `docs/product/plans/batch-2-decisions.md`. Blueprint: `docs/product/research/pillar_3_batch_2.md`.

## POST-BATCH-1 BASELINE (locked 2026-04-18, commit `31124fa`)

| Metric | Value |
|---|---:|
| Passing | **420** |
| Failing | **24** (same 4 buckets as Batch 1 completion entry: API sqlite ×6, cron ×3, setup ×5, source parsers ×7, matched_skills ×3) |
| Skipped | **3** |
| Runtime | 167.32s |
| Command | `cd backend && python -m pytest tests/ --ignore=tests/test_main.py -q` |

Any Batch 2 regression claim compares `passed` count against **420**. Any drop below 420 on the untouched tests is a regression and blocks the batch.

---

## Order of operations (dependency-respecting)

```
Phase 0  Migration runner + directory scaffold
Phase 1  Auth + users/sessions tables (no tenant yet)
Phase 2  Multi-tenancy — add tenant_id to jobs/user_actions/applications; migrate existing rows to tenant 1
Phase 3  user_feed table + FeedService (reads only)
Phase 4  FeedService.ingest_job (writes) + pre-filter cascade
Phase 5  ARQ worker wiring (score_new_job task; idempotency via notification_ledger)
Phase 6  Apprise channel wrapper + test-send endpoint + send_digest task
Phase 7  FastAPI routes — /auth/{register,login,logout,me} + /settings/channels + tenant-scope middleware
Phase 8  Frontend — /settings/channels page + signup/login pages + API client tenant wiring
Phase 9  CLAUDE.md + IMPLEMENTATION_LOG.md + memory save + push
```

Each phase ends with `pytest -q` and an explicit `passed` count comparison.

---

## Phase 0 — Migration runner

**Why first:** Every subsequent phase creates tables. We need a durable `_schema_migrations` table and a runnable CLI before the first `CREATE TABLE`.

**Files touched:**
- NEW `backend/migrations/__init__.py`
- NEW `backend/migrations/runner.py` (≤80 lines)
- NEW `backend/migrations/0000_baseline.up.sql` (no-op — just records the pre-Batch-2 schema as version 0)
- NEW `backend/migrations/0000_baseline.down.sql` (no-op)
- NEW `backend/tests/test_migrations.py`

**Test-first sequence:**
1. `test_migrations_table_created_on_first_run` — assert `_schema_migrations` exists after `runner.up()`.
2. `test_migration_is_idempotent` — two calls to `runner.up()` apply each file exactly once.
3. `test_down_reverses_last_migration` — `runner.down()` applies the matching `.down.sql` and removes the row.
4. `test_status_lists_applied_and_pending` — `runner.status()` returns `{'applied': [...], 'pending': [...]}`.

**Commit:** `feat(migrations): add forward/reverse SQL migration runner`

---

## Phase 1 — Auth (`users`, `sessions`, password hashing)

**Why before multi-tenant:** The `tenant_id` column on jobs needs a target table to FK into. `users` must exist first.

**Dependency added:**
- `passlib[argon2]>=1.7.4` — argon2id password hashing (OWASP-recommended)
- `itsdangerous>=2.2.0` — signed session cookie wrapper

**Files touched:**
- NEW `backend/migrations/0001_auth.up.sql` — `users`, `sessions` tables
- NEW `backend/migrations/0001_auth.down.sql`
- NEW `backend/src/services/auth/__init__.py`
- NEW `backend/src/services/auth/passwords.py` — `hash_password()`, `verify_password()`
- NEW `backend/src/services/auth/sessions.py` — `create_session()`, `resolve_session()`, `revoke_session()`, cookie sign/verify
- MOD `backend/src/core/settings.py` — `SESSION_SECRET` from env, `SESSION_MAX_AGE_DAYS=30`
- MOD `backend/pyproject.toml` — add `passlib[argon2]`, `itsdangerous`
- NEW `backend/tests/test_auth_passwords.py`
- NEW `backend/tests/test_auth_sessions.py`

**Schema:**
```sql
CREATE TABLE users (
    id TEXT PRIMARY KEY,                          -- uuid4 hex
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,                  -- argon2id PHC string
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMP NULL                     -- soft delete for GDPR
);
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,                          -- uuid4 hex, embedded in signed cookie
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NOT NULL,
    last_seen TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    user_agent TEXT NULL,
    ip_hash TEXT NULL                             -- sha256(ip + salt) — PII hygiene
);
CREATE INDEX idx_sessions_user ON sessions(user_id);
CREATE INDEX idx_sessions_expires ON sessions(expires_at);
```

**Test-first sequence:**
1. `test_password_roundtrip` — `verify_password(hash_password("x"), "x")` is True; wrong password False.
2. `test_password_hash_is_argon2id` — `hash_password` output starts with `$argon2id$`.
3. `test_session_create_and_resolve` — create → cookie string → resolve → `user_id` matches.
4. `test_session_expiry_enforced` — session past `expires_at` fails to resolve.
5. `test_session_revoke_deletes_row` — `revoke_session(id)` removes row, resolve now None.
6. `test_cookie_tampering_rejected` — flip one char in cookie → resolve returns None (HMAC fails).

**Commit:** `feat(auth): users + sessions schema with argon2id + signed cookie`

---

## Phase 2 — Multi-tenant column migration

**Files touched:**
- NEW `backend/migrations/0002_multi_tenant.up.sql`
- NEW `backend/migrations/0002_multi_tenant.down.sql`
- MOD `backend/src/repositories/database.py` — add `tenant_id` to inserts, widen UNIQUE to `(tenant_id, normalized_company, normalized_title)`
- MOD `backend/src/models.py` — `Job.tenant_id: str = DEFAULT_TENANT_ID` (the well-known `00000000-0000-0000-0000-000000000001`)
- NEW `backend/src/core/tenancy.py` — `DEFAULT_TENANT_ID`, `require_tenant(request) -> str` FastAPI dependency
- NEW `backend/tests/test_tenancy_isolation.py` — **dedicated test class** per success criteria

**Migration steps (in `0002_multi_tenant.up.sql`):**
1. `INSERT INTO users(id, email, password_hash) VALUES ('00000000-0000-0000-0000-000000000001', 'local@job360.local', '!')` — placeholder user for existing single-user data. Password hash is `!` (un-loginable — argon2 never produces `!`).
2. `ALTER TABLE jobs ADD COLUMN tenant_id TEXT DEFAULT '00000000-0000-0000-0000-000000000001'`.
3. `ALTER TABLE user_actions ADD COLUMN tenant_id TEXT DEFAULT '00000000-0000-0000-0000-000000000001'`.
4. `ALTER TABLE applications ADD COLUMN tenant_id TEXT DEFAULT '00000000-0000-0000-0000-000000000001'`.
5. Drop old `UNIQUE(normalized_company, normalized_title)`, create `UNIQUE(tenant_id, normalized_company, normalized_title)`. In SQLite this requires the standard rename-create-copy-drop pattern — spelled out in the `.up.sql`.
6. Index `idx_jobs_tenant_date ON jobs(tenant_id, first_seen DESC)`.

**Zero-downtime rule:** Column DEFAULT ensures rows without an explicit `tenant_id` on insert still land in tenant 1, so single-user CLI invocations keep working unchanged.

**Test-first sequence (tenant isolation):**
1. `test_single_user_data_lands_in_default_tenant` — existing `insert_job()` call (no tenant arg) → row has `tenant_id = DEFAULT_TENANT_ID`.
2. `test_tenant_a_cannot_read_tenant_b_jobs` — insert under tenant A, query under tenant B → empty list.
3. `test_tenant_a_cannot_read_tenant_b_actions` — same for `user_actions`.
4. `test_tenant_a_cannot_read_tenant_b_applications` — same for `applications`.
5. `test_uniqueness_widened_to_tenant_scope` — same `(company, title)` can coexist across two tenants, cannot duplicate within one tenant.
6. `test_existing_normalized_key_unchanged` — `Job.normalized_key()` output byte-equal before/after. (CLAUDE.md rule #1.)

**Commit:** `feat(tenancy): add tenant_id to jobs/actions/applications with default tenant backfill`

---

## Phase 3 — `user_feed` table + FeedService (read path)

**Files touched:**
- NEW `backend/migrations/0003_user_feed.up.sql`
- NEW `backend/migrations/0003_user_feed.down.sql`
- NEW `backend/src/services/feed.py` — `FeedService` class
- NEW `backend/tests/test_feed_service.py`

**Schema:**
```sql
CREATE TABLE user_feed (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    score INTEGER NOT NULL CHECK (score BETWEEN 0 AND 100),
    bucket TEXT NOT NULL,                         -- '24h','24_48h','48_72h','3_7d'
    status TEXT NOT NULL DEFAULT 'active',        -- 'active','skipped','stale','applied'
    notified_at TIMESTAMP NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, job_id)
);
CREATE INDEX idx_feed_dashboard ON user_feed(user_id, bucket, score DESC) WHERE status='active';
CREATE INDEX idx_feed_notify ON user_feed(user_id, status, created_at) WHERE notified_at IS NULL AND status='active';
CREATE INDEX idx_feed_cascade ON user_feed(job_id);
```

**FeedService surface (minimum):**
```python
class FeedService:
    async def list_for_user(self, user_id: str, *, bucket: str|None=None,
                            status: str='active', limit: int=200) -> list[FeedRow]: ...
    async def list_pending_notifications(self, user_id: str, *,
                                         min_score: int, limit: int=15) -> list[FeedRow]: ...
    async def mark_notified(self, feed_ids: list[int]) -> None: ...
    async def update_status(self, user_id: str, job_id: int, status: str) -> None: ...
    async def cascade_stale(self, job_id: int) -> int:  # returns rows updated
        ...
```

**Test-first sequence:**
1. `test_list_for_user_returns_active_only` — two rows, one skipped → dashboard query returns one.
2. `test_list_pending_notifications_filters_by_threshold_and_not_notified` — scores 60, 85, 85 (notified) → only unsent 85 returned.
3. `test_mark_notified_updates_timestamp` — after call, row excluded from pending.
4. `test_update_status_skipped_hides_from_dashboard` — tick → dashboard empty.
5. `test_cascade_stale_marks_job_across_users` — two users feeding same job_id, cascade sets both to stale.

**Commit:** `feat(feed): user_feed table + FeedService read path`

---

## Phase 4 — `FeedService.ingest_job` (write path) + pre-filter cascade

**Files touched:**
- MOD `backend/src/services/feed.py` — add `ingest_job(tenant_id, user_profile, job) -> Optional[FeedRow]`
- NEW `backend/src/services/prefilter.py` — 4-stage cascade (location → experience → skills overlap → scoring)
- NEW `backend/tests/test_prefilter.py`
- MOD `backend/tests/test_feed_service.py` — add ingest tests

**Cascade semantics:**
```python
def passes_prefilter(profile, job) -> bool:
    return (
        location_ok(profile.preferred_locations, job)
        and experience_ok(profile.experience_level, job)
        and skill_overlap_ok(profile.skills, job, min_overlap=1)
    )
```
Only jobs passing all three stages enter `JobScorer.score()`. Matches blueprint §2 "99% elimination before scoring."

**Test-first sequence:**
1. `test_prefilter_eliminates_foreign_only_jobs` — London user, US-only job → filtered out.
2. `test_prefilter_eliminates_mismatched_seniority` — junior candidate, senior role → filtered out.
3. `test_prefilter_eliminates_zero_skill_overlap` — ML profile, accountancy job → filtered out.
4. `test_prefilter_retains_valid_matches` — matching job → passes.
5. `test_ingest_job_skipped_if_prefilter_fails` — no row created in `user_feed`.
6. `test_ingest_job_stores_score_and_bucket` — matching job → row with correct score + bucket.
7. `test_ingest_job_is_idempotent` — second ingest with same (user, job) → UPDATE, not duplicate row.

**Commit:** `feat(prefilter): 4-stage cascade feeding FeedService.ingest_job`

---

## Phase 5 — ARQ worker + notification ledger (`score_new_job` task)

**Dependencies added:**
- `arq>=0.26.0`
- `redis>=5.0.0` (transitive; pin for reproducibility)

**Files touched:**
- NEW `backend/migrations/0004_notification_ledger.up.sql`
- NEW `backend/migrations/0004_notification_ledger.down.sql`
- NEW `backend/src/workers/__init__.py`
- NEW `backend/src/workers/settings.py` — `WorkerSettings` class for ARQ
- NEW `backend/src/workers/tasks.py` — `score_new_job`, `send_digest`
- NEW `backend/tests/test_worker_tasks.py`
- MOD `backend/pyproject.toml` — add `arq`

**notification_ledger schema:**
```sql
CREATE TABLE notification_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    job_id INTEGER NOT NULL,
    channel TEXT NOT NULL,
    status TEXT NOT NULL,                 -- 'queued','sent','failed','dlq'
    sent_at TIMESTAMP NULL,
    error_message TEXT NULL,
    retry_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, job_id, channel)      -- idempotency
);
CREATE INDEX idx_ledger_user_status ON notification_ledger(user_id, status);
```

**`score_new_job(ctx, job_id, tenant_id)` behaviour:**
1. Load job row. If missing → no-op.
2. For each active user in tenant → `FeedService.ingest_job(user_profile, job)` (pre-filter + score + write).
3. For each user where `score >= user.instant_threshold` → queue `send_notification(user_id, job_id, 'instant')`.
4. Uses ARQ's built-in `job_id` (deterministic: `score:{job_id}:{tenant_id}`) for idempotency — if the worker restarts mid-pass, the same job won't be rescored twice.

**Test-first sequence (no Redis needed — test via direct function call with fake `ctx`):**
1. `test_score_new_job_creates_feed_rows_for_each_passing_user` — 3 users, 1 job, 2 pass prefilter → 2 rows in `user_feed`.
2. `test_score_new_job_is_idempotent` — call twice → still 2 rows (UPSERT semantics).
3. `test_score_new_job_queues_instant_notification_above_threshold` — user threshold 80, job scores 85 → ledger row `status='queued'`. Mock the enqueue via `ctx['enqueue_job']`.
4. `test_notification_ledger_unique_per_channel` — second `INSERT` with same (user, job, channel) fails cleanly (caller catches → idempotent).
5. `test_worker_settings_loads_redis_url_from_env` — missing env → sensible default.

**Commit:** `feat(worker): ARQ score_new_job task + notification_ledger`

---

## Phase 6 — Apprise channel wrapper + test-send

**Dependency added:**
- `apprise>=1.7.0`
- `cryptography>=42.0.0` — Fernet (already transitive via `httpx`, but pin explicitly)

**Files touched:**
- NEW `backend/migrations/0005_user_channels.up.sql`
- NEW `backend/migrations/0005_user_channels.down.sql`
- NEW `backend/src/services/channels/__init__.py`
- NEW `backend/src/services/channels/crypto.py` — `encrypt()`, `decrypt()` (Fernet)
- NEW `backend/src/services/channels/dispatcher.py` — `send_to_channel(user_id, channel, job)` — builds Apprise URL → `AppriseAsset` → `notify()`
- NEW `backend/src/services/channels/formatters.py` — per-channel payload builders (Slack Block Kit, Discord embed, Telegram markdown, plain email)
- MOD `backend/src/workers/tasks.py` — `send_notification(ctx, user_id, job_id, trigger)` task calls dispatcher
- NEW `backend/tests/test_channels_crypto.py`
- NEW `backend/tests/test_channels_dispatcher.py`

**`user_channels` schema:**
```sql
CREATE TABLE user_channels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    channel_type TEXT NOT NULL,            -- 'email','slack','discord','telegram','webhook'
    display_name TEXT NOT NULL,            -- user-facing label
    credential_encrypted BLOB NOT NULL,    -- Fernet-encrypted URL/token
    key_version INTEGER NOT NULL DEFAULT 1,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_channels_user ON user_channels(user_id, enabled);
```

**Test-first sequence:**
1. `test_fernet_roundtrip` — encrypt/decrypt restores plaintext.
2. `test_channel_dispatcher_routes_to_correct_apprise_url` — mock `apprise.Apprise`, assert it was called with decrypted URL.
3. `test_test_send_returns_false_on_apprise_failure` — mock `notify()` returning False → dispatcher returns `{"ok": False, "error": ...}`.
4. `test_send_notification_task_writes_ledger_sent` — after success, ledger row `status='sent'`, `sent_at` set.
5. `test_send_notification_retries_on_5xx` — first call raises 503, second succeeds → ledger `retry_count=1`.

**Commit:** `feat(channels): Apprise dispatcher + user_channels schema + test-send`

---

## Phase 7 — FastAPI routes + middleware

**Files touched:**
- NEW `backend/src/api/middleware/auth.py` — `SessionAuthMiddleware` resolves cookie → `request.state.user`
- NEW `backend/src/api/routes/auth.py` — `POST /auth/register`, `POST /auth/login`, `POST /auth/logout`, `GET /auth/me`
- NEW `backend/src/api/routes/channels.py` — `GET /settings/channels`, `POST /settings/channels`, `DELETE /settings/channels/{id}`, `POST /settings/channels/{id}/test`
- MOD `backend/src/api/main.py` — register new routers, install middleware
- MOD `backend/src/api/routes/{jobs,actions,profile,pipeline,search}.py` — **add `tenant_id = Depends(require_tenant)`** to every endpoint; filter all queries by tenant
- MOD `backend/src/api/main.py:20` — CORS: read `FRONTEND_ORIGIN` from env, default `http://localhost:3000`
- NEW `backend/tests/test_auth_routes.py`
- NEW `backend/tests/test_channels_routes.py`
- MOD `backend/tests/test_api.py` — add auth header fixture, update existing tests

**Test-first sequence:**
1. `test_register_creates_user_and_returns_cookie` — POST creates row, response has `Set-Cookie: session=...`.
2. `test_register_rejects_duplicate_email` — 409.
3. `test_login_wrong_password_rejected` — 401.
4. `test_logout_revokes_session` — subsequent `/auth/me` returns 401.
5. `test_unauthenticated_request_to_protected_endpoint_returns_401` — existing `/api/jobs` without cookie → 401.
6. `test_tenant_a_gets_only_own_jobs` — integration: two users, each inserts job via pipeline, `/api/jobs` under each cookie returns only that user's.
7. `test_test_send_returns_ok_true_on_mock_apprise_success` — integration for test-send button.

**Commit group (2 commits):**
- `feat(api): session middleware + /auth routes`
- `feat(api): /settings/channels routes + tenant-scoped queries in existing endpoints`

---

## Phase 8 — Frontend

**Files touched:**
- NEW `frontend/src/app/(auth)/login/page.tsx`
- NEW `frontend/src/app/(auth)/register/page.tsx`
- NEW `frontend/src/app/settings/channels/page.tsx`
- NEW `frontend/src/components/settings/ChannelForm.tsx` — form per channel type, test-send button, live status pill
- MOD `frontend/src/lib/api.ts` — `credentials: 'include'` on all `fetch()` calls so session cookie is sent
- MOD `frontend/src/lib/types.ts` — `Channel`, `ChannelTestResult`, `User` types
- MOD `frontend/src/components/layout/Navbar.tsx` — show email + logout when signed in, login/register buttons when not

**Frontend testing:** No Playwright wiring in Batch 2 scope. Smoke-test manually via `npm run dev` during Phase 9 verify.

**Commit:** `feat(frontend): auth pages + /settings/channels config UI`

---

## Phase 9 — Verify + handoff

1. **Full pytest run** from `backend/` — record new `passed` count. Must be `≥ 420 + new_tests_added`. No failure bucket other than the pre-existing 4 (or fewer if we fix any as a side effect; never add).
2. **Lint** — `ruff check backend/`. Must be clean on new code.
3. **CLAUDE.md update** — add sections:
   - "New tables (Batch 2): users, sessions, user_feed, notification_ledger, user_channels, _schema_migrations"
   - "New worker: ARQ on Redis. Run via `arq src.workers.settings.WorkerSettings`"
   - "New env vars: SESSION_SECRET, CHANNEL_ENCRYPTION_KEY, REDIS_URL, FRONTEND_ORIGIN"
   - "Auth model: signed session cookie, argon2id passwords; endpoints under `/api/auth/*`"
4. **IMPLEMENTATION_LOG.md** — append Batch 2 completion entry (DRAFT — reviewer validates) with test deltas, KPI deltas, what shipped, what deferred.
5. **Memory save** — draft `project_pillar3_batch_2_done.md` for the reviewer to persist.
6. **Git push** — `git push -u origin pillar3/batch-2`.
7. **Print** — `READY_FOR_REVIEW pillar3/batch-2 @ <hash>` and stop.

---

## Test-suite impact projection

| Category | Tests added | Notes |
|---|---:|---|
| test_migrations.py | 4 | Runner correctness |
| test_auth_passwords.py | 2 | Argon2id roundtrip |
| test_auth_sessions.py | 4 | Create/resolve/revoke/tamper |
| test_tenancy_isolation.py | 6 | Dedicated class per success criteria |
| test_feed_service.py | 12 | Read + ingest paths |
| test_prefilter.py | 4 | 4-stage cascade |
| test_worker_tasks.py | 5 | ARQ idempotency, ledger, threshold |
| test_channels_crypto.py | 2 | Fernet roundtrip + wrong-key rejection |
| test_channels_dispatcher.py | 3 | Apprise routing + retry + ledger write |
| test_auth_routes.py | 5 | Register/login/logout/me + dup |
| test_channels_routes.py | 4 | CRUD + test-send |
| test_api.py updates | +2 net | Tenant-scoped existing endpoints |
| **Total new/updated** | **≈53** | Target post-Batch-2 passing: **≥ 473** |

---

## Risk register

| Risk | Likelihood | Mitigation |
|---|---|---|
| Multi-tenant column rename breaks existing `INSERT OR IGNORE` path | Medium | Phase 2 test `test_single_user_data_lands_in_default_tenant` runs every existing insert path; migration DEFAULT prevents NULL |
| ARQ import at top of `tasks.py` fails CI because Redis not available | Medium | Guard `redis` usage behind `ctx['redis']` which ARQ provides; tests call task functions directly with a stub ctx — no Redis ever touched in pytest |
| Apprise's `notify()` call hits a real webhook in tests | High (easy to regress) | `conftest.py` autouse fixture monkeypatches `apprise.Apprise.notify` to a no-op returning True by default; tests opt-in via `mock_apprise(result=False)` |
| Frontend session cookie blocked by SameSite | Medium | Dev config: `SameSite=Lax`, `Secure=False` in dev (explicit), `Secure=True` behind `ENV=prod` |
| Existing `test_api.py` 6 pre-existing failures get worse when we add auth | Medium | Pre-existing failures are already `sqlite3.OperationalError` during DB init — independent of auth; adding auth middleware must not add new failures. Verify by running after Phase 7 and comparing failure count |
| Feed table grows without bound | Low | TTL cleanup defined in Phase 9 nightly cron; scope this batch is table + index, not cleanup worker (punt to Batch 3) |

---

## Out-of-scope — explicit deferrals (punt to later batches)

- **Postgres migration** — Batch 3 first step (D4 decision).
- **FAISS / embedding pre-filter** — Batch 3+ (blueprint §2 "Phase 3").
- **SSE dashboard** — Batch 3 if needed (D3 decision).
- **Quiet hours / digest scheduler cron** — scoped but not wired. Phase 9 defines the cleanup cron; `send_digest` task exists but is called only by tests in this batch. Scheduling glue lands in Batch 3.
- **SMS channel** — never (blueprint §1 cost analysis).
- **Celery migration** — Batch 3+ at scale.
- **Amazon SES integration** — Batch 4 per research.
- **Password reset / email verification flows** — Batch 3. Auth ships with register/login/logout/me only.
- **2FA** — post-launch.
- **Frontend: SWR/TanStack Query** — D11 decision defers to a later cleanup.
- **Prometheus metrics for notification latency** — stubbed in Batch 1 KPI exporter, wired in Batch 3 once digest scheduler runs in prod.

---

*End of plan. Begin Phase 0.*
