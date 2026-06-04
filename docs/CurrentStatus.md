# CurrentStatus.md — Job360 Codebase Honest Mirror

> ⚠️ **Snapshot from 2026-04-19.** This document was built by 8 parallel Explore subagents auditing the code-base at commit `13d4305` (Merge Batch 3.5.4). It has **not** been refreshed for the Step 0 → Step 1 → Step 1.5 → Step 2 → Step 3 work that has merged since (notably: migrations `0010`–`0014`, the engine→API multi-dim seam, the four new API routes — `auth` / `channels` / `notifications` / `notification_rules` / `runs` — and the Next.js 16 server/client split). For **current state**, see `docs/pillars/README.md` + the three pillar manuals + `glossary.md` + `runbook.md`, plus the project root `STATUS.md`. This file is preserved as a historical "honest-mirror" snapshot because the 8-agent audit methodology produced a different *kind* of evidence (file-anchored claims) than a maintained status doc.

**Method.** Audit built by 8 parallel Explore subagents reading code only. No trust in CLAUDE.md, memory, or prior audit. Every claim anchored `file:line`.
**Last full re-audit.** 2026-04-19 (commit `13d4305` — Merge Batch 3.5.4)
**Prior audit.** 2026-04-19 (commit `ac90bae`) — see `CurrentStatus_diff.md` for what moved since then. 4 stabilisation batches merged between the two audits (3.5.1, 3.5.2, 3.5.3, 3.5.4).

---

## §1 — Overview

Job360. UK-focused multi-domain job aggregator. Python 3.9+ FastAPI backend + Next.js 16 frontend. Multi-user delivery layer (Batch 2) + tiered polling (Batch 3) + IDOR/ARQ/scheduler stabilisation (Batch 3.5) + profile/search IDOR patch (3.5.1) + per-user profile storage (3.5.2) + conditional-cache pilot (3.5.3) + test cleanup (3.5.4) all shipped.

**Tree skeleton:**

| Path | Files | Notes |
|---|---|---|
| `backend/src/` | 122 .py | 5 root + 12 submodules (4 empty placeholders — see §15) |
| `backend/src/api/` | 14 | FastAPI routes + auth deps |
| `backend/src/api/routes/` | 8 | health, auth, actions, jobs, profile, pipeline, search, channels |
| `backend/src/services/` | 31 | Primary business logic (auth, channels, notifications, profile subdirs) |
| `backend/src/sources/` | 57 (49 source files + base + 6 dirs + init) | 50 entries in SOURCE_REGISTRY (indeed handles 2) |
| `backend/src/core/` | 5 | settings, keywords, companies, tenancy |
| `backend/src/repositories/` | 3 | database.py + csv_export |
| `backend/src/workers/` | 3 | tasks.py + settings.py + __init__ |
| `backend/src/utils/` | 4 | logger, rate_limiter, time_buckets |
| `backend/migrations/` | 14 SQL + runner.py | 7 migration pairs (0000–0006) |
| `backend/tests/` | 44 (43 test files + conftest) | 615 collected |
| `frontend/src/` | 39 | Next.js 16 App Router, 9 pages |
| `docs/` | 18 | plans + reviews + status |

**HEAD = `13d4305`** · Branch: `main` · Subject: _Merge Batch 3.5.4: Test cleanup (577p/25f/3s → 600p/0f/3s)_

**Recent 10 commits (newest first):**
- `13d4305` Merge Batch 3.5.4: Test cleanup
- `2458858` chore(pyproject): add pytest-randomly as dev dep + opt-out by default
- `82eaea6` test(cleanup): authenticated_async_context fixture + SearchConfig injection
- `24d64e8` test(cleanup): fix PROJECT_ROOT + drop dead requirements.txt tests
- `9e3a6cb` docs(pillar3): Batch 3.5.4 cleanup plan + investigation
- `…` Batch 3.5.3 (conditional-cache pilot — nhs_jobs_xml only)
- `…` Batch 3.5.2 (per-user profile storage via `user_profiles` table)
- `…` Batch 3.5.1 (profile + search IDOR closure)
- `ac90bae` Merge Batch 3.5: IDOR + ARQ + Scheduler wiring
- `fad1744` Merge Batch 3: Tiered Polling + Source Expansion

---

## §2 — Architecture (Scoring + Matching)

**Scoring components** (4-component model, 0–100 + penalties), `backend/src/services/skill_matcher.py`:

| Component | Weight | Anchor |
|---|---|---|
| Title match | 0–40 | `:18` (`TITLE_WEIGHT`) |
| Skill match | 0–40 | `:19` (`SKILL_WEIGHT`); `SKILL_CAP = SKILL_WEIGHT` |
| Location | 0–10 | `:20` |
| Recency | 0–10 | `:21` |
| Negative title penalty | −30 | `:219` |
| Foreign location penalty | −15 | `:231` |

Final clamp `[0,100]` at `:268`. `MIN_MATCH_SCORE = 30` at `core/settings.py:44`. Applied as filter at `main.py:445`.

**Recency priority** (`recency_score_for_job` `:195–212`):
1. `date_confidence == "fabricated"` → return 0
2. `posted_at` + trustworthy (high/medium/repost_backdated) → full band
3. `posted_at` + low confidence → fall back to `date_found` capped at 60%
4. No `posted_at` + has `date_found` → 60% band
5. Neither → 0

**Two scoring paths:**
- Module-level `score_job(job)` `:259–268` — hard-coded fallback when no profile
- `JobScorer(config).score(job)` `:322–331` — production path, uses `SearchConfig`

**Visa detection** `check_visa_flag` `:271` (module) + `:333` (instance) → `_has_visa_keyword()` `:94–99` honouring `_VISA_NEGATIONS` `:86–91`.

**Prefilter cascade** (`backend/src/services/prefilter.py`):
1. `location_ok` `:56–83` — ~70% eliminated (remote/hybrid/substring match)
2. `experience_ok` `:86–113` — ±1 seniority band
3. `skill_overlap_ok` `:116–123` — ≥1 skill intersection
- `passes_prefilter` `:126–131` (AND) — SQL-cheap gate before `JobScorer.score()`. Blueprint §2 99% elimination target.
- Hot-path invocation: `backend/src/workers/tasks.py:108` inside `score_and_ingest`.

**Dedup** (`backend/src/services/deduplicator.py`):
- `deduplicate()` `:49–62` groups by `(normalized_company, _normalize_title)`
- `_normalize_title` `:18–33` strips seniority prefixes + job codes + parentheticals — **intentionally wider than DB UNIQUE** (docstring `:21–27`)
- Tiebreaker `:60`: `(match_score DESC, completeness DESC)`
- DB UNIQUE at `database.py:49` matches `Job.normalized_key()` at `models.py:61–65` (company suffix + lowercase). Consistent by design.

**Pillar 2 ABSENT.** Grep evidence:
- `sentence_transformers` / `chromadb` / `pgvector` / `cosine_similarity` → 0 hits
- `cross.encoder` / `BM25` / `rerank` / `RRF` → 0 hits
- LLM enrichment of job posts → 0 hits (LLM is CV-only)

Pure keyword-matching until Pillar 2 commences. Scoring is **4-dimensional, not 7+**.

---

## §3 — Pipeline

**Flow:** `run_search()` (`main.py:279`) → load profile → build sources → **TieredScheduler** → ghost detection → score → dedup → DB writes → notifications.

**Scheduler wiring (HOT PATH — no longer `asyncio.gather`):**
- Imported `main.py:26`
- Instantiated + dispatched `main.py:363–364`:
  ```python
  scheduler = TieredScheduler(sources, registry)
  paired = await scheduler.tick(force=True)
  ```
- Breaker registry consulted before each tick (`scheduler.py:114–122`); OPEN sources skip dispatch; success/failure auto-recorded (`scheduler.py:140–144`).
- Post-dispatch breaker logging at `main.py:380–384`.

**TIER_INTERVALS_SECONDS** (`scheduler.py:36–47`): ats=60s · reed=300s · workday=900s · rss=900s · scrapers/keyed_api/free_json/other/unknown/default=3600s. NAME_TIER overrides: reed→reed, workday→workday (`:52–57`).

**Breaker registry adoption.** All 49 sources registered in `default_breaker_registry()` at `main.py:360`. State machine CLOSED → OPEN (after 5 fails) → HALF_OPEN (after 300s cooldown) → CLOSED (`circuit_breaker.py:29–72`).

**Conditional cache adoption: 1 source** (up from 0 at prior audit — Batch 3.5.3 pilot). Only `nhs_jobs_xml` calls `_get_json_conditional()`. Grep: `sources/feeds/nhs_jobs_xml.py` + `sources/base.py` (the helper). 48 other sources untouched. FIFO cache (256 entries) at `services/conditional_cache.py:29–77` with `hit_count`/`miss_count` instrumentation.

**Ghost detection completeness gate** `main.py:144–187`:
- Triggered post-scheduler `main.py:428` via `_ghost_detection_pass(db, sources, results, history)`
- 70% rolling-7d completeness threshold gates absence sweep (rate-limit safety). Source returning < 0.7× rolling avg → skip sweep.
- Otherwise: `update_last_seen` for observed + `mark_missed_for_source` for absent.

**Repost detection ABSENT.** No `sentence_transformers`, no `embedding`, no LLM scoring path. Dedup syntactic only (`models.py:61–65`).

---

## §4 — Data Model

**`Job` dataclass** (`backend/src/models.py:17–65`, 65 LOC):

Required: `title, company, apply_url, source, date_found`
Defaults:
- `location: str = ""`, `salary_min/max: Optional[float] = None`, `description: str = ""`
- `match_score: int = 0`, `visa_flag: bool = False`, `is_new: bool = True`, `experience_level: str = ""`
- **Batch 1 date-model fields:**
  - `posted_at: Optional[str] = None` — source-claimed posting date
  - `date_confidence: str = "low"` — enum: high/medium/low/fabricated/repost_backdated
  - `date_posted_raw: Optional[str] = None` — audit-only

`__post_init__` `:40–50`: HTML unescape, company sanitisation (rejects nan/None/n/a → "Unknown"), salary sanity (<10k → None, >500k non-GBP → None).
`normalized_key()` `:61–65` returns `(company_suffix_stripped, title_lowercased)` — the DB UNIQUE key.

**Profile dataclasses** (`backend/src/services/profile/models.py`, 117 LOC):
- `CVData` `:9–48` — raw_text + skills + job_titles + companies + education + certs + summary + experience_text + LinkedIn (positions, skills, industry) + GitHub (languages dict, topics, inferred_skills) + display fields (name, headline, location, achievements)
- `UserPreferences` `:52–64` — target titles, additional/excluded skills, locations, industries, salary min/max, work_arrangement, experience_level, negative_keywords, github_username, about_me
- `UserProfile` `:68–79` — cv_data + preferences; `is_complete` `:72–79` checks raw_text OR (job_titles OR skills)
- `SearchConfig` `:83–117` — job_titles, primary/secondary/tertiary_skills, relevance_keywords, negative_title_keywords, locations, visa_keywords, core_domain_words (set), supporting_role_words (set), search_queries. `from_defaults()` returns minimal config — users MUST upload CV for matching.

---

## §5 — Date Field Audit

**Schema semantics:**
- `database.py:34` — `date_found TEXT NOT NULL` = **crawl timestamp**, always `datetime.now(...)`
- `database.py:41` — `posted_at TEXT` = real upstream posting date (NULL allowed)
- `database.py:45` — `date_confidence TEXT DEFAULT 'low'` enum
- `database.py:46` — `date_posted_raw TEXT` audit field

**49 source files audited.** Every one writes `date_found = datetime.now(timezone.utc).isoformat()` — correct post-Batch-1 semantics. Bucketed by `posted_at` handling:

**HONEST-EXTRACTED (36)** — extracts real upstream date into `posted_at`:
- Free APIs (11): arbeitnow, devitjobs, gov_apprenticeships, himalayas, hn_jobs, jobicy, landingjobs, remoteok, remotive, teaching_vacancies, aijobs
- Keyed APIs (6): adzuna, careerjet, findwork, jsearch, reed, google_jobs
- ATS (7): ashby, comeet, lever, recruitee, rippling, smartrecruiters, workday
- Feeds (7): biospace, jobs_ac_uk, nhs_jobs_xml, realworkfromanywhere, workanywhere, weworkremotely, uni_jobs
- Scrapers (1): eightykhours
- Other (4): hackernews, indeed/glassdoor, themuse, nofluffjobs

**HONEST-NULL (13)** — `posted_at=None` + `date_confidence="low"` (upstream lacks reliable date):
- Keyed APIs (1): jooble (drops `updated` field, kept in `date_posted_raw`)
- ATS (5): greenhouse, personio, pinpoint, successfactors, workable
- Feeds (1): nhs_jobs (closingDate is deadline, not post date)
- Scrapers (6): aijobs_ai, aijobs_global, bcs_jobs, climatebase, jobtensor, linkedin

**FABRICATING (0).** No source sets `posted_at = datetime.now(...)`. Batch 1 redefinition fully honoured.

---

## §6 — Database Layer

**Connection** (`database.py:18–23`): WAL mode + `busy_timeout=5000` + `aiosqlite.Row` factory. 434 LOC.

**Tables:**

| Table | CREATE anchor | Notes |
|---|---|---|
| `jobs` | `database.py:24–50` | UNIQUE `(normalized_company, normalized_title)`; 5 indexes |
| `run_log` | `database.py:51–58` | `per_source` JSON column |
| `user_actions` | rebuilt `0002_multi_tenant.up.sql:22–39` | UNIQUE `(user_id, job_id)`; FK→users CASCADE |
| `applications` | rebuilt `0002_multi_tenant.up.sql:42–61` | UNIQUE `(user_id, job_id)`; FK→users CASCADE |
| `users` | `0001_auth.up.sql:2–8` | id PK, email UNIQUE, password_hash, deleted_at |
| `sessions` | `0001_auth.up.sql:10–18` | FK→users CASCADE; idx user/expires |
| `user_feed` | `0003_user_feed.up.sql:4–28` | UNIQUE `(user_id, job_id)`; status enum; 3 partial indexes |
| `notification_ledger` | `0004_notification_ledger.up.sql:4–18` | UNIQUE `(user_id, job_id, channel)` — idempotency |
| `user_channels` | `0005_user_channels.up.sql:11–22` | FK→users CASCADE; credential_encrypted BLOB (Fernet); key_version |
| **`user_profiles`** | **`0006_user_profiles.up.sql:11–20`** | **PK `user_id`; cv_data/preferences/linkedin_data/github_data JSON cols (Batch 3.5.2)** |
| `_schema_migrations` | `runner.py:50–58` | id PK (NNNN_name stem) + applied_at UTC |

**`jobs` Pillar-3-Batch-1 columns** (lines 40–48): posted_at, first_seen_at, last_seen_at, last_updated_at, date_confidence (DEFAULT 'low'), date_posted_raw, consecutive_misses (DEFAULT 0), staleness_state (DEFAULT 'active'). 5 indexes at `:59–63`: date_found, first_seen, match_score, staleness_state, last_seen_at.

**Migration runner** (`backend/migrations/runner.py:49–133`, 175 LOC):
- Lexical discovery of `.up.sql`/`.down.sql` pairs via `_discover_pairs()` `:66–78`
- `up()` `:81–110` — reads pending, executes via `executescript()`, records stem + UTC
- `down()` `:113–133` — reverses most recent applied
- `status()` `:136–147` — returns `{applied, pending}`
- CLI at `:150–174`: `python -m migrations.runner {up|down|status} [db_path]`

**Legacy auto-migration path** `database.py:85–114`: ALTER-TABLE-add 8 columns if missing — forward-compat safety net. Whitelist-validated.

**Profile storage — NOW PER-USER** (`backend/src/services/profile/storage.py`, 145 LOC — Batch 3.5.2):
- Backed by `user_profiles` table (synchronous stdlib `sqlite3`, not `aiosqlite`)
- `save_profile(profile, user_id)` `:42–60` — upsert JSON-serialised dataclasses
- `load_profile(user_id)` `:63–84` — rehydrate with `_filter_fields()` drift guard `:103–110`
- `profile_exists(user_id)` `:87–100`
- `_maybe_hydrate_legacy_json(user_id)` `:113–145` — one-shot non-destructive migration of legacy `data/user_profile.json` into `user_profiles` on first `load_profile(DEFAULT_TENANT_ID)`
- **Closes the multi-user data-loss bug** previously flagged (two authenticated users no longer overwrite each other's CVs)

---

## §7 — API

**26 endpoints** across 8 route modules (auth.py has 4, the rest listed below):

| Module | Count | Auth status |
|---|---|---|
| `health.py` | 3 | 3 public |
| `jobs.py` | 3 | 3 require_user ✓ (Batch 3.5) |
| `actions.py` | 4 | 4 require_user ✓ (Batch 3.5) |
| `pipeline.py` | 5 | 5 require_user ✓ (Batch 3.5) |
| **`profile.py`** | **4** | **4 require_user ✓ (Batch 3.5.1)** |
| **`search.py`** | **2** | **2 require_user ✓ (Batch 3.5.1 — existence-hiding 404)** |
| `auth.py` | 4 | 2 public (register/login) + 1 optional (logout) + 1 require_user (/me) |
| `channels.py` | 4 | 4 require_user ✓ |

**App setup** (`api/main.py`, 47 LOC): `lifespan` `:19–23` runs `init_db()` startup + `close_db()` shutdown. CORS middleware `:31–37` reads `FRONTEND_ORIGIN` (default `http://localhost:3000`). All routers mounted with `/api` prefix `:39–47`.

**Endpoint listing (METHOD PATH — auth — anchor):**

Health: `GET /api/health` `:16` · `GET /api/status` `:21` · `GET /api/sources` `:46`

Auth: `POST /api/auth/register` `:54` (public) · `POST /api/auth/login` `:77` (public) · `POST /api/auth/logout` `:99` (optional cookie) · `GET /api/auth/me` `:113` (require_user)

Jobs (require_user): `GET /api/jobs/export` `:68` · `GET /api/jobs` `:111` · `GET /api/jobs/{job_id}` `:183`

Actions (require_user): `POST /api/jobs/{job_id}/action` `:14` · `DELETE /api/jobs/{job_id}/action` `:33` · `GET /api/actions` `:43` · `GET /api/actions/counts` `:56`

Pipeline (require_user): `GET /api/pipeline` `:39` · `GET /api/pipeline/counts` `:52` · `GET /api/pipeline/reminders` `:64` · `POST /api/pipeline/{job_id}` `:76` · `POST /api/pipeline/{job_id}/advance` `:90`

Profile (require_user — **closed in 3.5.1**): `GET /api/profile` `:57` · `POST /api/profile` `:71` · `POST /api/profile/linkedin` `:129` · `POST /api/profile/github` `:155`

Search (require_user — **closed in 3.5.1 with existence-hiding 404**): `POST /api/search` `:28` · `GET /api/search/{run_id}/status` `:58` — cross-user access and unknown `run_id` both return 404 (no 403 enumeration oracle); `user_id` scrubbed from response payload.

Channels (require_user): `GET /api/settings/channels` `:43` · `POST /api/settings/channels` `:64` · `DELETE /api/settings/channels/{channel_id}` `:88` · `POST /api/settings/channels/{channel_id}/test` `:105` (two-layer ownership check: HTTP + dispatcher filter)

**Auth module** (`services/auth/`):
- `passwords.py` — argon2id (OWASP defaults: time=3, mem=64 MiB, para=4): `hash_password` `:16` · `verify_password` `:20`
- `sessions.py` — signed-cookie + HMAC-SHA256, 30-day expiry: `create_session` `:29` · `resolve_session` `:63` · `revoke_session` `:92`
- `auth_deps.py` — `require_user` `:71` (401 if invalid) · `optional_user` `:83` (returns Optional). Cookie name `job360_session`. Secret from `SESSION_SECRET`, fail-closed `:36–41`.

**Pydantic models** (`api/models.py`): 20 `BaseModel` classes (lines 8, 13, 19, 23, 31, 60, 66, 71, 77, 81, 92, 112, 118, 123, 128, 133, 140, 150, 154, 158).

**IDOR — fully closed.** All per-user routes filter by `user.id` from `Depends(require_user)` — `user_id` never accepted from URL / body. Scrutinised by `tests/test_api_idor.py` (13 tests).

**Rate limiting middleware:** ABSENT. **SSE / WebSocket:** ABSENT (search uses polling via `/api/search/{run_id}/status`).

---

## §8 — Frontend

**Stack** (`frontend/package.json`):
- next 16.2.2 · react 19.2.4 · react-dom 19.2.4
- tailwindcss ^4 · @tailwindcss/postcss ^4
- shadcn 4.1.2 · @base-ui/react 1.3.0 · lucide-react 1.7.0 · recharts 3.8.1 · motion 12.38.0
- clsx 2.1.1 · class-variance-authority 0.7.1 · tailwind-merge 3.5.0 · tw-animate-css 1.4.0
- **No** TanStack Query / React Query / SWR

**Pages under `frontend/src/app/`** (9):
- `/` (root) · `/dashboard` · `/jobs/[id]` · `/pipeline` · `/profile` · `/settings/channels`
- `/(auth)/login` · `/(auth)/register` (route group)

**Component groupings** (`frontend/src/components/`, 27 files):
- `jobs/` (6): FilterPanel, JobCard, JobList, ScoreCounter, ScoreRadar, TimeBuckets
- `pipeline/` (1): KanbanBoard
- `profile/` (3): CVUpload, CVViewer, PreferencesForm
- `layout/` (3): FloatingIcons, Footer, Navbar
- `ui/`: shadcn primitives

**API client** (`frontend/src/lib/api.ts`, 310 LOC): **27 exported functions** covering health/jobs/actions/profile/search/pipeline/auth/channels. Relies on HTTP-only cookies for auth (no explicit headers).

**Types** (`frontend/src/lib/types.ts`, 162 LOC): mirror backend Pydantic models with minor trim (CVDetail omits a few display-only arrays).

**SSE / WebSocket: ABSENT.** Grep: `EventSource` 0 · `WebSocket` 0. Search polls via `getSearchStatus(runId)` `:191`.

---

## §9 — Notifications (Legacy CLI Path)

`NotificationChannel` ABC at `services/notifications/base.py:11`. Auto-discovery `get_all_channels()` `:38`; gating `get_configured_channels()` `:46`; shared `format_salary()` `:27`.

| Channel | Class anchor | Env gate |
|---|---|---|
| Email | `email_notify.py:82` | SMTP_HOST/PORT/EMAIL/PASSWORD/NOTIFY_EMAIL |
| Slack | `slack_notify.py:95` | SLACK_WEBHOOK_URL |
| Discord | `discord_notify.py:75` | DISCORD_WEBHOOK_URL |

Report generator `report_generator.py` (162 LOC): `generate_html_report()` (HTML inline CSS, time bucketing 24h/24-48h/48-72h/3-7d, top 10/bucket); `generate_markdown_report()` (unused).

---

## §9b — Delivery Layer (Batch 2+)

**Apprise dispatcher** (`services/channels/dispatcher.py`, 174 LOC):
- Lazy import: `_get_apprise_cls()` `:23–31` — Apprise pulled in only at first dispatch call (keeps pytest fast, CLAUDE.md rule #11)
- `load_user_channels()` `:42` — reads `user_channels`, decrypts via Fernet
- `dispatch()` `:72` — loops enabled channels, `ap.add(url)` + `_notify_async()` (prefers `ap.async_notify`, sync fallback)
- `test_send()` — two-layer ownership check (HTTP route + service filter)

**Fernet crypto** (`services/channels/crypto.py`):
- `encrypt()` `:37` · `decrypt()` `:41` (catches `InvalidToken` → `ValueError`)
- Key from `CHANNEL_ENCRYPTION_KEY` env (`:27`); fail-closed if unset
- `key_version` column exists (`0005:17`) but rotation logic NOT IMPLEMENTED

**FeedService** (`services/feed.py:47`, 171 LOC):

| Method | Anchor | Purpose |
|---|---|---|
| `list_for_user` | `:60` | Dashboard: active rows, score DESC, limit 200 |
| `list_pending_notifications` | `:82` | Worker: unsent + active + score ≥ threshold, limit 15 |
| `mark_notified` | `:106` | Batch write `notified_at` |
| `update_status` | `:117` | Write `status` enum |
| `cascade_stale` | `:128` | Ghost detection: mark all users' rows stale per job |
| `upsert_feed_row` | `:141` | Idempotent (user, job) upsert |

**Worker tasks** (`backend/src/workers/tasks.py`, 368 LOC):

| Task | Anchor | Notes |
|---|---|---|
| `score_and_ingest` | `:46` | Pre-filter + score all users, upsert feed, queue notifications above threshold |
| `send_notification` | `:200` | Dispatch all enabled channels, write ledger sent/failed |
| `mark_ledger_sent_task` | `:284` | ARQ wrapper for `mark_ledger_sent` `:157` |
| `mark_ledger_failed_task` | `:293` | ARQ wrapper for `mark_ledger_failed` `:172` |

Helpers: `_record_ledger_if_new` `:141` · `idempotency_key` `:26–29` (SHA1 of `user_id:job_id:channel` — currently unused beyond audit; ledger UNIQUE constraint replaces it).

Per-user `JobScorer` in `score_and_ingest` at `:102–105, 113`. Enqueue path at `:120–130` lazily dispatches `send_notification` for scores above threshold.

**WorkerSettings** (`backend/src/workers/settings.py:80`, 103 LOC):
```python
functions = [score_and_ingest, send_notification,
             mark_ledger_sent_task, mark_ledger_failed_task]
```
`redis_settings` derived from `REDIS_URL` env (`:99`). Lazy `arq.connections.RedisSettings` import at `:74` keeps pytest Redis-free.

**Gaps (unchanged since Batch 3.5):**
- Quiet hours: ABSENT (grep 0)
- Digest aggregation: STUB only (`urgency` param at `:204`, no batching; line 122 hardcodes `"instant"`)
- Telegram handler class: ABSENT (delegated to Apprise `tgram://` URL scheme, schema-ready at `0005:8`)
- Webhook handler class: ABSENT (delegated to Apprise `json://` scheme, schema-ready at `0005:9`)
- SMS: ABSENT entirely
- Direct Redis client: NONE — all enqueue via ARQ ctx

---

## §10 — Config

**Env vars** (`backend/src/core/settings.py`):

| Var | Required? | Default | Used by | Anchor |
|---|---|---|---|---|
| REED/ADZUNA/JSEARCH/JOOBLE/SERPAPI/CAREERJET/FINDWORK keys | No | "" | sources | `:15–22` |
| GITHUB_TOKEN | No | "" | profile enrichment | `:25` |
| GEMINI_API_KEY / GROQ_API_KEY / CEREBRAS_API_KEY | No | "" | CV LLM providers | `:28–30` |
| SMTP_HOST (hardcoded=smtp.gmail.com) · SMTP_PORT (587) | hardcoded | — | email channel | `:33–34` |
| SMTP_EMAIL/PASSWORD/NOTIFY_EMAIL | No | "" | email channel | `:35–37` |
| SLACK_WEBHOOK_URL · DISCORD_WEBHOOK_URL | No | "" | channels | `:40–41` |
| TARGET_SALARY_MIN/MAX | No | 40k/120k | tiebreaker | `:49–50` |
| **SESSION_SECRET** | **Yes (prod)** | dev fallback | `auth_deps.py:36` | — |
| **CHANNEL_ENCRYPTION_KEY** | **Yes (prod)** | — (raises if missing) | `crypto.py:27` | — |
| FRONTEND_ORIGIN | No | `http://localhost:3000` | CORS | `api/main.py:34` |
| REDIS_URL | No | `redis://localhost:6379` | workers | `workers/settings.py:99` |

**.env.example drift:** `CEREBRAS_API_KEY` loaded at `settings.py:30` but missing from `.env.example`.

**Constants:** `MIN_MATCH_SCORE=30` `:44` · `MAX_RESULTS_PER_SOURCE=100` `:45` · `MAX_DAYS_OLD=7` `:46` · `MAX_RETRIES=3` `:109` · `RETRY_BACKOFF=[1,2,4]` `:110` · `REQUEST_TIMEOUT=30` `:113` · `USER_AGENT` `:114` · `RATE_LIMITS` (50 entries) `:53–106`.

**`keywords.py` state — domain lists EMPTIED** (2026-04-09 LLM-driven decision, 72 LOC):
- JOB_TITLES, PRIMARY_SKILLS, SECONDARY_SKILLS, TERTIARY_SKILLS, RELEVANCE_KEYWORDS, NEGATIVE_TITLE_KEYWORDS — **all `[]`** at `:16–21`
- Retained domain-agnostic: LOCATIONS (25 entries `:28–55`), VISA_KEYWORDS (8 entries `:63–72`)
- KNOWN_SKILLS / KNOWN_TITLE_PATTERNS — DELETED

**`companies.py` ATS slug counts (266 total — prior memory "268" was wrong):**

| Platform | Count | Anchor |
|---|---|---|
| Greenhouse | 80 | `:4–30` |
| Lever | 35 | `:34–46` |
| Workable | 25 | `:50–60` |
| Ashby | 25 | `:64–74` |
| SmartRecruiters | 15 | `:78–85` |
| Pinpoint | 15 | `:89–96` |
| Recruitee | 20 | `:100–108` |
| Workday | 20 (tenant dicts) | `:112–134` |
| Personio | 18 | `:216–224` |
| SuccessFactors | 3 sitemaps | `:229–234` |
| Rippling | 5 | `:240–246` |
| Comeet | 5 | `:251–257` |
| **Total** | **266** | — |

---

## §11 — Deps

**Backend** (`backend/pyproject.toml`, 81 LOC):
- Core: aiohttp ≥3.9 · aiosqlite ≥0.19 · python-dotenv ≥1.0 · fastapi ≥0.115 · uvicorn[standard] ≥0.30 · httpx ≥0.27
- Data/CV: pdfplumber ≥0.10 · python-docx ≥1.1 · pandas ≥2.0 · jinja2 ≥3.1
- LLM: google-generativeai ≥0.8 · groq ≥0.11 · cerebras-cloud-sdk ≥1.0
- Auth/security (Batch 2): argon2-cffi ≥23.1 · itsdangerous ≥2.2 · cryptography ≥42.0 · email-validator ≥2.1
- Notifications: apprise ≥1.7
- CLI/UX: click ≥8.1 · rich ≥13.0 · humanize ≥4.9
- Optional dev (pyproject): pytest ≥8.0 · pytest-asyncio ≥0.23 · aioresponses ≥0.7 · fpdf2 ≥2.7 · **pytest-randomly ≥4.0** (added Batch 3.5.4, opt-out by default via `addopts = -p no:randomly`)
- Optional indeed: python-jobspy

**Frontend** key versions: see §8.

**Absent:** sentence-transformers · chromadb · pgvector · ESCO lib · Prometheus client (in pyproject — but exporter lives at `backend/ops/`).

---

## §12 — Tests

**43 test files · 615 collected · baseline run 600p/0f/3s** (per `13d4305` merge subject). `test_main.py` (12 tests) typically excluded due to JobSpy live-HTTP leak (~32-min hang).

Top test files by size:

| File | Tests | Coverage |
|---|---|---|
| `test_sources.py` | 81 | All 50 source connectors (mocked HTTP) |
| `test_scorer.py` | 60 | Scoring components, penalties, visa, recency |
| `test_profile.py` | 48 | SearchConfig, UserProfile, CV parser, keyword generator, JobScorer |
| `test_linkedin_github.py` | 46 | LinkedIn PDF parser + GitHub enricher |
| `test_time_buckets.py` | 33 | Bucketing logic |
| `test_models.py` | 22 | Job dataclass, normalisation, salary sanity |
| `test_notifications.py` | 19 | Email/Slack/Discord delivery |
| `test_prefilter.py` | 15 | Location/exp/skill cascade |
| `test_date_schema.py` | 13 | Date parsing + buckets |
| `test_deduplicator.py` | 13 | Dedup grouping + suffix stripping |
| `test_api_idor.py` | 13 | Cross-tenant access denial (Batch 3.5 + 3.5.1) |
| `test_profile_storage.py` | 12 | **Per-user storage + legacy JSON hydration** (Batch 3.5.2) |
| `test_main.py` | 12 | **EXCLUDED — JobSpy live HTTP** |
| `test_cli.py` | 11 | CLI commands (`len(SOURCE_REGISTRY) == 50`) |
| `test_ghost_detection.py` | 11 | Stale detection state machine |
| `test_conditional_fetch.py` | 11 | ConditionalCache (incl. hit/miss metrics Batch 3.5.3) |
| `test_database.py` | 9 | Schema + migrations + history |
| `test_api.py` | 9 | Routing + response models |
| `test_auth_routes.py` | 8 | Register/login/logout/me |
| `test_feed_service.py` | 8 | FeedService methods |
| `test_llm_provider.py` | 8 | LLM CV-parser providers |
| `test_worker_tasks.py` | 8 | Async task execution |
| `test_channels_dispatcher.py` | 7 | Apprise routing |
| `test_channels_routes.py` | 7 | Channel CRUD |
| `test_circuit_breaker.py` | 7 | State machine |
| `test_kpi_exporter.py` | 7 | KPI exporter (Batch 1) |
| `test_notification_base.py` | 7 | ABC + format_salary |
| `test_reports.py` | 6 | Report generation |
| `test_scheduler.py` | 6 | TieredScheduler |
| `test_setup.py` | 6 **was 4 per 8b audit** | setup.sh validation |
| `test_auth_sessions.py` | 5 | Session lifecycle |
| `test_cli_view.py` | 5 | Rich table |
| `test_cron.py` | 5 | cron_setup.sh |
| `test_migrations.py` | 5 | Migration runner |
| `test_rate_limiter.py` | 5 | Async limiter |
| `test_worker_send_notification.py` | 5 | `send_notification` body (Batch 3.5) |
| `test_auth_passwords.py` | 4 | argon2id |
| `test_channels_crypto.py` | 4 | Fernet |
| `test_companies_slugs.py` | 4 | ATS catalog rule |
| `test_csv_export.py` | 4 | CSV format |
| `test_main_scheduler_wiring.py` | 3 | Scheduler on hot path (Batch 3.5) |
| `test_worker_settings.py` | 3 | WorkerSettings imports without Redis (Batch 3.5) |
| `test_tenancy_isolation.py` | 1 | DEFAULT_TENANT_ID placeholder contract |

**conftest.py fixtures:** `authenticated_async_context` (Batch 3.5.4), `sample_ai_job`, `sample_unrelated_job`, `sample_duplicate_jobs`, `sample_visa_job`, `sample_non_uk_job`, `sample_empty_description_job`.

**Pytest config** (`pyproject.toml:57–66`):
```toml
testpaths = ["tests"]
asyncio_mode = "auto"
pythonpath = ["."]
addopts = "-p no:randomly"  # disabled by default; enable via -p randomly
```
**No @pytest.mark.skip / @pytest.mark.xfail** anywhere. Clean baseline.

---

## §13 — Known Issues

**TODO/FIXME/HACK/XXX in `backend/src/`:** zero hits (re-verified).
**Bare `except Exception: pass`:** zero hits (re-verified).

**Inspection findings:**
1. **Conditional cache adoption: 1 of 49 sources.** `nhs_jobs_xml` opted in during Batch 3.5.3; 48 others still on `_get_json`. Not dead code anymore — the helper has one live caller — but adoption remains a rounding error. Broader pilot pending.
2. **Digest aggregation STUB.** `send_notification(urgency=...)` accepts both `"instant"` and `"digest"` per docstring at `workers/tasks.py:229–231`, but `score_and_ingest` hardcodes `"instant"` at `:122`; no batching logic exists.
3. **Quiet hours ABSENT.** No scheduling filter in notification path.
4. **Telegram / Webhook handler classes ABSENT.** Apprise delegates cover functionally (`tgram://`, `json://` schemes); validation stubs exist in routes but no dedicated channel classes.
5. **SMS ABSENT entirely.** No `sms` in `_VALID_TYPES`.
6. **Fernet key rotation NOT IMPLEMENTED.** `key_version` column exists but no rotation code path.
7. **Production-boot smoke against real Redis PENDING.** Batch 3.5 P3 deferred — ARQ runtime works in isolation but never booted against real broker. Carries into Batch 4.
8. **`.env.example` drift:** `CEREBRAS_API_KEY` loaded at `settings.py:30` but not in `.env.example`.
9. **Salary / domain scoring dimensions ABSENT.** 4-dimensional scoring only. Pillar 2 prerequisite gap.
10. **No CI / Docker / observability stack beyond Grafana JSON + KPI exporter.**

**Largest files (LOC) — complexity hotspots:**

| File | LOC |
|---|---|
| `src/main.py` | 560 |
| `src/repositories/database.py` | 434 |
| `src/services/profile/linkedin_parser.py` | 412 |
| `src/workers/tasks.py` | 368 |
| `src/services/skill_matcher.py` | 335 |
| `src/services/profile/cv_parser.py` | 258 |
| `src/core/companies.py` | 257 |
| `src/sources/base.py` | 234 |
| `src/services/profile/github_enricher.py` | 232 |
| `src/cli.py` | 218 |

---

## §14 — Infrastructure

| Surface | Status | Anchor |
|---|---|---|
| GitHub Actions / CI | **None** | `.github/workflows/` exists but empty |
| Dockerfile | **None** | absent |
| docker-compose.yml | **None** | absent |
| Grafana dashboard | **Present** | `backend/ops/grafana_dashboard.json` |
| KPI exporter | **Present** (port 9310, 5-min refresh) | `backend/ops/exporter.py` (7 live KPIs + 4 stubs, plus cache hit/miss post-3.5.3) |
| Prometheus client | dep declared in ops, used by exporter | — |
| OpenTelemetry | **Absent** | grep 0 |
| Database backend | **SQLite** (`backend/data/jobs.db`) | aiosqlite ≥0.19 — no Postgres/Supabase migration |
| ESCO taxonomy (Pillar 1 prep) | **Absent** | no `backend/data/esco*` files; no ESCO imports |
| setup.sh | Present (74 LOC) | root |
| cron_setup.sh | Present (51 LOC) — 4 AM/4 PM Europe/London | root |

---

## §15 — Dead Code

**Empty directories under `backend/src/` (pre-Phase-4 placeholders, `__pycache__` only — confirmed by `ls`):**
- `filters/` — 0 live `.py` files
- `llm/` — 0 live files
- `pipeline/` — 0 live files
- `validation/` — 0 live files

Placeholders from the clean-architecture rename. Real implementations live under `services/` (filters → skill_matcher/deduplicator/prefilter; llm → profile/llm_provider; pipeline → main.py; validation → `__post_init__` in models.py). **Safe to delete the empty dirs.**

**Other empty dirs (test fixtures):** `backend/tests/qa_profiles/cvs/` · `backend/tests/qa_profiles/pdfs/`.

**Markers:** no `*.bak` / `*.orig` / `*.tmp`. No `DEPRECATED` / `UNUSED` / `DELETE ME` in first-20-line headers.

**`scripts/`, `migrations/`, `data/`, `ops/` all active.**

---

## Appendix A — Anchor Index

| Component | Anchor |
|---|---|
| `SOURCE_REGISTRY` | `backend/src/main.py:83` |
| `_build_sources` | within `run_search` (`backend/src/main.py`) |
| `run_search` | `backend/src/main.py:279` |
| TieredScheduler dispatch on hot path | `backend/src/main.py:363–364` |
| Ghost detection pass | `backend/src/main.py:428` (calls `:144–187`) |
| `JobScorer` class | `backend/src/services/skill_matcher.py:281` |
| `score_job` (module fallback) | `backend/src/services/skill_matcher.py:259` |
| `recency_score_for_job` | `backend/src/services/skill_matcher.py:195` |
| `Job` dataclass | `backend/src/models.py:17` |
| `Job.normalized_key` | `backend/src/models.py:61` |
| `jobs` table CREATE | `backend/src/repositories/database.py:24` |
| `user_feed` CREATE | `backend/migrations/0003_user_feed.up.sql:4` |
| `notification_ledger` CREATE | `backend/migrations/0004_notification_ledger.up.sql:4` |
| `user_channels` CREATE | `backend/migrations/0005_user_channels.up.sql:11` |
| `user_profiles` CREATE | `backend/migrations/0006_user_profiles.up.sql:11` |
| `users` + `sessions` CREATE | `backend/migrations/0001_auth.up.sql:2,10` |
| `require_user` | `backend/src/api/auth_deps.py:71` |
| `optional_user` | `backend/src/api/auth_deps.py:83` |
| `WorkerSettings` | `backend/src/workers/settings.py:80` |
| `send_notification` | `backend/src/workers/tasks.py:200` |
| `score_and_ingest` | `backend/src/workers/tasks.py:46` |
| `idempotency_key` | `backend/src/workers/tasks.py:26` |
| `TieredScheduler` | `backend/src/services/scheduler.py:71` |
| `TIER_INTERVALS_SECONDS` | `backend/src/services/scheduler.py:36–47` |
| `CircuitBreaker` | `backend/src/services/circuit_breaker.py:29` |
| `BreakerRegistry` | `backend/src/services/circuit_breaker.py:75` |
| `ConditionalCache` | `backend/src/services/conditional_cache.py:29` |
| `_get_json_conditional` | `backend/src/sources/base.py:158` (1 live caller: `nhs_jobs_xml`) |
| `FeedService` | `backend/src/services/feed.py:47` |
| `passes_prefilter` | `backend/src/services/prefilter.py:126` |
| `save_profile` / `load_profile` (per-user) | `backend/src/services/profile/storage.py:42,63` |
| migration runner | `backend/migrations/runner.py:49–133` |
| KPI exporter | `backend/ops/exporter.py` |
| Grafana dashboard | `backend/ops/grafana_dashboard.json` |
| Apprise dispatcher (lazy import) | `backend/src/services/channels/dispatcher.py:23,72` |
| Fernet crypto | `backend/src/services/channels/crypto.py:27,37,41` |

*End of CurrentStatus.md.*
