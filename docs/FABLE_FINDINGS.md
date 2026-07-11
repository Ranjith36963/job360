# Job360 — Production Codebase Findings

**Audited:** `origin/main` @ `d92136f` (the live Railway deployment base) · **Date:** 2026-07-10
**Method:** Manager (Opus 4.8) orchestrating a worker fleet — **Sonnet** for broad mechanical sweeps (frontend, tests/docs), **Opus** for high-judgment domains (security, data-integrity), plus a dedicated ops sweep. The manager verified every load-bearing claim against real code before recording it, de-duplicated overlaps, and adjudicated severity. Findings that a single skeptic could refute were dropped.

> **Scope note — "production" = `main`.** Railway deploys from `main`. The stacked PRs **#27→#31** (CI fix, prod email, request-timeout middleware, healthcheck fixes, extraction rescue) are **merged-worthy but NOT yet in production**, so a few items below are already fixed *pending merge* — each is tagged **[FIXED-PENDING-MERGE]**. Everything else is true of production right now.

## Severity summary

| Sev | Count | Headline |
|-----|------:|----------|
| 🔴 CRITICAL | 2 | One shared DB connection for all requests → corruption/errors under any concurrency |
| 🟠 HIGH | 11 | Multi-dim scoring silently off in the ingest path; migration half-apply; PII to Sentry; cookie `Secure` gap; migrations in the serving process; no security scanning; 4 frontend UX/leak bugs |
| 🟡 MEDIUM | 18 | SSRF via webhooks; enumeration; in-memory rate limits; worker has no timeouts/health; purge deletes live jobs; NULL-comparison ghost bug; non-atomic writes; … |
| 🟢 LOW | 8 | GDPR-deleted account resurrection; future-date recency; CORS width; env-example gaps; … |

## 🎯 Fix these first (ranked by risk × ease)

1. **C1 — pool the database connection.** Everything else is noise next to this; the app is one traffic spike from constant `"another operation in progress"` errors. Small, well-bounded fix.
2. **H1 — set `job.id` in the worker ingest path.** One line; restores multi-dimensional scoring that is currently silently disabled for every job the pipeline writes.
3. **H4 — `Sentry send_default_pii=False` + scrub cookies/passwords.** One line + a `before_send`; stops shipping session cookies and plaintext passwords to a third party.
4. **H5 — gate the session cookie `Secure` flag on the same prod check as everything else.** One line; closes a real session-capture window.
5. **H2 / H6 — make migrations atomic and move them out of the request-serving lifespan.** Prevents silent schema drift and "one bad migration = full outage on every replica."
6. **H7 — add `pip-audit` + `npm audit` + `gitleaks` + `bandit` to CI.** Cheap, catches the next class of problem automatically (the feedback-loop principle).

---

# 🔴 CRITICAL

### C1 — A single shared psycopg async connection serves every concurrent request
- **File:** `backend/src/api/dependencies.py:8-22` (`_db` global singleton) + `backend/src/repositories/database.py:19,25` (one `self._conn`, opened once).
- **Evidence (verified by manager):** `_db` is a module-global `JobDatabase` created once in `init_db()` and returned to *every* request by `get_db()`. `JobDatabase` opens exactly one connection (`self._conn = await aiosqlite.connect(...)` → psycopg via the shim) and all ~40 methods run on it. psycopg3 async connections raise `ProgrammingError: another operation is in progress` when two coroutines use them at once.
- **Impact:** Under any real concurrency (two overlapping requests), queries error out or read each other's cursors. It has *not* bitten yet only because live traffic is tiny (≈7–11 users); the first modest spike turns it into constant 500s and possibly cross-user data bleed.
- **Fix:** Use `psycopg_pool.AsyncConnectionPool`; acquire a connection per request/operation (`async with pool.connection() as conn:`), never share one `AsyncConnection` across tasks. Wire pool open/close into the FastAPI lifespan. (This also fixes C2, M13, and the `lastval()` races.)

### C2 — LLM-judge `match_batch` runs concurrent DB ops on that same shared connection
- **File:** `backend/src/services/llm_matcher.py:181-241` (`match_batch`, `asyncio.gather` + `Semaphore(3)`).
- **Evidence:** Each of the 3 concurrent `_one()` coroutines calls `has_verdict`/`load_enrichment`/`save_verdict` on the shared `_db._conn`.
- **Impact:** The judge stage collides on the connection under exactly the concurrency it was designed for → intermittent "operation in progress" and dropped verdicts during rescore.
- **Fix:** Same as C1 — each `_one()` takes its own pooled connection. Once C1 lands with a pool, this resolves.

---

# 🟠 HIGH

### H1 — Worker ingest never sets `job.id`, so multi-dimensional scoring is silently disabled
- **File:** `backend/src/workers/tasks.py:113-122,151` (`score_and_ingest`).
- **Evidence:** The reconstructed `Job` never gets its DB `id` set, but `enrichment_lookup` is keyed on `getattr(job,"id",None)` → always `None` → the salary/seniority/visa/workplace dims always score 0. `rescore.py:63` sets `job.id`; the primary ingest path does not.
- **Impact:** Every feed row the ARQ worker writes is scored keyword-only; the whole Engine-2 dimension system is off for normal ingest. (This is the recurring "id-at-scoring-time" bug, still live in the worker path.)
- **Fix:** After building the `Job`, `job.id = job_row["id"]` before scoring.

### H2 — Migrations can half-apply and then be marked "done" (silent schema drift)
- **File:** `backend/migrations/runner.py:121-148,211-218` under autocommit (`pg.py:321`).
- **Evidence:** `up.sql` statements run in autocommit. If a later statement fails, earlier ones are already committed but the stem isn't recorded; on reboot a leading `CREATE TABLE` raises `DuplicateTable`, which `up()` catches and then records the stem as applied — skipping the remaining `INSERT…SELECT`/`DROP`/`RENAME`.
- **Impact:** A migration like `0002` (new table + data copy) can leave the new table created but the data move never run, then be marked applied forever → permanent, invisible schema/data drift.
- **Fix:** Wrap each migration body in one explicit transaction (`async with conn.transaction():`, not autocommit); record the stem only after the whole body commits; never treat `DuplicateTable` as "applied".

### H3 — A missing/renamed column is swallowed and shown to the user as "no data"
- **File:** `backend/src/repositories/pg.py:81-86,406-407` + many readers in `database.py` doing `except OperationalError: return []`.
- **Evidence:** `UndefinedColumn` is remapped to `OperationalError`, which dozens of readers treat as "legacy missing table → return empty".
- **Impact:** A schema drift or a typo'd `SELECT` column makes `get_user_feed_jobs` / notification readers silently return empty → the dashboard shows zero jobs with no error logged, indistinguishable from "genuinely no data".
- **Fix:** Narrow the graceful-degrade to `UndefinedTable`/`InvalidSchemaName`; let `UndefinedColumn` propagate (or log loudly).

### H4 — Sentry `send_default_pii=True` ships session cookies and plaintext passwords to a third party
- **File:** `backend/src/api/main.py:61-65`.
- **Evidence:** With the FastAPI integration and `send_default_pii=True`, Sentry captures request headers (incl. `Cookie: job360_session=…`), client IP, user email, and can attach bodies — so a 500 during `/login` or `/register` can forward a live session cookie and the plaintext password.
- **Impact:** Session hijack surface + a GDPR problem (CVs, emails, credentials leaving the box). *(Note: the live-smoke merge already gates Sentry init to prod-only, but `send_default_pii` is a separate setting and still on.)*
- **Fix:** `send_default_pii=False`; add a `before_send` that strips `Cookie`/`Authorization` headers and any `password` field.

### H5 — Session-cookie `Secure` flag is gated on a *different* env var than the rest of prod detection
- **File:** `backend/src/api/routes/auth.py:109` (`_set_session_cookie`).
- **Evidence:** `secure = os.environ.get("JOB360_ENV") == "prod"`, but HSTS/env-validation/Sentry all key off `APP_ENV=production` **or** `RAILWAY_ENVIRONMENT`. A deploy that sets `SESSION_SECRET` but not the separate `JOB360_ENV=prod` passes startup, emits HSTS, yet issues the 30-day auth cookie **without `Secure`**.
- **Impact:** The auth cookie can ride a plain-HTTP request (first pre-HSTS visit, a stray `http://` asset, a forced downgrade) → network capture of a valid session.
- **Fix:** `secure = _is_production()` (the shared helper); default to `True` unless an explicit dev flag is set.

### H6 — Migrations auto-apply *inside* the request-serving process on every boot
- **File:** `backend/src/api/dependencies.py:19-21` called from the lifespan at `backend/src/api/main.py:81`.
- **Evidence:** Every deploy/restart runs `runner.up()` during FastAPI startup; a failure raises out of `lifespan` → the app never becomes ready.
- **Impact:** One bad migration = full outage, and on Railway rolling deploys it bricks *every* new replica. No separate release/migration gate.
- **Fix:** Run migrations as an explicit pre-deploy/release step (Railway "deploy command" or a one-shot job); keep app boot fail-safe. Compounded by H11 (Postgres migration SQL is untested in CI).

### H7 — No security scanning anywhere in CI; type-check is non-blocking
- **File:** `.github/workflows/ci.yml`, `ci-offline.yml`.
- **Evidence:** CI runs only ruff/pytest/tsc/eslint/next-build. No `pip-audit`, `npm audit`, `bandit`, `gitleaks`, or CodeQL. `ci.yml` mypy is `continue-on-error: true` (395 errors never block).
- **Impact:** Vulnerable dependencies and accidentally-committed secrets ship undetected.
- **Fix:** Add blocking `pip-audit` + `npm audit --audit-level=high` + `gitleaks` + `bandit` jobs. (Exactly the "turn failures into an automatic gate" principle.)

### H8 — Kanban drag listeners swallow clicks on the card's own buttons
- **File:** `frontend/src/components/pipeline/KanbanBoard.tsx:181-204,413-418`.
- **Evidence:** `{...listeners}` (raw `onPointerDown`) is spread on the whole card which contains nested `<button>`s (Notes/History/CV/Advance), and the `PointerSensor` has **no** `activationConstraint`, so dnd-kit installs a capture-phase click blocker on every pointerdown.
- **Impact:** Users clicking those buttons on a pipeline card get the click silently eaten — buttons feel dead.
- **Fix:** `useSensor(PointerSensor, { activationConstraint: { distance: 8 } })`, or move the drag listeners to a dedicated handle.

### H9 — Dashboard queries ignore errors and render failures as "No jobs found"
- **File:** `frontend/src/app/dashboard/page.tsx:106-160`.
- **Evidence:** The three `useQuery` calls never read `isError`/`error`; only `isFetching`. A 401/500/network error falls through to `total===0` → the normal empty state.
- **Impact:** On the app's primary page, real failures look identical to "no results", with no retry.
- **Fix:** Destructure `isError`/`error` and render an error banner (as `pipeline/page.tsx` already does).

### H10 — Every background refetch blanks the job list with skeletons
- **File:** `frontend/src/components/jobs/JobList.tsx:51-62` + `dashboard/page.tsx:108,447`.
- **Evidence:** `JobList` gets `loading={isFetching}` and unconditionally shows the skeleton grid whenever fetching — ignoring `placeholderData:(prev)=>prev` which kept real data.
- **Impact:** Every 30s `staleTime` expiry / filter change flashes the whole list to skeletons and resets scroll.
- **Fix:** Gate the skeleton on `isLoading` (no data yet); use a subtle "refreshing" indicator for `isFetching` when data exists.

### H11 — Four auth pages leak raw technical error strings to users
- **File:** `frontend/src/app/(auth)/reset-password/page.tsx:66-70`, `verify-email/page.tsx:43`, `forgot-password/page.tsx:50`, `auth/magic/page.tsx:42-45`.
- **Evidence:** They render `err.message` (`"API error 400: invalid or expired token"`) directly instead of `friendlyAuthError()` which login/register already use.
- **Impact:** Ugly, leaky UX on password-reset / verification / magic-link flows.
- **Fix:** Swap `err.message` → `friendlyAuthError(err, fallback)` in all four.

---

# 🟡 MEDIUM

### M1 — Authenticated SSRF via webhook channel
- **File:** `backend/src/api/routes/channels.py:126-140` + `services/channels/dispatcher.py:287-349`.
- **Impact:** A logged-in user can register a `webhook` pointed at `http://169.254.169.254/…` (cloud metadata), `http://127.0.0.1:*`, or Railway-internal hosts; `POST /channels/{id}/test` makes the server request it from inside the private network (blind, but enough to probe).
- **Fix:** Resolve the host and reject private/loopback/link-local/reserved ranges (re-check after DNS); block non-standard ports.

### M2 — Registration email enumeration + login timing oracle
- **File:** `backend/src/api/routes/auth.py:136-140,191`.
- **Impact:** `/register` returns `409` for existing emails (a direct "who has an account" oracle for a UK job-seeker product — GDPR-sensitive), while magic-link/reset carefully avoid this. Login skips argon2 for unknown emails → timing oracle.
- **Fix:** Return a generic "check your email" for register regardless of existence (send an out-of-band "you already have an account" mail); run a dummy `verify_password` on the login miss path.

### M3 — Brute-force/abuse limits are in-process memory; register has none
- **File:** `backend/src/services/auth/rate_limit.py` + `routes/auth.py:120-134`.
- **Impact:** Login-lockout/magic-link/reset limiters live in a per-process dict → reset on every Railway redeploy and **not shared across replicas**, defeating lockout under scaling/restart loops. `/register` has no limit (mass account creation). Lockout keyed only on email → targeted account-lockout DoS.
- **Fix:** Move limiters to Redis (the module already anticipates this); add an IP dimension; rate-limit `/register` per IP.

### M4 — Worker has no health check and no ARQ timeout/retry/DLQ policy
- **File:** `backend/Dockerfile.worker` (no `HEALTHCHECK`) + `backend/src/workers/settings.py:86-141`.
- **Impact:** If ARQ deadlocks or loses Redis, nothing detects it — scoring/notifications/ghost-sweep silently stop. No `job_timeout`/`max_tries`/DLQ: a slow `score_and_ingest` fan-out (ATS timeout 240s) can exceed the 300s default, get killed, then **retry up to 5×**, multiplying LLM spend; poison jobs loop then vanish.
- **Fix:** Set ARQ `health_check_interval` + a Railway restart trigger; set explicit `job_timeout` > worst-case fan-out, cap `max_tries`, add a dead-letter path.

### M5 — `purge_old_jobs` deletes still-live jobs and orphans per-user rows
- **File:** `backend/src/repositories/database.py:547-556` (rule #3).
- **Impact:** Deletes on `first_seen` (fixed at first discovery), not `last_seen_at` — a posting re-seen for months is still purged at 30 days. Purged shared `jobs` rows still referenced by `user_feed`/`applications` (FKs stripped by the shim) orphan those rows → the user's pipeline loses the job it points at.
- **Fix:** Purge on `last_seen_at < cutoff`; guard rows referenced by `applications`. **Rule #3 says confirm before touching purge logic.**

### M6 — Ghost sweep misses every `NULL` staleness row (Postgres NULL semantics)
- **File:** `backend/src/workers/tasks.py:476-482`.
- **Impact:** `WHERE staleness_state != 'confirmed_expired'` — in Postgres `NULL != '…'` is `NULL` (filtered out), so every legacy row with `staleness_state IS NULL` is never ghost-detected → dead postings keep being served.
- **Fix:** `WHERE staleness_state IS NULL OR staleness_state != 'confirmed_expired'`.

### M7 — Non-atomic multi-statement writes under autocommit (data loss)
- **File:** `backend/src/repositories/pg.py:321,431-438`; `database.py:690-702,882-903`; `tasks.py:600-628`.
- **Impact:** `autocommit=True` makes `commit()`/`rollback()` no-ops. `upsert_tailored_doc` does DELETE-then-INSERT: if the INSERT fails (or the process dies between), the user's existing tailored document is permanently lost. `advance_application` and `mark_missed_for_source` likewise partially apply.
- **Fix:** Group these writes in an explicit transaction (`async with conn.transaction():`), especially delete-then-insert.

### M8 — Negative per-dimension scores are persisted unclamped
- **File:** `backend/src/services/scoring_dimensions.py:138-139` + `database.py:355-375` (rule #27).
- **Impact:** `seniority_score` can be negative and penalties are subtracted; only the composite `match_score` is clamped to [0,100]. The raw dim columns feeding the frontend radar chart can be negative → out-of-range UI, breaking rule #27 for anything reading dims directly.
- **Fix:** Clamp each stored dimension to [0,100] (or handle negatives consistently at read time).

### M9 — PII (client IP + user_id) written to on-disk rotating logs unredacted
- **File:** `backend/src/api/middleware.py:103-104` → `utils/logger.py:109-117`.
- **Impact:** Every request logs IP + user_id into `data/logs/*.jsonl` with no scrubbing — GDPR-relevant PII in container-local files.
- **Fix:** Drop or hash the IP; confirm/limit retention.

### M10 — Hardcoded DB password in "prod" compose + empty secret defaults outside prod
- **File:** `docker-compose.prod.yml:22,58`; `backend/src/core/settings.py:259-271`.
- **Impact:** `job360dev` is baked into the prod compose file. `SESSION_SECRET`/`CHANNEL_ENCRYPTION_KEY` default to `""` and are validated **only** when `APP_ENV=production`/`RAILWAY_ENVIRONMENT` is set — any other env boots with empty signing/encryption keys.
- **Fix:** Require secrets unconditionally (default-deny); remove the hardcoded DB password (use env).

### M11 — CI never exercises Postgres or the migration SQL
- **File:** `.github/workflows/ci.yml:44-48` (+ `ci-offline.yml`).
- **Impact:** A Postgres service is started but tests run on **SQLite via conftest** — the prod Postgres path and every `migrations/*.sql` are untested, so a PG-specific bug (see H2/H6) only appears at prod boot.
- **Fix:** Run at least a migration smoke test (and ideally the suite) against the Postgres service with `runner.up`.

### M12 — Uptime monitor pings liveness, so real outages stay green
- **File:** `.github/workflows/uptime.yml:16-19`.
- **Impact:** It hits `/api/livez` which returns 200 with no dependency checks; if Postgres/Redis is down the app is broken but the monitor is green.
- **Fix:** Point it at `/api/readyz` (which probes DB+Redis). (Note GitHub crons fire 10–30 min late → poor detection time; a real uptime service is better.)

### M13 — `lastrowid` via session-global `lastval()` returns wrong ids
- **File:** `backend/src/repositories/pg.py:409-417`.
- **Impact:** After an `INSERT OR IGNORE` (→ `ON CONFLICT DO NOTHING`) that inserted nothing, `lastval()` returns a stale id from earlier in the session; combined with the shared connection (C1), concurrent inserts read each other's `lastval`.
- **Fix:** Use `RETURNING id` read from the same cursor; drop the `lastval()` path.

### M14 — Frontend auth middleware fails **open** when the backend is unreachable
- **File:** `frontend/src/middleware.ts:41-52`.
- **Impact:** On a `fetch` failure to `/api/auth/me`, the catch returns `NextResponse.next()` → serves the protected page shell unauthenticated. Softened because client API calls still 401, but SSR/layout content bypasses the gate.
- **Fix:** Serve a neutral "service unavailable" page (or redirect to login) on that error path; log/alert.

### M15 — No security headers from the frontend (Next.js) layer
- **File:** `frontend/next.config.ts`.
- **Impact:** No CSP / `frame-ancestors` / `Referrer-Policy` / HSTS from Next — no clickjacking or injection backstop (the one `dangerouslySetInnerHTML` in `jobs/[id]/page.tsx` is manually escaped, but there's no CSP net).
- **Fix:** Add a `headers()` block with at least `frame-ancestors 'none'`, `nosniff`, and a CSP compatible with Sentry/PostHog.

### M16 — A 403 in the shared fetch wrapper force-navigates and discards unsaved input
- **File:** `frontend/src/lib/api.ts:98-110`.
- **Impact:** `request<T>()` does `window.location.href = "/verify-email"` on any 403 `email_not_verified` — a background refetch can yank the user off a half-filled form with no confirmation.
- **Fix:** Make the redirect a top-level AuthProvider decision, not a side effect in the low-level fetch client.

### M17 — Pipeline board renders off `counts` but data comes from a separate `applications` fetch
- **File:** `frontend/src/app/pipeline/page.tsx:136,278-299`.
- **Impact:** The two are fetched independently; if they diverge (or one errors silently), the board shows `total>0` with empty columns, or hides the board despite data.
- **Fix:** Gate `EmptyState`/`KanbanBoard` on `applications.length` (the data actually rendered), not the independent `counts`.

### M18 — XML entity-expansion (billion-laughs) DoS on ingested feeds
- **File:** `backend/src/sources/base.py:23` + all `sources/feeds/*.py` & `sources/ats/*.py` using `ET.fromstring(_sanitize_xml(...))`.
- **Impact:** Stdlib ET won't resolve *external* entities (no XXE), but a malicious/compromised upstream feed can ship an internal-entity bomb to exhaust worker memory/CPU.
- **Fix:** Parse with `defusedxml.ElementTree` (drop-in), or reject documents containing a `DOCTYPE`.

---

# 🟢 LOW

- **L1 — Magic-link consume resurrects GDPR-deleted accounts.** `backend/src/services/auth/magic_link.py:171-192` clears `deleted_at=NULL` on consume, silently reactivating an erased account. Also lazily creates accounts for any address that clicks a link. **Fix:** don't clear `deleted_at` on consume; require an explicit reactivation path.
- **L2 — Future-dated posts get maximum recency.** `backend/src/services/skill_matcher.py:283-307`: negative `days_old` passes `<=1` → full recency weight. **Fix:** `if days_old < 0: return 0`.
- **L3 — CORS `allow_credentials=True` with `allow_methods/headers=["*"]` and unvalidated env origins.** `backend/src/api/main.py:93-101`. Safe today; a mis-set `FRONTEND_ORIGIN` (e.g. `*`) would expose authed responses cross-origin. **Fix:** reject `*` origin when credentials are allowed; fail closed in prod.
- **L4 — Global `rowid`→`ctid` regex rewrite.** `backend/src/repositories/pg.py:248` rewrites the token everywhere including string literals/aliases — latent correctness trap. **Fix:** restrict or drop (no call-site needs SQLite rowid).
- **L5 — `.env.example` missing ops-critical vars.** `APP_ENV`, `SENTRY_DSN`, `DATABASE_URL` absent — the exact vars gating prod validation/HSTS/error-tracking. **[Partially FIXED-PENDING-MERGE in PR #30.]** **Fix:** add them with commented prod values.
- **L6 — Frontend image ships full `node_modules` instead of Next standalone.** `frontend/Dockerfile:41-47`. Bigger image + attack surface. **Fix:** `output:"standalone"` in `next.config.ts`; copy only the standalone bundle.
- **L7 — Profile page detects "no profile" by string-matching `"404"` in the error message.** `frontend/src/app/profile/page.tsx:91-99`. **Fix:** use the typed `ApiError.isNotFound`.
- **L8 — Optimistic like/skip update has no `cancelQueries` and doesn't patch the bucket-counts query.** `frontend/src/app/dashboard/page.tsx:197-225`. **Fix:** `cancelQueries` before the optimistic write; also patch/invalidate `allJobsKey`.

---

# ✅ Already fixed, pending merge (PRs #27→#31)

These were found by the sweep but are already corrected in open, gate-green PRs — merging the stack closes them in production:
- **Inbound request-timeout middleware** (ops #7) — added in PR #30 (`RequestTimeoutMiddleware`, 504).
- **Container healthcheck path** (`docker-compose.prod.yml` `/health`→`/api/livez`; Dockerfile `/api/health`→`/api/livez`) — PR #30.
- **`.env.example` `DATABASE_URL`/`SENTRY_DSN`/timeout vars** — PR #30 (partial; L5 tracks the remainder).
- **Doc drift (test count, backup status, email-live)** — PR #31 + memory.

---

# 🔎 Sources & pipeline reliability

_A recurring theme: the pipeline cannot tell **"this source found nothing today"** from **"this source's parser broke"** — the two look identical in logs, so silent failures persist indefinitely._

### S1 — [HIGH] Circuit breaker counts an empty result as a failure
- **File:** `backend/src/services/scheduler.py:175`.
- **Evidence:** `fetch_failed = isinstance(result, BaseException) or result is None or not result` — `not result` makes an empty list a "failure" fed to `breaker.record_failure()`.
- **Impact:** A legitimately quiet source (niche board, keyword mismatch) trips the breaker OPEN after 5 quiet cycles and stops being polled — while a genuinely broken scraper returning `[]` looks the same.
- **Fix:** Only exceptions/`None`/timeouts count as breaker failures; track zero-result runs separately and alert on N consecutive zeros vs. that source's historical baseline (reuse the completeness pattern already in `main.py`).

### S2 — [HIGH] Reed/Adzuna title fan-out always exceeds the fetch timeout → permanent failure
- **File:** `backend/src/sources/apis_keyed/reed.py:33-46`, `adzuna.py:30-45`.
- **Evidence:** Reed does `job_titles[:12] × 3 locations` (≥72s of limiter sleeps alone at concurrency 1 / 2s delay); **Adzuna iterates the entire unbounded `self.job_titles`** with no slice (siblings cap: jsearch `[:4]`, careerjet `[:6]`, jooble `[:8]`). Both get the generic 60s ceiling.
- **Impact:** Any real profile with a moderately long title list guarantees Reed/Adzuna exceed 60s, get cancelled every tick, and register as permanent failures (→ breaker OPEN via S1).
- **Fix:** Cap `self.job_titles` (e.g. `[:8]`) in Adzuna like its siblings; raise the timeout for these two names or cut Reed's 12×3 fan-out.

### S3 — [HIGH] `_is_uk_or_remote` substring match lets foreign jobs through
- **File:** `backend/src/sources/base.py:36-46`.
- **Evidence:** Membership is plain substring `in`: `"uk" in "ukraine"` and `"uk" in "milwaukee"` are both `True`, and `UK_TERMS` is checked before `FOREIGN_INDICATORS`.
- **Impact:** "Kyiv, Ukraine" / "Milwaukee, WI" jobs are classified UK/remote and pass the filter across ~15+ sources — polluting UK relevance everywhere.
- **Fix:** Word-boundary match: `re.search(rf'\b{re.escape(term)}\b', loc_lower)`.

### S4 — [MEDIUM] HTML scrapers die silently on any layout change
- **File:** `backend/src/sources/scrapers/{linkedin,bcs_jobs,aijobs_ai,climatebase}.py`.
- **Evidence:** Hand-rolled regexes on live HTML; any exception or zero-match returns `[]` and logs the same `"found 0 relevant jobs"` as a real quiet day.
- **Impact:** A class-name/DOM change sends the source permanently to 0 with no alert — can persist forever.
- **Fix:** A per-scraper structural health check (assert some anchor/count matched on a non-trivial page) that raises or logs `error`/emits a metric, distinct from a true zero-result day.

### S5 — [MEDIUM] JobSpy (Indeed/Glassdoor) leaks a thread on every timeout
- **File:** `backend/src/sources/other/indeed.py:38-47` + `scheduler.py:156-165`.
- **Evidence:** `scrape_jobs` runs in `asyncio.to_thread`; `wait_for` cancellation detaches from but does not stop the OS thread (the scheduler comment says so).
- **Impact:** Each timeout leaves a stray thread scraping in the background, holding connections/CPU — a slow leak under repeated timeouts.
- **Fix:** Give `scrape_jobs` its own internal timeout, or run it in a killable process pool.

### S6 — [MEDIUM] `_conditional_fetch` doesn't catch `JSONDecodeError`
- **File:** `backend/src/sources/base.py:241`.
- **Evidence:** Its except only catches `(aiohttp.ClientError, asyncio.TimeoutError)` — unlike `_request` (`:106-108`) — yet it calls `resp.json()`.
- **Impact:** Malformed JSON from a conditional-fetch source raises out of `fetch_jobs()` with no retry (latent; only the text sibling is used today, but the JSON path is unguarded for the next adopter).
- **Fix:** Add `json.JSONDecodeError` to the except tuple.

### S7 — [MEDIUM] `eightykhours` has no `DOMAINS`, so it fires for every user
- **File:** `backend/src/sources/scrapers/eightykhours.py:17-20`.
- **Evidence:** No `DOMAINS` override → inherits `{"general"}` (siblings use `{"climate"}`/`{"tech"}`).
- **Impact:** A nurse/professor search consumes its rate-limited slot and gets AI-safety/EA job noise injected.
- **Fix:** `DOMAINS = {"tech"}` (or a niche tag).

### S8/S9 — [LOW] Dead `glassdoor` rate-limit + auth failures logged at `debug`
- `RATE_LIMITS["glassdoor"]` is never read (`JobSpySource.name` is hardcoded `"indeed"`) — `backend/src/core/settings.py:202`. **Fix:** remove or give glassdoor its own tracked instance.
- `_NO_RETRY_STATUSES` (401/403/404/422) log at `debug` and return `None` — an expired API key looks identical to an empty result. `backend/src/sources/base.py:123-125`. **Fix:** log 401/403 at `warning`/`error` + an auth-failure counter.

---

# 🧪 Test-quality, documentation-drift & repo-hygiene

_Ground-truth counts (measured): **1716 tests** collected, **47 SOURCE_REGISTRY / 46 unique** source classes, **25 migration pairs** (0000–0024)._

### T1 — [HIGH] The production SQL-rewriter `translate()` has zero direct tests
- **File:** `backend/src/repositories/pg.py:229` (`translate`), `:270` (`split_statements`), `:290` (`schema_for_path`).
- **Evidence:** This is the real prod DB driver (~13 modules import it), a chain of 10+ regex substitutions (`AUTOINCREMENT`, `INSERT OR IGNORE`→`ON CONFLICT`, FK stripping, `?`→`%s`). A grep of all tests for `translate(`/`split_statements(` = **zero hits**; all 1716 tests exercise it only indirectly. (The simpler `runner._split_sql_statements()` got a regression test after a real bug; this harder one never did.)
- **Impact:** A regex regression (a `?` inside a string literal, a multi-line FK clause) could silently corrupt prod writes while the whole suite stays green — the exact false confidence you asked me to hunt.
- **Fix:** Add `tests/test_pg_translate.py` — table-driven input→output per regex branch + adversarial inputs.

### T2 — [HIGH] A fully-shipped feature (AI CV/cover-letter tailoring) is undocumented
- **File:** migrations `0023`/`0024`; `backend/src/services/tailoring/*`; `routes/tailor.py`; `frontend/src/components/tailor/*`; env `TAILOR_FREE_PER_MONTH`.
- **Evidence:** Full-stack feature landed 2026-07-04, but CLAUDE.md phase summaries stop at "Two-pass profile extraction (2026-06-17)" and STATUS.md predates it.
- **Impact:** A future session has zero awareness it exists → risk of duplicating or breaking it.
- **Fix:** Add a phase-summary entry to CLAUDE.md, link `docs/peruser_cv_coverletter.md`, update STATUS.md.

### T3 — [HIGH] `.env.example` + CLAUDE.md omit prod-required `DATABASE_URL` and now-primary `OPENAI_API_KEY`
- **File:** `backend/src/core/settings.py:268` (`_REQUIRED_PROD_VARS` includes `DATABASE_URL`) vs `.env.example`.
- **Evidence:** `.env.example` never mentions `DATABASE_URL` (or `OPENAI_API_KEY`/`OPENAI_MODEL`, now the PRIMARY LLM provider ahead of Gemini/Groq/Cerebras; or `SENTRY_DSN`, `APP_ENV`, login-lockout vars).
- **Impact:** A new environment from the example alone hits the prod `RuntimeError` from `validate_required_env()` with no clue, and won't know OpenAI is primary. **[Partially FIXED-PENDING-MERGE in PR #31/#30.]**
- **Fix:** Add all of them with comments; refresh the CLAUDE.md env table.

### T4 — [HIGH] No migration test runs against a populated table
- **File:** `backend/tests/test_migrations.py`.
- **Evidence:** Every migration test creates *empty* tables, runs `runner.up()`, asserts a column exists. None seeds a row first.
- **Impact:** A `NOT NULL`-without-default add, or an `ALTER` that only fails against existing data, passes the whole suite and breaks only on the non-empty prod DB (compounds H2/H6).
- **Fix:** One test that seeds rows before migrating and asserts old data survives + is correctly defaulted.

### T5 — [HIGH] Rule #28 is violated by a file the rule itself names as "being removed"
- **File:** `backend/src/core/skill_synonyms.py` (615-line hand-typed alias dict, imported by `keyword_generator.py` + `skill_matcher.py`).
- **Evidence:** Rule #28 lists this under "Offenders being removed," but it's still live and not shrinking.
- **Impact:** The doc claims an in-progress cleanup that hasn't happened; a session may assume it's handled.
- **Fix:** Either finish the removal (route via ESCO/LLM), or amend rule #28 to scope this file out with a stated reason (it's scoring, not extraction — if that's the real distinction, say so).

### T6–T9 — [MEDIUM] Doc drift on `main` + weak assertions
- **CLAUDE.md test count stale** ("~1,409" vs 1716) and **describes backend as "async SQLite"** (it's Postgres via `pg.py` now) and **migration range "0000→0021"** (actual 0000–0024). **[FIXED-PENDING-MERGE in PR #31.]** **Fix:** merge #31 or update on main.
- **`pytest.raises(Exception)` too broad** on `test_database.py:311` + `test_tenancy_isolation.py:155` — if the *first* insert fails the UNIQUE test still passes. **Fix:** narrow to `aiosqlite.IntegrityError`, move first insert outside the block.
- **Profile-version diff test** (`test_discovery.py:258`) asserts shape (`"changed_fields" in body`) not that a change was detected — a diff engine returning `[]` passes (rule #21 anti-pattern; also `test_profile_versions_endpoint.py:76`). **Fix:** assert the changed field is actually present.

### T10–T12 — [LOW] Hygiene
- **Dead code:** legacy `score_job()` (`skill_matcher.py:402`) has no prod callers but CLAUDE.md calls it one of "two paths." **Fix:** delete + tests, or mark test-only.
- **`.gitignore` gap:** `test-artifacts/*.png` misses subdirs (`design/`, `tailor/`, ~7.5 MB untracked) — a `git add -A` would sweep them in. **Fix:** `test-artifacts/**/*.png`.
- **No real concurrent-write integration test** — `test_db_retry.py` mocks the lock; nothing fires two real writers at one row (directly relevant to C1). **Fix:** backlog an `asyncio.gather` two-writer test.

### Confirmed clean (checked)
No committed `.env` or secrets (git-grep for key patterns = zero); real CV/`User_info/` gitignored; `.gitignore` core coverage solid; no mock-the-thing-you-test violations in the core suites (`test_deduplicator`/`test_ghost_detection`/`test_llm_matcher` mock only true external boundaries and assert real values); only one legit optional-dep skip; no orphaned modules; single `pyproject.toml`.

---

# 🔬 Independent second-opinion fleet + adversarial verification

_Second-opinion fleet on the routes/worker/enrichment paths surfaced these NEW issues not caught by the first sweep. (Service-internals sweep + adversarial verifier still finishing.)_

### N1 — [HIGH] Daily digest silently never fires unless the send-minute is a multiple of 5
- **File:** `backend/src/workers/tasks.py:679-687` (`_bundle_due` daily branch) vs cron `settings.py:120` (`notification_tick` runs at minutes 0,5,10,…).
- **Evidence:** The tick only executes at 5-minute marks, but the daily branch requires an EXACT `now_local.hour==h and now_local.minute==m`, while the UI accepts any `HH:MM` (e.g. `08:03`).
- **Impact:** Most users who pick a send-time whose minute isn't divisible by 5 **never get their daily digest**. A single missed tick (restart/outage) also skips that user for the whole day.
- **Fix:** Match a window, not an exact minute — round `m` down to the nearest 5 on save, or check `0 <= (now_local - target) < tick_interval`, with catch-up.

### N2 — [HIGH] Profile extraction runs ~8 sequential LLM calls synchronously inside the HTTP request
- **File:** `backend/src/api/routes/profile.py:320-328` → `services/profile/two_pass.py:158-265`.
- **Evidence:** Every profile-writing route `await`s `run_two_pass_extraction` (CV+LinkedIn+GitHub+about-me passes + 3 `llm_merge_duplicates` + `llm_suggest_adjacent_skills`) — up to 8 chained LLM round-trips, each with a 60s timeout × 2 retries × 4-provider fallback.
- **Impact:** One upload can block the response for tens of seconds to minutes → gateway timeouts (Railway/nginx 30–60s), hung tab, event-loop task tied up.
- **Fix:** Move it to an ARQ background job (the pattern `_maybe_trigger_rescore` already uses); return 202 + a job id; frontend polls.

### N3 — [HIGH] Job-detail page loads the ENTIRE `job_enrichment` table on every request
- **File:** `backend/src/api/routes/jobs.py:625` (`_build_enrichment_lookup(db._conn)`), which does `SELECT … FROM job_enrichment` with no WHERE/LIMIT.
- **Evidence:** Runs on every authenticated `GET /jobs/{id}` when enrichment is on (it is, in prod), deserializing every catalog row — when only one job's enrichment is needed. A single-row `load_enrichment(conn, job_id)` already exists and is unused here.
- **Impact:** Full-table scan on a page users hit constantly, worsening daily as the 30-day catalog grows.
- **Fix:** Use `load_enrichment(db._conn, row["id"])`; wrap the single value for `JobScorer`.

### N4 — [MEDIUM] `/jobs` `limit`/`offset`/`hours` unbounded/unvalidated
- **File:** `backend/src/api/routes/jobs.py:437,444-445`.
- **Evidence:** `limit=Query(100)`, `offset=Query(0)`, `hours=Query(None)` — no `ge`/`le` (siblings `runs.py`/`notifications.py` do bound). Negative `offset`/`limit` silently slices from the tail; negative `hours` makes a future cutoff.
- **Impact:** Oversized responses / silently-wrong pages / accepted nonsense input.
- **Fix:** `Query(100, ge=1, le=500)`, `Query(0, ge=0)`, `hours ge=0`.

### N5 — [MEDIUM] Two spots leak raw exception text to clients
- **File:** `backend/src/api/routes/tailor.py:158-159` (`detail=f"Generation failed: {exc}"`) and `search.py:82` (`progress=str(e)`, returned by the status endpoint).
- **Impact:** Internal error detail (LLM provider names, response fragments, DB internals) exposed in API responses.
- **Fix:** `logger.exception` server-side; return a generic message.

### N6 — [MEDIUM] `search.py` `_runs` in-memory store grows unbounded
- **File:** `backend/src/api/routes/search.py:28,81-86`.
- **Evidence:** Module-level `_runs: dict` with no eviction/TTL — every `POST /search` adds an entry never removed (also holds the leaked exception text from N5).
- **Impact:** Slow unbounded memory growth over process lifetime.
- **Fix:** LRU/TTL eviction on completed/failed entries.

### N7 — [MEDIUM] Redundant/N+1 DB round-trips in the hot paths
- **`main.py:683-689` then `750-764`** re-issues the identical `SELECT id FROM jobs …` per job in the feed-write loop after `job.id` was already resolved above — doubles point-queries per run. **Fix:** reuse `job.id`.
- **`tasks.py:721-729`** (`notification_tick`) does one `SELECT timezone FROM users` per rule instead of a join; **`tasks.py:553-560`** (`send_bundle`) fetches job details one-by-one instead of `WHERE id IN (…)`. Runs every 5 min forever. **Fix:** batch both.

### N8 — [LOW] `_safe_fetch` swallows `asyncio.CancelledError`
- **File:** `backend/src/services/scheduler.py:152-168` — `except BaseException` catches cancellation and records it as a breaker "failure". **Fix:** `except asyncio.CancelledError: raise` first.

### N9 — [LOW] `GET /profile/versions` `limit` unbounded
- **File:** `backend/src/api/routes/profile.py:441` — `limit: int = 20` with no bound. **Fix:** `Query(20, ge=1, le=100)`.

_(The routes/worker sweep independently confirmed several first-fleet findings — e.g. the dead `score_and_ingest` fan-out ties to H1, and it verified enrichment `enrich_batch` handles per-job exceptions correctly. Clean per this sweep: `job_enrichment_schema.py`, `auth.py`, `pipeline.py`/`actions.py`/`notification_rules.py`/`runs.py` pagination + scoping.)_

---

_Still pending: the service-internals fresh sweep + the Opus adversarial verifier's line-by-line verdicts on all findings. Their results append here._

---

# What was checked and found solid

- **No IDOR** across the per-user routes — every one gates on `require_user`/`require_verified_user` and scopes by `user.id` (double-checked for OAuth state + channel ownership).
- **No raw-SQL injection of user input** — parameterized `%s` throughout; the few f-string SQL sites interpolate only allowlist-validated column names.
- **Auth primitives are sound** — 128-bit uuid4 session id, itsdangerous HMAC verified before DB lookup (no fixation), 256-bit `secrets.token_urlsafe` tokens SHA-256-hashed at rest with sane expiries, argon2id passwords, fail-closed `SESSION_SECRET`/Fernet keys, CV upload bounded read + extension allowlist.
- **The new backup workflow** (`db-backup.yml`) is genuinely strong — restore-verify gate + encryption-before-upload + retention.
- **livez/readyz logic** and the migration runner's `pg_advisory_lock` concurrency guard are correct in isolation.
