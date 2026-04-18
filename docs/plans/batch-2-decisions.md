# Batch 2 — Design Decisions

> Output of the `superpowers:brainstorming` skill. Every architectural choice in Batch 2 that is irreversible or expensive to reverse is recorded here with 2–3 options, pros/cons, and a recommendation + reason. Subsequent plan + implementation cite this doc by section.

**Brief reference:** `docs/research/pillar_3_batch_2.md` (authoritative blueprint)
**Branch:** `pillar3/batch-2`
**Base commit:** `main` @ `31124fa` (Batch 1 merged)
**Date:** 2026-04-18

---

## D1. Task queue — ARQ vs Celery vs RQ

| Option | Memory | Async native | Retries/cron built in | Broker | Monitoring |
|---|---|---|---|---|---|
| **ARQ** | ~30–60 MB | **Yes** (asyncio) | Yes | Redis | None (log-scrape) |
| **Celery** | ~100–200 MB/worker | No (sync primary; `asyncio` via `anyio` wrapper) | Yes, richest | RabbitMQ or Redis | Flower |
| **RQ** | ~50 MB | No | Basic | Redis | rq-dashboard |

**Recommendation: ARQ.**
**Reason:**
1. Job360 is 100% asyncio end-to-end (`aiohttp` + `aiosqlite` + FastAPI). Celery's sync model would force a shim layer around every DB/HTTP call. ARQ sits in the same event loop as FastAPI — we can share one Redis client + one DB pool.
2. Batch 2 ships ≤10K users (per blueprint §2). ARQ's known gaps (no built-in `group()`/`chord()` fan-out, no rate-limit decorator) are tolerable at this scale — both are <10 lines of application code. Blueprint §2 explicitly says "migrate to Celery at ~30K users".
3. RQ benchmarked slowest (51s for 20K jobs vs ARQ's 35s per blueprint §2). It offers no compensating advantage.
4. ARQ is maintained by Samuel Colvin (Pydantic author) — compatible with the project's dependency philosophy (light, typed, async).

**Risks accepted:** ARQ's GUI-monitoring gap. Mitigated by exposing `notification_ledger` status columns via a small FastAPI admin endpoint (cheaper than standing up Flower).

---

## D2. Notification routing — Apprise vs Novu self-hosted vs per-channel SDK

| Option | Channels | Infra cost | License | Queuing built in |
|---|---|---|---|---|
| **Apprise** | 100+ (Slack, SMTP, Telegram, Discord, webhook) | $0 | MIT | **No** (send-only) |
| **Novu self-hosted** | ~15 | $5–10/mo (Mongo+Redis+S3) | Apache 2.0 | Yes |
| **Per-channel SDK** (slack_sdk, smtplib, etc.) | As built | $0 | Mixed | No |

**Recommendation: Apprise + ARQ (wrap Apprise in ARQ jobs for retry/DLQ).**
**Reason:**
1. Apprise's URL-based configuration (`tgram://bot/chat`, `slack://t1/t2/t3`) aligns naturally with storing a JSONB list per user — matches the `user_notification_preferences.channels` shape in the blueprint §1.
2. Novu's self-hosted stack (Mongo + S3 + extra Redis instance) adds 3 infra dependencies for a feature set we only use ~20% of. The digest + quiet-hours + rate-limit logic Novu provides we already need to custom-build anyway (our tier policy differs from Novu's defaults).
3. Per-channel SDKs mean 4× as much code to write and maintain. Apprise unifies the send interface; channel quirks (Slack Block Kit vs Discord embeds vs Telegram MarkdownV2) are shaped by a small `format_payload(channel, job)` function **before** handing to Apprise.
4. The Apprise "no queue/retry" gap is exactly what ARQ fills. The two libraries compose cleanly — ARQ owns retry, DLQ, rate-limit, idempotency; Apprise owns wire-format + credentials.

**Risk accepted:** Apprise's per-channel rate limits are not first-class — we enforce them in ARQ worker settings (`max_tries`, `backoff_seconds`) rather than trusting Apprise to back off.

---

## D3. Real-time feed — polling vs SSE vs WebSockets

| Option | Server load @ 10K users | Client complexity | Firewall-friendly |
|---|---|---|---|
| **Polling (30s)** | ~333 req/s | Low | Yes |
| **SSE** | One persistent conn per user | Low | Yes |
| **WebSockets** | One persistent conn per user (bidir) | Medium | Sometimes blocked |

**Recommendation: Polling for MVP (30s), SSE-ready for V1.5 — WebSockets explicitly rejected.**
**Reason:**
1. Blueprint §3 is emphatic: "WebSockets are not recommended. Job360's dashboard is read-heavy." Confirmed by Figma LiveGraph quote: "most traffic is driven by initial reads, not live updates." We trust that read.
2. Polling at 30s × 10K users = 333 req/s — FastAPI with uvicorn workers handles this without horizontal scaling. Zero incremental infrastructure.
3. SSE wiring is straightforward (`EventSourceResponse` from `sse-starlette`) but adds a new failure mode (stale connections) that provides no UX win for jobs scrolling in a bucket view.
4. `GET /api/feed?since=<timestamp>` returns only new+changed rows — bandwidth is bounded, not all 200 rows each poll. We add an `updated_at` index on `user_feed` already in the schema.

**Migration path:** If Batch 3 demands sub-second latency (unlikely for a job board), add `GET /api/feed/stream` SSE endpoint alongside the polling endpoint; both read from the same `FeedService`. Frontend feature-flags the upgrade.

---

## D4. Persistence — SQLite now vs PostgreSQL now vs PostgreSQL later

| Option | Migration pain | Multi-tenant safe? | FastAPI async driver |
|---|---|---|---|
| **Stay on SQLite (WAL) for Batch 2** | 0 | Only via schema convention (tenant_id) | `aiosqlite` (current) |
| **Migrate to PostgreSQL NOW** | High — rewrite every query, re-test every source | Yes (native) + row-level security option | `asyncpg` |
| **Migrate in Batch 3** | Medium — schema already tenant-shaped | Yes | `asyncpg` |

**Recommendation: Stay on SQLite for Batch 2; plan migration to PostgreSQL as Batch 3 first step.**
**Reason:**
1. Blueprint §3 admits the `user_feed` table "~10M rows at 2GB... PostgreSQL handles trivially." It does not say SQLite cannot handle it. At ≤1K users × 200 top-N matches = 200K rows (16 MB), SQLite in WAL mode is not the bottleneck.
2. Migrating databases **and** bolting on auth + multi-tenant **and** ARQ **and** Apprise all in one batch is the textbook recipe for irreversible breakage. Batch 2 already ships 5 major subsystems. Adding a DB-engine swap triples review surface.
3. The hard work of migration — splitting every query to accept `tenant_id`, index strategy, FK cascade policy — is exactly what we **do** in Batch 2 on SQLite. When Batch 3 moves to Postgres, the SQL change surface is mechanical (ILIKE vs LIKE, `AUTOINCREMENT` → `SERIAL`, JSON1 → JSONB). Row shape and query shape are identical.
4. CLAUDE.md rule #3 forbids touching `purge_old_jobs` without explicit confirmation — a DB engine swap would force that today. Defer.

**Risks accepted:**
- SQLite's single-writer model means the ARQ worker and FastAPI app contend on the same write lock. Mitigation: WAL + 5s busy timeout (already in place, `database.py:21–22`) + write-batching in ARQ tasks.
- No native `UUID`/`JSONB`/`CASCADE ON DELETE` in SQLite 3.30. We use `TEXT` UUIDs via `uuid4().hex`, JSON stored as `TEXT` with app-side validation, and explicit `DELETE FROM child WHERE parent_id=X` calls rather than engine-level cascade.

---

## D5. Auth — session cookie vs JWT vs Supabase Auth

| Option | Self-hosted | Rotation / revocation | Frontend integration |
|---|---|---|---|
| **Session cookies (signed, HttpOnly)** | Yes | Trivial (delete row) | Native |
| **JWT (HS256)** | Yes | Hard without blacklist table | Header or cookie |
| **Supabase Auth** | No (hosted free tier) | Yes | Supabase SDK |

**Recommendation: Signed session cookies backed by a `sessions` table.**
**Reason:**
1. Revocation is first-class: logout = `DELETE FROM sessions WHERE id = ?`. JWT would require either short expiry (poor UX) or a blacklist table (which defeats JWT's "stateless" pitch). Job360 expects to implement "log out all devices" and admin-force-logout — both trivial with sessions.
2. Blueprint §4 lists Supabase Auth as an option, but Supabase is a managed service (external dependency, external PII egress, GDPR DPA paperwork). Contradicts the project's "zero-cost / self-hosted" ethos captured in memory's Project Vision.
3. Session cookies on a same-origin FastAPI + Next.js setup are the simplest possible path: FastAPI sets `Set-Cookie: session=<signed>; HttpOnly; Secure; SameSite=Lax` on login, every subsequent request carries it, middleware resolves `request.state.user_id`. No client-side token juggling, no `localStorage` XSS exposure.
4. Password hashing with `passlib[argon2]` (argon2id, OWASP-recommended). No home-grown scrypt.

**Implementation choice inside this decision:**
- Cookie value: `<session_id>.<hmac-sha256(session_id, SESSION_SECRET)>` (itsdangerous-style) — so we don't need a DB read just to detect cookie tampering.
- `sessions` table: `(id TEXT PK, user_id TEXT, created_at TIMESTAMP, expires_at TIMESTAMP, last_seen TIMESTAMP, user_agent TEXT NULL, ip_hash TEXT NULL)`. 30-day absolute expiry, sliding `last_seen` refresh.

**Risk accepted:** CSRF. Mitigation: `SameSite=Lax` covers non-mutating GETs; for POST/DELETE, require `X-CSRF-Token` header matching a second cookie (double-submit). `SameSite=Strict` is too hostile to link-click flows.

---

## D6. Multi-tenancy model — shared schema with `tenant_id` vs schema-per-tenant

**Recommendation: Shared schema with `tenant_id` column.**
**Reason:** Job360 is a consumer product, not a B2B SaaS. Expected shape is 1 tenant = 1 user (single-user memory reflects this). Schema-per-tenant is hostile to at-scale analytics ("count all jobs across all users"), and SQLite has no schema namespacing anyway. Shared-schema + `tenant_id` FK + index on `(tenant_id, created_at)` per table is the minimum structure that proves isolation via a dedicated test class.

**Implementation detail:** `tenant_id` and `user_id` are the SAME column initially — one user = one tenant. We keep the concept separated (two columns would be possible) so Batch 3+ can introduce shared-workspace tenancy without an ALTER TABLE storm.

Existing single-user data migrates as `tenant_id = user_id = '00000000-0000-0000-0000-000000000001'` (well-known local-admin UUID).

> **REVISION 2026-04-18** (post-review): implementation landed with a SINGLE `user_id` column, not two (no separate `tenant_id`). Reason: re-reading blueprint §3 ("jobs is a shared catalog, user_feed is per-user") clarified that `jobs` must NOT carry a tenant scope at all, so the only per-user tables are `user_actions`, `applications`, `user_feed`, `notification_ledger`, `user_channels` — all naturally scoped by `user_id`. Shared-workspace tenancy in Batch 4+ will come via a separate `tenants` join table (`user_tenants(user_id, tenant_id, role)`) rather than adding a second column to every per-user row. YAGNI-correct for the consumer product we're actually shipping.

---

## D7. Notification trigger policy — three-tier (blueprint §1) vs binary digest-only vs "always instant"

**Recommendation: Three-tier (immediate ≥80 / digest 30–79 / dashboard-only <30).**
**Reason:** Directly from blueprint §1 — this is the documented product differentiator ("no job search tool offers score-threshold notifications or tiered urgency"). No brainstorming tension here; ratified as-is.

---

## D8. Pre-filter order — blueprint §2 cascade vs inverted vs embedding-first

**Recommendation: Cascade per blueprint §2 — `location+work_arrangement → experience → skills overlap → score`.**
**Reason:** Blueprint justifies the order with compounding elimination rates (70% → 50% → 60–80% → 95–99%). Embeddings (FAISS / all-MiniLM-L6-v2) are Phase-3 per §2 "Phase 3 (10,000+ users)". Not this batch.

**Implementation:** Pre-filter runs at the query boundary inside `FeedService.ingest_job(tenant, job)` — the ARQ worker calls it once per new job. The existing `JobScorer` becomes step 4 of the cascade rather than step 1.

---

## D9. Migration tooling — alembic vs plain SQL files vs extending existing `_migrate()`

**Recommendation: Plain forward/reverse SQL files under `backend/migrations/` with a tiny Python runner that records applied migrations in a `_schema_migrations(id, applied_at)` table.**
**Reason:**
1. Alembic targets SQLAlchemy, which Job360 does not use (raw `aiosqlite` with f-string SQL). Dragging it in just for schema versioning is over-tooling.
2. The existing `_migrate()` in `database.py:75–97` does `PRAGMA table_info` diffs — it cannot drop columns, cannot reorder, cannot run data migrations. Batch 2 needs all three (move `user_profile.json` content into `users` + `user_preferences`, copy existing `jobs`/`applications` rows into `tenant_id=1`).
3. Plain SQL + a 40-line `run_migrations.py` is legible, reviewable, and satisfies the hard constraint "new tables under migrations/ with forward+reverse SQL".

**Layout:**
```
backend/migrations/
  0001_multi_tenant_auth.up.sql
  0001_multi_tenant_auth.down.sql
  0002_user_feed.up.sql
  0002_user_feed.down.sql
  0003_notification_ledger.up.sql
  0003_notification_ledger.down.sql
  runner.py          # python -m migrations.runner up|down|status
```

Each `.up.sql` is idempotent (`CREATE TABLE IF NOT EXISTS`, `ALTER TABLE ... ADD COLUMN` guarded by the runner's version check).

---

## D10. Channel credential encryption — Fernet vs libsodium vs plaintext-with-column-encryption-later

**Recommendation: Fernet (AES-128-CBC + HMAC-SHA256), key in env var `CHANNEL_ENCRYPTION_KEY`.**
**Reason:** Blueprint §1 spec'd it; `cryptography` is already a transitive dep (via `httpx`'s cert chain); zero new packages. Key rotation is a new column (`key_version`) we add pre-emptively so rotation in Batch 3 is additive.

**Scope note:** Only the `channels[*].credential` fields are encrypted (webhook URLs, bot tokens). Email addresses, channel names, preferences themselves remain plaintext since they are not secrets and are useful for admin/support queries.

---

## D11. Frontend data fetch — swr vs react-query vs keep native fetch

**Recommendation: Keep native `fetch` (CurrentStatus.md §8 — project has no client-side cache library).**
**Reason:** Adding SWR/TanStack Query is outside Batch 2's "channel config UI" scope. Polling every 30s via plain `setInterval` + `fetch` matches the existing pattern in `/dashboard` and `/search/:run_id/status`. Frontend churn minimized; revisit in Batch 3 if we move to SSE.

---

## D12. Idempotency key for notification sends

**Recommendation: `idempotency_key = sha1(f"{user_id}:{job_id}:{channel}:{trigger_bucket}")`, stored in `notification_ledger`, unique constraint `UNIQUE(user_id, job_id, channel)`.**

**Reason:** Matches blueprint §1 dedup ledger. Recomputation after an ARQ worker restart hits the unique constraint and silently no-ops — exactly the "channel-aware dedup: same job to all channels, never twice to the same channel" behaviour blueprint §1 demands. `trigger_bucket` is included in the hash but not in the uniqueness — if a job jumps from 24h to 24–48h, re-notification is blocked by the ledger, preserving the "no re-trigger on bucket transitions" rule.

---

## Out-of-scope (explicitly deferred)

- **PostgreSQL migration** → Batch 3 first step.
- **FAISS / embedding-based retrieval** → Batch 3 or later (blueprint §2 "Phase 3").
- **Novu / Courier / Knock integration** → never planned; paid tiers economically infeasible.
- **SMS channel** → blueprint §1 flagged as £0.04/msg → £1,200/mo @ 1K users. Premium-tier only, not Batch 2.
- **Supabase Auth** → D5 recommendation is self-hosted session; Supabase is an explicit non-goal.
- **Celery migration** → Batch 3 or later when user count ≥ 30K.

---

## Tie to CLAUDE.md hard constraints

- **Rule #1 (don't touch `normalized_key`)**: Upheld. Batch 2 adds `tenant_id` as a new column and includes it in the scope of dedup (i.e., two users can both have a row for `(normalized_company, normalized_title)` without constraint conflict). We drop the table-level `UNIQUE(normalized_company, normalized_title)` and replace with `UNIQUE(tenant_id, normalized_company, normalized_title)`. The function `normalized_key()` on `Job` is NOT modified — we only widen the uniqueness predicate in SQL.
- **Rule #2 (don't change `BaseJobSource`)**: Upheld. All Batch 2 changes are above the source layer.
- **Rule #3 (don't touch `purge_old_jobs` without confirmation)**: Upheld. We add `purge_stale_feed_rows()` alongside, leaving the existing purge intact.
- **Rule #4 (mock all HTTP)**: Tests for ARQ tasks, Apprise, and any notification path use `aioresponses` / monkeypatched Apprise `notify()`.
- **Rule #5 (run the suite)**: Baseline locked at top of `batch-2-plan.md`; regressions measured against it.
- **Rule #8 (source count)**: Untouched — Batch 2 does not add/remove sources.

---

*End of decisions doc. Proceed to `batch-2-plan.md`.*
