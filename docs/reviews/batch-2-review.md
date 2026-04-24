# Batch 2 Review — 2026-04-18 — commit d877bd6

## Verdict

[ ] APPROVED   [x] CHANGES REQUIRED

Batch 2 delivers a lot that is correctly shaped: a clean migration runner,
argon2id + signed-cookie auth, a `user_feed` SSOT table + service, a
3-stage pre-filter, a Fernet-wrapped Apprise dispatcher, notification
ledger with per-channel UNIQUE idempotency, and 77 new passing tests with
zero regressions against the locked 420 baseline (same 4 failure buckets
as Batch 1). The decisions doc is unusually thorough — 12 decisions with
alternatives and tradeoffs — and the SQL-level tenant isolation test
class does exactly what it claims.

However, three commits overclaim in the same pattern that blocked Batch
1 round 1 — **the schema ships but the caller code path doesn't** — and
two production-time footguns (fallback session secret, fallback Fernet
key) are committed as constants that silently take effect whenever the
env var is unset. None is hard to fix, and all but one are already
self-disclosed in the completion entry's "What got deferred" section —
but shipping as-is means commit subjects like "feat(tenancy): per-user
user_actions + applications" and "feat(worker): score_and_ingest task"
do not describe what runs in production. Fix the overclaims (either wire
the callers or reword the subjects + the completion entry headline), add
a fail-closed check for the two secrets, pin the four new deps in
`pyproject.toml`, and this batch ships clean.

---

## Critical (P0 — block merge)

_None._ No rule-#1/#2/#3 violations, no data-loss path, no live HTTP in
tests, no `normalized_key()` edits, no `purge_old_jobs` edits, no
`BaseJobSource` edits, no regressions (497 passed / 24 failed / 3
skipped — failing count and bucket composition identical to the locked
baseline). Auth routes are new-only, not retrofits, so the auth surface
cannot break existing flows.

---

## High (P1 — fix before merge)

### P1-1. `JobDatabase` action / application writes still single-tenant

- **Where:** `backend/src/repositories/database.py:277` (`insert_action`),
  `:286` (`delete_action`), `:290` (`get_actions`), `:297`
  (`get_action_counts`), `:304` (`get_action_for_job`), `:314`
  (`create_application`), `:322` (`advance_application`), `:333`
  (`_get_application`), `:345` (`get_applications`), `:365`
  (`get_application_counts`), `:371` (`get_stale_applications`).
- **What shipped vs what the commit implies:** Migration 0002 rebuilt
  `user_actions` and `applications` with `user_id` columns + widened
  `UNIQUE(user_id, job_id)`. Commit `1a4c07d` is titled
  `feat(tenancy): per-user user_actions + applications`. But every one
  of the 11 methods above still SELECTs / UPDATEs / DELETEs
  **without** referencing `user_id`, and every INSERT omits it —
  relying on the column DEFAULT
  (`'00000000-0000-0000-0000-000000000001'`). The effect:
  - All action / application writes collapse onto the placeholder
    tenant regardless of which real user triggered them.
  - `INSERT OR REPLACE INTO user_actions(job_id, action, notes,
    created_at)` at line 280 matches the UNIQUE constraint on
    `(default_tenant, job_id)`, so a second real user liking the same
    job **silently overwrites** the first real user's action row at
    the DB level. The `UNIQUE(user_id, job_id)` widening provides no
    protection because neither writer supplies a user_id.
  - `get_actions()` / `get_application_counts()` leak every user's
    rows because there is no `WHERE user_id = ?`.
- **Reachability:** `/api/jobs/{id}/action`, `/api/actions`,
  `/api/actions/counts`, `/api/pipeline/*` are all unauthenticated and
  reachable from any browser. The auth dependency exists
  (`src/api/auth_deps.py::require_user`) but is not wired onto any
  pre-existing route.
- **Self-disclosure:** `docs/IMPLEMENTATION_LOG.md` §"What got
  deferred" item 1 explicitly names the deferral: "Wrapping existing
  `/api/jobs`, `/api/actions`, `/api/profile`, `/api/pipeline`,
  `/api/search` in `Depends(require_user)` … punt to Batch 2.1 or
  Batch 3." That disclosure is honest. **The mismatch is that the
  commit subject and the Batch-2 headline still sell full tenancy
  even though the only tenant-scoped data plane is `user_channels` +
  `user_feed`.**
- **Evidence grep:** `grep -n "user_id" backend/src/repositories/
  database.py` → **0 hits**. The entire repo layer is user-unaware.
  `grep -n "require_user\|CurrentUser" backend/src/api/routes/
  {jobs,actions,profile,pipeline,search}.py` → **0 hits**.
- **Fix suggestion** — pick one:
  * **(a) Wire it, per plan Phase 7.** Add `user_id: str` parameter to
    every `JobDatabase` action/application method, default to
    `DEFAULT_TENANT_ID` for backwards-compat with CLI paths, filter
    every query by it. Add `Depends(require_user)` to the five
    pre-existing routers and thread `user.id` through. This is the
    mechanical work the plan originally scoped; ≈80 LOC across 5
    route files + 11 repo methods + 2 integration tests
    (`tenant_a_cannot_read_tenant_b_via_/api/actions`). Does not touch
    the scoring or source layer.
  * **(b) Accept the deferral but reword the overclaim.**
    - Rewrite commit `1a4c07d`'s subject to `feat(tenancy): schema for
      per-user user_actions + applications (writes remain
      default-tenant until routes are auth-gated)`.
    - Rename `docs/IMPLEMENTATION_LOG.md` §"What shipped" item 3
      from "Tenancy — … prove A↔B separation" to something like
      "Tenancy schema — `user_actions` / `applications` rebuilt with
      `user_id` column + widened UNIQUE; A↔B separation is proven at
      the SQL layer but the API/repository layer still defaults
      every write to the placeholder tenant pending Batch 2.1."
    - Add a comment at the top of every affected `JobDatabase`
      method: `# TODO(batch-2.1): accept user_id, filter queries.
      Currently defaults to DEFAULT_TENANT_ID — two real users'
      actions will overwrite each other.`
  Option (a) is preferred — it is <1 hour of work, makes the headline
  honest, and eliminates the silent-overwrite hazard if the CLI and
  any real user ever run concurrently. Option (b) is acceptable if
  the team has already decided Batch 2.1 is imminent.

### P1-2. `score_and_ingest` never runs the per-user scorer

- **Where:** `backend/src/workers/tasks.py:101-103` — inside the
  per-user loop: `score = int(job_row["match_score"] or 0)`.
- **What shipped vs what the commit implies:** Commit `e60b285
  feat(worker): score_and_ingest task + notification_ledger`. Plan
  Phase 5 specified: "For each active user in tenant →
  `FeedService.ingest_job(user_profile, job)` (pre-filter + score +
  write)." Decisions doc D8 is explicit: "The existing `JobScorer`
  becomes step 4 of the cascade rather than step 1." But the shipped
  code reuses the **catalog-level** `jobs.match_score` for every user,
  so:
  - Every user gets the **same** score for the same job, which makes
    the per-user `instant_threshold` branch at line 116
    (`if score >= threshold`) collapse to a global on/off switch.
  - The blueprint §2 "hybrid two-stage retrieval" is implemented as
    one stage (prefilter) plus a constant lookup — not the intended
    "cheap filter → cheap per-user score."
  - The docstring at line 100 and the in-code comment at 102
    acknowledge the gap ("FeedService callers may overwrite with a
    JobScorer call when they have a per-user SearchConfig in scope")
    — but there is no such caller, and no Phase-4 `ingest_job` method
    on `FeedService` (only `upsert_feed_row`). Plan Phase 4's
    `ingest_job(tenant_id, user_profile, job)` did not ship.
- **Self-disclosure:** The completion entry does not name this. §"What
  got deferred" names the `user_profiles` table migration as
  deferred, which is the upstream dependency, but does not flag that
  the scoring step of the cascade went with it.
- **Fix suggestion** — pick one:
  * **(a) Wire it now.** Load each user's `SearchConfig` (single-user
    `user_profile.json` today, keyed by `user_id == DEFAULT_TENANT_ID`
    for the placeholder tenant) and call `JobScorer(config).score(job)`
    per user. Pre-Batch-3 this is degenerate (same config for every
    non-placeholder user), but the call site is correct and Batch 3's
    `user_profiles` table lights it up for real.
  * **(b) Reword the commit + add the deferral to the log.** Rename
    `e60b285` to `feat(worker): prefilter + ingest task +
    notification_ledger (per-user scoring deferred to user_profiles —
    Batch 3)`. Add a §"What got deferred" bullet: "Per-user
    `JobScorer` invocation inside `score_and_ingest` — every user
    currently sees the catalog `match_score`; true per-user scoring
    requires the `user_profiles` table."
  Option (a) is the cleanest because it closes the loop, matches the
  plan, and doesn't leave a buried TODO that Batch 3 will find the
  hard way. But (b) is acceptable and cheap.

### P1-3. Production-time fallback secrets are committed as constants

- **Where:**
  - `backend/src/api/auth_deps.py:23`:
    `_DEFAULT_SECRET = "dev-insecure-" + "x" * 40`, used whenever
    `SESSION_SECRET` env var is unset.
  - `backend/src/services/channels/crypto.py:25-28`: fallback
    `Fernet("mIaARLi5Yd8zKLTZBtRGcKB6a83kfkSTEhtfcRwGmF4=")` whenever
    `CHANNEL_ENCRYPTION_KEY` is unset.
- **Why it matters:** On a fresh prod deploy where the operator
  forgets one of these env vars (both are new — not in
  `.env.example`), the server comes up *green* and starts signing
  session cookies / encrypting webhook tokens with a value that is
  publicly visible in the git log. Any attacker who reads the repo
  can forge a valid session for any user, or decrypt every stored
  channel credential. The inline comments even say the words "dev /
  test fallback — never used in production" but the runtime cannot
  tell dev from prod.
- **Fix suggestion:** fail-closed. On FastAPI startup, if
  `os.environ.get("ENV", "dev") == "prod"` and either secret is
  unset, raise at `lifespan` entry so the process never serves
  traffic. Alternative: raise unconditionally from `_secret()` /
  `_fernet()` when unset and require the test suite to set explicit
  values (it already does — `set_test_key()` exists for crypto, and
  auth tests can set `SESSION_SECRET` via `monkeypatch.setenv`). The
  8-test `test_auth_sessions.py` suite does not rely on the default
  secret — it uses explicit `secret=` args — so removing the default
  is safe.
- **Secondary:** `backend/src/api/routes/auth.py:46` hardcodes
  `secure=False` on the session cookie with a comment "flip to True
  behind TLS terminator in prod." Manual flag flips rot. Gate on
  `os.environ.get("ENV") == "prod"` in the same pass.

---

## Medium (P2 — follow-up before Batch 3)

### P2-1. `pyproject.toml` does not declare the four new runtime deps

- **Where:** `backend/pyproject.toml:6-24`. `argon2-cffi`,
  `itsdangerous`, `apprise`, and `email-validator` are imported from
  the new code but not in `[project.dependencies]`. `cryptography` is
  a transitive via `httpx` but should also be pinned explicitly
  because we now depend on `cryptography.fernet` directly.
- **Why:** First fresh-venv CI run (or any new contributor doing
  `pip install -e .`) fails at import time. Self-disclosed in the
  completion entry §"Surprises" bullet 5, so not a surprise to the
  generator — just not done.
- **Fix:** add four lines:
  ```
  "argon2-cffi>=23.1.0",
  "itsdangerous>=2.2.0",
  "apprise>=1.7.0",
  "email-validator>=2.1.0",
  "cryptography>=42.0.0",
  ```

### P2-2. `dispatcher.test_send` takes a raw `channel_id` with no ownership check

- **Where:**
  `backend/src/services/channels/dispatcher.py:130-150`. The function
  `SELECT * FROM user_channels WHERE id = ?` — no `user_id` filter.
- **Why it works today:** the only caller is
  `/api/settings/channels/{id}/test` which does an ownership SELECT
  first and raises 404 if not owned, so the current HTTP surface is
  safe.
- **Why flag it:** the service boundary (dispatcher module) does not
  enforce the invariant; the next caller (future ARQ task, future
  admin route, future digest path) that forgets the HTTP-level
  ownership SELECT has an IDOR. The fix is defensive: require
  `user_id: str` on the `test_send(db, user_id, channel_id)`
  signature and filter `WHERE id = ? AND user_id = ?`; the HTTP layer
  already has `user.id` in scope.

### P2-3. `_notify_async` blocks the event loop on sync Apprise

- **Where:**
  `backend/src/services/channels/dispatcher.py:113-119`. The
  fallback when `ap.async_notify` is absent is `return
  bool(ap.notify(...))` — a sync network call inside an async
  function. Apprise holds the GIL for the duration of the SMTP/HTTPS
  handshake + response.
- **Why:** At 1 user or unit tests this is fine. At the intended
  10K-user scale from blueprint §2 with polling + instant
  notifications converging on the same loop, this serialises every
  send. Should be wrapped in `asyncio.to_thread(ap.notify, ...)`.
- **Why P2 not P1:** the ARQ worker runtime isn't wired yet
  (explicit deferral); nothing in prod currently calls this. Fix
  before the worker schedule lands.

### P2-4. Migration 0002's SELECT is brittle to future `user_actions` columns

- **Where:**
  `backend/migrations/0002_multi_tenant.up.sql:29-31` and `:48-51`.
  The rebuild uses an explicit column list (`id, job_id, action,
  notes, created_at`) — correct today, but if a future Batch 1.x
  ever adds e.g. a `confidence` column to `user_actions`, this
  migration would silently drop it on any DB that happens to be
  pre-0002 at the time. (Low probability — but the reviewer TODO
  from the generator itself flagged "0002 SELECT column audit.")
- **Fix:** add a comment at the top of the migration listing the
  columns mirrored, and a unit test `test_migration_0002_preserves_
  every_legacy_column` that fails if `PRAGMA table_info(user_actions)`
  on a fresh DB misses anything from a documented list.

### P2-5. `score_and_ingest` notifications threshold defaults to 80 globally

- **Where:** `backend/src/workers/tasks.py:95` —
  `targets = [(u["id"], FilterProfile(), 80) for u in users]`.
  Blueprint §1 and decisions doc D7 specify that the instant
  threshold is **per user** (`user.instant_threshold DEFAULT 80`).
  With no `user_preferences` table yet, every user is forced to 80.
- **Why P2:** compounds with P1-2 — today this is "every user gets
  the same hardcoded score compared against the same hardcoded
  threshold." Either reword the code comment + log entry, or ship
  the `user_preferences` table alongside this function (Batch 3
  dep).

---

## Low / polish (P3)

### P3-1. `idempotency_key()` helper is dead code

- `backend/src/workers/tasks.py:21-24` defines
  `idempotency_key(user_id, job_id, channel)` returning a sha1 hex,
  matching decisions doc D12. But it is never called — the
  `notification_ledger` UNIQUE constraint does the idempotency work.
  Either delete the helper or wire it into ledger inserts (storing
  the key in a dedicated column would let a future Redis cache layer
  dedup pre-DB; right now it's aspirational).

### P3-2. Decision D6 (`tenant_id` + `user_id` as separable concepts) diverges silently from the implementation

- `docs/plans/batch-2-decisions.md:115-121` says the two columns
  should be "SAME column initially — one user = one tenant" but
  "keep the concept separated (two columns would be possible) so
  Batch 3+ can introduce shared-workspace tenancy without an ALTER
  TABLE storm." The implementation just uses `user_id`. The
  `surprises` bullet in IMPLEMENTATION_LOG.md explains why (blueprint
  §3 makes `tenant_id` on `jobs` the wrong call), but doesn't update
  D6. This is YAGNI-correct — one column today is simpler — but
  should be reconciled by editing D6 to say "D6 REVISED 2026-04-18:
  single `user_id` column chosen after blueprint re-read; shared-
  workspace tenancy in Batch 4+ will add a separate `tenants` join
  table rather than a second column on every per-user row." Future
  readers who git-blame the decisions doc shouldn't have to reverse-
  engineer the divergence from code.

### P3-3. `core/tenancy.py` is a 2-line constant module — thin

- The file defines `DEFAULT_TENANT_ID` and nothing else. Plan Phase 2
  originally scoped a `require_tenant(request) -> str` FastAPI
  dependency in this file; that landed as `require_user` in
  `auth_deps.py` instead. Either inline the constant into
  `auth_deps.py` and delete the module, or add the planned
  `require_tenant` helper even if it's just
  `lambda user=Depends(require_user): user.id`. Currently the module
  is a naming placeholder that hasn't earned its keep.

### P3-4. `resolve_session` slides `last_seen` without error handling

- `backend/src/services/auth/sessions.py:83-85`. The UPDATE
  `last_seen = ?` is best-effort but any failure propagates — if the
  DB write fails, the session is rejected even though the signature
  check + row lookup succeeded. Either wrap in try/except and log,
  or comment "failure to slide last_seen is acceptable — ignore."
  Low priority; reachable only if SQLite is locked.

### P3-5. `runner.up` / `runner.down` does not emit the applied stem before commit

- `backend/migrations/runner.py`. If a migration raises mid-way, the
  operator sees the exception but not which stem was executing. Add
  `print(f"applying {stem}…")` / `print(f"reverting {last}…")`
  before the `executescript` call. Minor.

---

## Decision-doc compliance spot-check

| Decision | Compliant? | Notes |
|---|---|---|
| D1 ARQ task queue | Partial | Task functions exist as plain `async def`; `WorkerSettings` + Redis wiring explicitly deferred. |
| D2 Apprise + ARQ | ✓ | Dispatcher uses Apprise; per-channel rate-limit TODO in P2-3. |
| D3 Polling | ✓ | Frontend uses native `fetch`; no SSE. |
| D4 Stay on SQLite | ✓ | No Postgres code paths introduced. |
| D5 Session cookies | ✓ | argon2id + itsdangerous as specified. Default-secret footgun in P1-3. |
| D6 Shared schema + tenant_id column | **Divergent** | Implementation uses `user_id` only (no `tenant_id` column). Sensible but see P3-2. |
| D7 Three-tier trigger | Partial | Tier 1 (instant ≥80) exists; Tier 2 (digest) not wired; Tier 3 (suppress <30) not applied — every passing job is ingested regardless of score. |
| D8 Pre-filter cascade | Partial | Stages 1-3 shipped; stage 4 (per-user `JobScorer`) is P1-2. |
| D9 Plain SQL migrations | ✓ | `runner.py` is 150 lines; forward+reverse files present for every migration. |
| D10 Fernet crypto | ✓ | `user_channels.key_version` column pre-allocated for rotation. Default-key footgun in P1-3. |
| D11 Native `fetch` | ✓ | No SWR / TanStack introduced. |
| D12 Ledger idempotency | ✓ structurally | `UNIQUE(user_id, job_id, channel)` does the work; helper at P3-1 is unused. |

---

## CLAUDE.md rule audit

- **Rule #1 (`normalized_key` untouched):** PASS. `grep "def
  normalized_key" backend/src/models.py` → 1 hit, identical to
  Batch 1. Test
  `test_tenancy_isolation.py::test_normalized_key_unchanged` asserts
  byte-equal output.
- **Rule #2 (`BaseJobSource` untouched):** PASS. `git diff main..HEAD
  -- backend/src/sources/base.py` → empty.
- **Rule #3 (`purge_old_jobs` untouched):** PASS. `git diff main..HEAD
  -- backend/src/repositories/database.py` adds nothing inside
  `purge_old_jobs`. Batch 2 added a TODO in `FeedService.cascade_stale`
  for future TTL cleanup of `user_feed` but did not modify the
  existing purge.
- **Rule #4 (mock HTTP):** PASS. Apprise is monkeypatched per-test;
  reviewer-TODO P2 suggests a conftest autouse fixture for insurance.
  No live HTTP observed in new tests.
- **Rule #5 (run the suite):** PASS. 497 passed / 24 failed / 3
  skipped / 192s. Failure count and bucket composition identical to
  the locked 420 baseline (4 buckets: API sqlite ×6, cron ×3, setup
  ×5, source parsers ×7, matched_skills ×3).
- **Rule #6 (read before edit):** N/A — all shipping-code reads
  inferred from the diff.
- **Rule #7 (check before create):** PASS. New modules do not
  duplicate existing ones (`services/auth/`, `services/channels/`,
  `services/feed.py`, `services/prefilter.py`, `workers/tasks.py`
  are all greenfield).
- **Rule #8 (source count):** PASS. `SOURCE_REGISTRY` length
  assertion in `test_cli.py` unchanged — Batch 2 touches no source.
- **Rule #9 (scoring changes → test verification):** PASS (vacuously
  — scoring module `skill_matcher.py` untouched by Batch 2).

---

## Batch-2-specific audit checklist results

| Item | Result |
|---|---|
| ☐ Every new query scopes by `tenant_id` / `user_id` | **Partial.** `FeedService` ✓, `user_channels` routes ✓, `notification_ledger` writes ✓, `JobDatabase` action/application methods ✗ (P1-1), `dispatcher.test_send` ✗ (P2-2). |
| ☐ No endpoint returns another tenant's data even with forged IDs | **Partial.** `/settings/channels` ✓ (ownership filter on all ops). Pre-existing `/api/jobs`, `/api/actions`, `/api/pipeline`, `/api/profile`, `/api/search` remain unauthenticated and unscoped — explicit deferral per completion entry (P1-1). |
| ☐ CSV export scoped to tenant | ✓ (`jobs` is the shared catalog; its export is correctly unscoped). `user_actions` / `applications` have no dedicated export in this batch. |
| ☐ ARQ queue keys include `tenant_id` | N/A — ARQ not yet wired. `idempotency_key()` helper (unused) does include `user_id`. |
| ☐ Existing single-user data migrated to `tenant_id=1`, nothing lost | ✓. Migration 0002 INSERT-SELECT pattern preserves `id`, `job_id`, `action`, `notes`, `created_at` for `user_actions` and all 6 columns for `applications`. Brittle to future added columns (P2-4). |
| ☐ Forward+reverse migrations run on fresh DB | ✓. `test_migrations.py` covers up/down/idempotent/status. `0002.up.sql` and `0002.down.sql` are inverse (table rebuild, DELETE placeholder user on down). |
| ☐ No CV text / email / name / password / webhook URL in log strings | ✓. `grep -rn "logger\|logging" backend/src/services/auth/ backend/src/services/channels/ backend/src/workers/` returns nothing. `dispatcher` truncates exception strings to 500 chars — good. Existing `email_notify.py` / `slack_notify.py` / `discord_notify.py` log URLs on failure, but those are pre-Batch-2 and out of scope. |
| ☐ Per-user tokens hashed at rest | ✓ passwords hashed with argon2id (not truncated, not stored raw). Channel credentials encrypted with Fernet. Session IDs stored as 128-bit hex — not hashed, but not sensitive (the signature in the cookie is what authenticates, not the raw ID in the DB). |
| ☐ All 5 (sic: 12) decisions have a recommendation + justification | ✓ — all 12 decisions have 2-3 options with table + reason paragraph + risks. |
| ☐ Implementation matches the recommendation (if not, surprise logged) | **Partial.** D6 divergence not reconciled in the decisions doc (P3-2). D7 tiers 2 and 3 not wired but disclosed. D8 stage 4 missing and **not** disclosed (P1-2). D1 ARQ runtime deferral disclosed. |

---

## Commit trail

| # | SHA | Subject | Role |
|---|---|---|---|
| 1 | `381b3d0` | docs(pillar3): Batch 2 decisions + plan | brainstorming + TDD plan |
| 2 | `575eb8c` | feat(migrations): add forward/reverse SQL migration runner | Phase 0 |
| 3 | `e3ba487` | feat(auth): users + sessions with argon2id + signed cookie | Phase 1 |
| 4 | `1a4c07d` | feat(tenancy): per-user user_actions + applications | **overclaim — see P1-1** |
| 5 | `5932b61` | feat(feed): user_feed SSOT table + FeedService | Phase 3 |
| 6 | `b2f4873` | feat(prefilter): 99% 3-stage pre-filter cascade | Phase 4 |
| 7 | `e60b285` | feat(worker): score_and_ingest task + notification_ledger | **overclaim — see P1-2** |
| 8 | `99ef596` | feat(channels): Apprise dispatcher + Fernet credential storage | Phase 6 |
| 9 | `b4bf372` | fix(test): auth sessions fixture targets migration 0001 only | test isolation |
| 10 | `87d177f` | feat(api): /auth and /settings/channels routes + session middleware | Phase 7 |
| 11 | `4d6560c` | feat(frontend): login + register + /settings/channels pages | Phase 8 |
| 12 | `d877bd6` | docs: Batch 2 completion entry + CLAUDE.md appendix | handoff |

---

## Test deltas

| Metric | Locked baseline (31124fa) | After Batch 2 (d877bd6) | Delta |
|---|---:|---:|---:|
| Passing | 420 | 497 | +77 |
| Failing | 24 (4 buckets) | 24 (same 4 buckets) | 0 |
| Skipped | 3 | 3 | 0 |
| Run time | 167.32s | 192.11s | +24.79s |

Failure bucket composition verified unchanged: 6 `test_api.py` sqlite,
3 `test_cron.py` FileNotFoundError, 5 `test_setup.py` FileNotFoundError,
7 `test_sources.py` parse regressions, 3 `test_time_buckets.py`
matched-skills — same as Batch 1 completion. No new failures
introduced by Batch 2.

---

## Review methodology

**Round 1:** fetched `origin/pillar3/batch-2` at `d877bd6`, read the
pillar-3 blueprint, the decisions doc, the plan, and the locked
baseline. Read every new file under `backend/migrations/`,
`backend/src/services/{auth,channels,feed,prefilter}*`,
`backend/src/workers/`, `backend/src/api/{auth_deps,routes/auth,
routes/channels,main}.py`, and scanned the pre-existing routes for
auth wiring. Read the full `JobDatabase` action + application
methods. Ran the full test suite from `backend/` (`python -m pytest
tests/ --ignore=tests/test_main.py -q`) and confirmed the claimed
497/24/3 totals. Ran targeted greps:

- `grep -n "user_id" backend/src/repositories/database.py` → 0 hits
  (evidence for P1-1).
- `grep -n "require_user\|CurrentUser" backend/src/api/routes/
  {jobs,actions,profile,pipeline,search}.py` → 0 hits (evidence for
  P1-1).
- `grep -rn "log\.\|logger\.\|logging\.\|print(" backend/src/services/
  {auth,channels}/ backend/src/workers/` → 0 hits in new code
  (secret-hygiene PASS).
- `grep -n "JobScorer\|score_job\|score(" backend/src/workers/
  tasks.py` → 0 hits (evidence for P1-2).
- `grep -n "passlib\|argon2\|itsdangerous\|apprise\|email-validator"
  backend/pyproject.toml` → 0 hits (evidence for P2-1).
- `grep "def normalized_key" backend/src/models.py` → 1 hit (rule #1
  PASS).

`coderabbit:code-review` — CLI not available in the reviewer
worktree harness; manual audit performed against plan + rules above,
same as Batch 1 rounds 1-3.

---

**Signal (round 1):**

REVIEW_COMPLETE pillar3/batch-2 verdict=CHANGES

---

## Round 2 re-review — 2026-04-18 — commit d124ed5

### Round 2 verdict

[x] APPROVED   [ ] CHANGES REQUIRED

Four review-response commits (`ab8155a`, `5920703`, `48cea56`,
`d124ed5`) landed on `pillar3/batch-2` and resolve all three round-1
P1s plus P2-1 and P2-2. The remaining P2/P3 items are deferred with
explicit per-finding rationale in the new §"Review-response commits
(round 2) / Not addressed" block of `docs/IMPLEMENTATION_LOG.md` —
that's the standard Batch-1-style accepted-deferral discipline. Test
state re-verified locally: 498 passed / 24 failed / 3 skipped / 186s.
Failing-bucket composition identical to the locked baseline. Net
+78 new tests across Batch 2 (up from +77 in round 1 because P2-2's
`test_test_send_rejects_cross_user_channel_id` is new). No
regressions. No new P0 findings. Approving.

### P1 resolution audit

**P1-1 — `JobDatabase` action/application writes still single-tenant**
— RESOLVED via reviewer option (b) per commit `d124ed5`.

- `backend/src/repositories/database.py:275-283` — 9-line `TODO(batch-2.1)`
  block above `insert_action` explicitly names the silent-overwrite
  hazard: *"If two real users ever both hit `/api/jobs/{id}/action`
  before that wiring lands, the second writer silently overwrites the
  first via INSERT OR REPLACE."* Same shape above
  `create_application` at `:317-321`. Evidence grep: `grep -n
  "TODO(batch-2.1)" backend/src/repositories/database.py` → **2
  hits**, both at the method headers round-1 named.
- `docs/IMPLEMENTATION_LOG.md:291` — §"What shipped" item 3 reworded
  from "per-user user_actions + applications — prove A↔B separation"
  to "per-user SCHEMA — SQL-layer isolation proven, repo layer
  remains tenant-blind pending Batch 2.1" with all 11 method names
  explicitly enumerated and the mitigation ("shipped frontend has no
  registration, single `user_profile.json` keeps collision path
  unreachable") spelled out.
- `docs/IMPLEMENTATION_LOG.md:301` — §"What got deferred" item 1
  expanded from one sentence to three sentences naming the collision
  mechanism and the mitigation.
- New §"Review-response commits (round 2)" section lists each fix +
  each accepted deferral with per-item rationale (P2-3 gated on ARQ
  wiring, P2-4 low risk, P2-5 blocked on `user_profiles` table, etc).

Verdict: option (b) is executed cleanly. Commit `1a4c07d` retains its
original subject, but the completion entry's language now matches
what ships and the in-code TODOs prevent future readers from being
misled. Acceptable exit.

**P1-2 — `score_and_ingest` never runs the per-user scorer** —
RESOLVED via commit `5920703`.

- `backend/src/workers/tasks.py:95-112` — the "reuse catalog
  match_score" line at old `:103` is replaced with a proper per-user
  invocation: `score = int(scorer_fn(user_id, job))` when a
  `ctx['scorer']` is injected (test path), else `default_scorer =
  JobScorer(_default_search_config())` followed by
  `default_scorer.score(job)` (prod path).
- `_default_search_config()` at `:194-212` correctly handles the
  pre-Batch-3 degeneracy by loading `user_profile.json` if present
  (single config for all users, honest about being a placeholder) or
  falling back to `SearchConfig.from_defaults()`. The broad except
  catches any profile-load failure — acceptable for this transitional
  path; Batch 3's user_profiles table eliminates both the fallback
  and the swallow.
- `backend/tests/test_worker_tasks.py:115-152` — the key test now
  injects a scorer that returns **distinct scores per user** (alice
  85, bob 70) and asserts on the call list. The per-user invariant
  is now testable, not just plausible. This is the exact "plan-shape
  test that would have caught the bug" point the round-1 review
  raised — pleased to see it implemented.

Grep confirmation: `grep -n "JobScorer\|scorer_fn\|default_scorer"
backend/src/workers/tasks.py` → 6 hits; `grep -n "(\"alice\", 85)\|
(\"bob\", 70)" backend/tests/test_worker_tasks.py` → matches,
confirming distinct-score test exists.

**P1-3 — Fallback production secrets committed as constants** —
RESOLVED via commit `ab8155a`.

- `backend/src/api/auth_deps.py:26-42` — `_secret()` now raises
  `RuntimeError` with a one-liner generator hint (`python -c 'import
  secrets; print(secrets.token_urlsafe(64))'`) instead of returning
  `"dev-insecure-xxxx…x"`. The old `_DEFAULT_SECRET` module constant
  is deleted. Any prod deploy that forgets to set `SESSION_SECRET`
  now fails at first request, not silently.
- `backend/src/services/channels/crypto.py:18-33` — `_fernet()` now
  raises with the Fernet key generator hint instead of returning a
  Fernet built from the previously-committed constant
  `"mIaARLi5Yd8…"`. The hardcoded key is deleted — `git grep` on the
  branch confirms it is no longer present in source.
- `backend/src/api/routes/auth.py:40-51` — `_set_session_cookie()`
  now gates the `secure` flag on `os.environ.get("JOB360_ENV") ==
  "prod"` instead of the hardcoded `secure=False`. The manual "flip
  to True in prod" comment is gone; automatic now.
- All 34 auth + channel tests still pass — tests explicitly set both
  env vars via `monkeypatch` / `set_test_key()`, so removing the
  fallbacks broke nothing. Fail-closed works correctly.

### P2-1 + P2-2 resolution

**P2-1 — `pyproject.toml` missing 5 runtime deps** — RESOLVED via
`ab8155a`. `backend/pyproject.toml:24-30` adds
`argon2-cffi>=23.1.0`, `itsdangerous>=2.2.0`, `apprise>=1.7.0`,
`email-validator>=2.1.0`, `cryptography>=42.0.0` with inline
comments explaining each. A fresh `pip install -e backend/` now
succeeds without manual installs.

**P2-2 — `dispatcher.test_send` no ownership check at service
boundary** — RESOLVED via `48cea56`. Signature widened to
`test_send(db, channel_id, *, user_id: Optional[str] = None)`, with
the SELECT branching to include `AND user_id = ?` when supplied. The
route at `channels.py:123` passes `user_id=user.id` through, so the
HTTP layer does an ownership SELECT *and* the service layer filters
on user_id — genuine defense-in-depth. The new test
`test_test_send_rejects_cross_user_channel_id` proves mallory cannot
dispatch via alice's channel even with alice's real `channel_id`;
`MockApp.return_value.notify.call_count == 0` is asserted so a future
regression that drops the service-layer filter will fail the test.

### P3-2 reconciliation

`docs/plans/batch-2-decisions.md:122` — added a "REVISION 2026-04-18
(post-review)" block to D6 explaining the single-`user_id`-column
choice. The reasoning matches the Surprises lesson: blueprint §3
made clear `jobs` stays shared and only the 5 per-user tables
(`user_actions`, `applications`, `user_feed`, `notification_ledger`,
`user_channels`) carry scoping, and a single `user_id` column is
sufficient. Shared-workspace tenancy in Batch 4+ is now explicitly
scoped to a `user_tenants(user_id, tenant_id, role)` join table
rather than a second column per row. Future readers who git-blame
the decisions doc will now see the divergence explained in situ.

### Accepted deferrals (round 2 — documented by generator)

From `docs/IMPLEMENTATION_LOG.md` new §"Not addressed in round 2":

- **P1-1 option (a) full repo-layer scoping** — Batch 2.1 (~80 LOC
  across 5 route files + 11 repo methods + 2 integration tests).
  Accepted; option (b) is live with TODOs.
- **P2-3 `asyncio.to_thread` wrap of sync `Apprise.notify`** — must
  land before the ARQ worker schedule goes live in Batch 3. Accepted
  because the scheduler isn't wired, so nothing currently calls the
  sync path.
- **P2-4 migration-0002 column-mirror test** — low-risk Batch 3
  polish. Accepted.
- **P2-5 per-user `instant_threshold`** — blocked on Batch 3
  `user_profiles` table. Accepted.
- **P3-1 dead `idempotency_key()` helper** — kept in tree as
  the Batch-3 Redis-pre-dedup landing point. Accepted with a note.
- **P3-3 thin `core/tenancy.py` module** — kept; a delegating
  `require_tenant` helper would be ceremony today. Accepted.
- **P3-4 `resolve_session` last_seen slide error handling** — kept;
  failure requires SQLite lock contention which is already covered
  by the 5s busy timeout. Accepted.
- **P3-5 migration runner stem-on-exception log** — low priority.
  Accepted.

All eight deferrals are per-item-justified, not handwaved. This is
the right shape.

### Round 2 test deltas

| Metric | Round 1 (d877bd6) | Round 2 (d124ed5) | Δ |
|---|---:|---:|---:|
| Passing | 497 | 498 | +1 (new `test_test_send_rejects_cross_user_channel_id`) |
| Failing | 24 (4 buckets) | 24 (same buckets) | 0 |
| Skipped | 3 | 3 | 0 |
| Run time | 192.11s | 186.29s | −5.82s |

Failing bucket composition reconfirmed unchanged: 6 `test_api.py`
sqlite, 3 `test_cron.py`, 5 `test_setup.py`, 7 `test_sources.py`, 3
`test_time_buckets.py`. No round-1 test started failing; no round-2
test failed.

### Round 2 methodology

Fetched `origin/pillar3/batch-2` at `d124ed5`, inspected the four
review-response commits individually via `git show --stat`, read the
full diff of each. Ran `grep -n "SESSION_SECRET\|CHANNEL_
ENCRYPTION_KEY\|_DEFAULT_SECRET\|mIaARLi5" backend/src/` to confirm
no fallback constant remains in source — only the RuntimeError
messages reference the env var names (acceptable). Ran the full
`python -m pytest tests/ --ignore=tests/test_main.py -q` from
`backend/` and verified the claimed 498/24/3 totals. Confirmed that
`test_test_send_rejects_cross_user_channel_id` appears in the
dispatcher test file and that its assertions (`result.ok is False`,
`'not found' in result.error`, `MockApp.return_value.notify.
call_count == 0`) are the exact shape that would catch the P2-2
regression.

No `coderabbit:code-review` second opinion available (CLI absent in
worktree harness); manual verification only, same constraint as
rounds 1–3 of Batch 1.

---

**Signal (round 2):**

REVIEW_COMPLETE pillar3/batch-2 verdict=APPROVED

---

## Round 3 confirmation — 2026-04-18 — commit d124ed5

Re-fetched `origin/pillar3/batch-2`. Generator HEAD unchanged since
round 2 (`d124ed5`). No new commits. Re-ran the spot-check greps the
round-2 audit used to verify each P1 resolution:

- **P1-3 fallback constants gone:** `grep -rn "_DEFAULT_SECRET\|
  mIaARLi5\|dev-insecure-" backend/src/` → **0 hits**. The committed
  default session secret and Fernet key are fully excised from
  source. `RuntimeError` fail-closed paths present at
  `auth_deps.py:38` and `crypto.py:29` — confirmed.
- **P1-2 per-user scorer wired:** `grep -n "scorer_fn\|default_scorer\|
  JobScorer" backend/src/workers/tasks.py` → 9 hits across
  import, scorer-callable pickup, default-scorer construction, and
  both invocation branches (lines 22, 96, 100-103, 108-112).
  Confirmed.
- **P1-1 repo-layer deferral marked:** `grep -n "TODO(batch-2.1)"
  backend/src/repositories/database.py` → **2 hits** at `:277`
  (`user_actions` header) and `:321` (`applications` header). Both
  spell out the silent-overwrite hazard. Confirmed.
- **P2-2 cross-user rejection test:** `grep -n "test_test_send_
  rejects_cross_user\|mallory" backend/tests/test_channels_
  dispatcher.py` → 3 hits. The mallory fixture + the
  `user_id="mallory"` cross-user dispatch attempt are both present.
  Confirmed.

CLAUDE.md rule spot-checks unchanged from round 2:

- `grep -c "def normalized_key" backend/src/models.py` → 1 (rule #1
  intact).
- Source-count assertion in `tests/test_cli.py` not modified
  (rule #8 intact).

No new code from the generator since round 2. Round-3 verdict is
unchanged: APPROVED.

**Signal (round 3):**

REVIEW_COMPLETE pillar3/batch-2 verdict=APPROVED
