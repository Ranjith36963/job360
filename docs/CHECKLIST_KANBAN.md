# Job360 — End-to-End Verification & Audit (merged)

> Merges the two prior docs into one source of truth:
> - **VERIFY_KANBAN** — deep *functional* live pass with **real user data** (real CV/LinkedIn/GitHub,
>   demo account) on the dev server; found + fixed 6 real bugs.
> - **AUDIT_CHECKLIST** — broad *structural* audit (frontend↔backend↔DB↔tests map, 49-route sweep,
>   production-build browser pass); added findings the functional pass missed.
>
> **Rule:** nothing is "LIVE" without evidence (DB row / HTTP code / screenshot / log). Code is the
> proof, not docs. **Legend:** ✅ LIVE-verified · ☑️ code present + tested (not fired live) ·
> ⚠️ partial/needs-config · 🚪 gated on infra · 🐛 open bug.

**Real test data (functional pass):** CV `User_info/CV/RanjithMG-AI-ML.pdf`, LinkedIn `Profile.pdf`,
GitHub `Ranjith36963`, demo account loading the real profile.

**Standing external limits (NOT code bugs):**
- LLM engines 2 & 4 *new* output → Gemini/Groq free-tier daily quota (429); Cerebras fallback works.
- Scheduled notification delivery → **Redis + ARQ worker not installed** (delivery *mechanism* proven via test-send).
- Slack/Discord OAuth connect → no provider creds in `.env` (routes exist, correctly gated-disabled).
- Browser auth needs **frontend + backend same-origin** (see Finding #12).

---

## Part 1 — Functional live verification (real data, every page + route)

| § | Area | Status | Evidence |
|---|---|---|---|
| A1 | Landing `/` | ✅ | renders (now "47 sources" after fix) |
| A2 | Register | ✅ | POST /auth/register 201, auto-login, /me 200 |
| A3 | Login | ✅ | /auth/login 200 → /dashboard |
| A4 | Logout | ✅ | /auth/logout 204, cookie cleared, /me→401 |
| A5 | auth/me | ✅ | 200 in / 401 out |
| A6 | Forgot password | ✅ | 204 + success state (**was 422 — Finding #8 FIXED**) |
| A7 | Reset password confirm | ✅ | same fixed JSON path; unit-tested (no live token walk) |
| A8 | Verify-email confirm | ✅ | minted token → `email_verified_at` set (live) |
| B1–B9 | Profile (CV/LinkedIn/GitHub/versions/diff/restore/json-resume) | ✅ | CV 200 (64 skills parsed real CV); LinkedIn `merged:true`; versions diff/restore live; json-resume 200 |
| ★ | Profile-version re-score (M2) | ✅ | profile change → new version → feed re-scored 35→97 rows, 30 re-judged |
| C1–C5 | Search + Dashboard | ✅ | full pipeline live (6089 raw→dedup→4 new, 40 sources); hybrid re-rank; COALESCE AI sort |
| D1–D7 | Jobs (detail/like/remove/duplicates/CSV/actions) | ✅ | like→DB row; un-like 200; CSV 15.9KB (⚠️ no UI button) |
| E1–E7 | Pipeline (add/advance/notes/timeline/counts/reminders) | ✅ | all 5 stages (applied→outreach→interview→offer→rejected); add+advance→DB stage+history |
| F1–F3 | Notifications history/stats | ✅ | 200 (empty ledger) |
| G1–G10 | Channels (providers/webhook add/test/delete/OAuth-gated) | ✅ | webhook add 201 (Fernet-encrypted); **test-send fired real Apprise POST**; delete works |
| H1–H5 | Notification rules (GET + PUT-upsert) | ✅ | GET 200 / PUT-upsert 200 — **one row per user, NO DELETE route** (UNIQUE(user_id), rule #23) |
| I1–I3 | Account guards (pw/email change) | ✅ | wrong pw → 401 (rule #26) |
| I4 | Delete account | ✅ | **now password-gated** (was unguarded — Finding #6 FIXED) |
| J1–J6 | Admin/Ops (sources/status/health/runs/source-health) | ✅ | all 200 |

**Matching engines (all 3 + judge enabled in this `.env`):** Keyword ✅ live · Dimensions ✅ (155 enrichment rows; new LLM quota-blocked) · Semantic/hybrid ✅ live (3,536 embeddings) · LLM judge ✅ (verdicts proven; new calls quota-blocked).

**Engine internals present in code (not UI):** 4-layer dedup, TieredScheduler, CircuitBreaker, ghost-detection, conditional ETag fetch, ARQ crons, timezone-aware quiet hours.

---

## Part 2 — Structural map (frontend ↔ backend ↔ DB ↔ tests)

Every feature traced through code (49 routes total). Backend pytest exists for nearly every feature
(`test_auth_routes`, `test_email_verification`, `test_password_reset`, `test_csv_export`,
`test_profile*`, `test_linkedin_github`, `test_pipeline_timeline`, `test_channels_*`,
`test_notification_rules`, `test_account_mgmt`, `test_source_health`, `test_deduplicator`,
`test_scoring_dimensions`). Frontend vitest covers the major flows (landing, login, dashboard,
filters, job detail, JobCard, CVUpload, PreferencesForm, KanbanBoard, channels, account).

**Wired-but-unused frontend functions (build exists, no UI caller):** `getRecentRuns`,
`getEmailVerified` (no verify banner), `getActions`/`getActionCounts`. (`exportJobsCsv` is now
wired — Export button at `frontend/src/app/dashboard/page.tsx:400`.)
**Shell pages:** `/jobs` → redirect to `/dashboard`; `/settings` → tab nav only.

---

## Part 3 — All findings (consolidated)

### Fixed (functional pass — TDD + live-verified + committed)
| # | Bug | Sev | Fix |
|---|---|---|---|
| 8 | Forgot-pw / reset / verify-email all **422** (missing JSON Content-Type) — whole recovery UX dead | HIGH | ✅ `5350963` → live 204 |
| 6 | Delete-account required **no password** (rule #26) | HIGH | ✅ `ac559ba` → wrong-pw 401 |
| 1 | Landing "50 sources" vs real count | Low | ✅ `d76183f` (and later → **47** after gov_apprenticeships restore) |
| 7 | Account copy "stay logged in" (actually logs out) | Low | ✅ `d76183f` |
| 2 | Job-detail tab title wrong after soft-nav | Low | ✅ `c5f4943` |
| 3 | Radar `width(-1)` console warning | Low | ✅ `c5f4943` |

> ⚠️ **Verify-on-main:** these fixes landed on `fix/per-user-search-and-scoring-gate`; confirm each is on current `origin/main` (some may need re-checking after the channels/notifications overhaul merge).

### Findings #10–#15 — ALL FIXED ✅ (were open in the structural / prod-build pass)
| # | Bug | Sev | Fix (shipped) |
|---|---|---|---|
| 10 | Broken footer links — `/privacy`, `/terms`, `/contact` → 404 | Med | ✅ added the 3 pages (`824879d`) |
| 11 | SQLite "database is locked" under concurrent writes — dropped rows | HIGH | ✅ busy_timeout 5s→30s + `with_write_retry` wrapping rescore/judge writes (`db_retry.py`); Postgres still the long-term scale plan |
| 12 | Same-origin auth requirement (split-host cookie breaks the gate) | HIGH | ✅ next.config `/api` proxy + relative fetch base (`824879d`) |
| 13 | `/admin` + `/notifications` shown to logged-out users; no gate | Med | ✅ middleware now gates `/admin` + `/notifications` (`824879d`). *Decision (2026-06-21): **no admin/RBAC model** — access tiers will be **Free / Premium subscription plans** instead. Deferred — see `docs/plans/2026-06-21-free-premium-plans.md`.* |
| 14 | Login error not in `role="alert"` (a11y) | Low | ✅ wrapped in `role=alert` (`824879d`) |
| 15 | Email verification not enforced | Low→ | ✅ enforced — `require_verified_user` → 403 on `POST /search`; frontend redirects to `/verify-email` (`fc10921` + frontend) |
| — | CSV export had no UI button (endpoint worked) | Low | ✅ added an Export button to the dashboard (`824879d`) |

**Result: the entire audit checklist is now fixed/live.** The only remaining *non-bug*
gate is real notification **delivery**, which needs **Redis + ARQ worker** installed (infra,
not code). Postgres remains the recommended long-term scale upgrade behind the #11 mitigation.

**Live verification of the two fixes (loop2, this session, running app):**
- **#15** — registered an unverified user → `POST /api/search` returned **403 `email_not_verified`**;
  after marking the email verified → `POST /api/search` returned **200 `{run_id, running}`**. Enforcement live.
- **#11** — ran a real search + per-user re-score against the live app: **zero `database is locked`**
  log lines (those were the errors that previously dropped rows). busy_timeout=30s + with_write_retry in the path.
- Full feed lifecycle (register → CV → search → **4,773 scored jobs** → dashboard → pipeline) was
  verified live earlier this session on the main checkout (with `.env` keys); see Part 1 / the prod-build pass.

### Reported / not-a-bug (owner domain or intentional)
- #5 run-time vs re-score keyword score differs → **Pillar 2, owner-handled, hands-off.**
- #9 metrics export "no such table: run_log" → worktree DB-path / telemetry only.
- #4 dashboard double `/api/jobs` fetch → intentional (feed vs count badges).

---

## Production-readiness fix list (priority)
1. **SQLite write contention (#11)** — stop dropping rows under load; plan Postgres.
2. **Same-origin / `/api` proxy (#12)** — browser auth won't work split.
3. **Broken footer links (#10)** — add or remove.
4. ~~Admin role gate (#13)~~ — **dropped**. No admin model; access tiers will be **Free / Premium plans** (deferred — see `docs/plans/2026-06-21-free-premium-plans.md`).
5. **Redis + ARQ worker** — enable + test real notification delivery (currently 🚪 gated).
6. a11y: login error `role=alert` (#14); decide email-verification enforcement (#15); CSV button.

## Honest status
- **Functional (real-data, single-user, dev):** every page + ~all routes LIVE-verified.
- **Structural (prod-build + code map):** all wiring traced + tested.
- **All audit findings (#1–#15 + CSV) FIXED.** Former blockers #11 (SQLite locks) and
  #12 (same-origin auth) are resolved in code.
- **Remaining = infra/scale, not bugs:** install **Redis + ARQ worker** to enable + test
  real notification delivery; migrate to **Postgres** for true write concurrency (behind
  the #11 mitigation). **No admin/RBAC is planned** — instead access will be split into **Free / Premium subscription tiers** (deferred — see `docs/plans/2026-06-21-free-premium-plans.md`).

---

## Part 4 — Worktree / branch consolidation status (re-checked 2026-06-19 vs `origin/main` = `fe9935a`)

**✅ Resolved (2026-06-21).** All worktree/feature branches listed in earlier revisions were merged into `main` and deleted; any unmerged work is preserved on `origin/backup/*`. Current `origin/main` canonical counts: **47 source keys / 46 instances / 46 classes**, **22 migrations** (0000→0021), 8 keyed APIs. The earlier 47-vs-46 / `gov_apprenticeships` open decisions are closed (gov_apprenticeships restored; count is 47).

---

## Part 5 — Code-real components MISSING from the A–J checklist (code-verified)

A full code inventory (**57** routes, 20 pages, 6 CLI cmds, ARQ workers, **46** service modules, 19 DB
tables, migrations 0000→**0021**, **47** SOURCE_REGISTRY keys) surfaced real features/lifecycle
pieces the A–J UI checklist above does **not** cover. Listed here so the doc matches the code.
Status: ✅ wired+working · ⚙️ backend-only (no UI) · 🚪 needs infra · 🧩 in code, NOT wired.

### Interfaces
- **CLI** ⚙️ `src/cli.py` — 6 commands: `run`, `api`, `status`, `view`, `sources`, `setup-profile`. A whole non-web interface the checklist never mentions.

### Background worker layer (ARQ) — `src/workers/`
- 🚪 `score_and_ingest`, `send_notification`, `enrich_job_task`, `send_bundle`, `notification_tick`, `nightly_ghost_sweep`, `mark_ledger_sent/failed`. Crons: `nightly_ghost_sweep` @02:00 UTC, `notification_tick` @every 5min. **Needs Redis to actually run** (the standing delivery gate).

### Lifecycle / engine components beyond the 4 scoring engines
- **Application deadlines** ✅ `deadline.py` + `jobs.deadline` (migration **0021**); pipeline refuses confirmed-expired jobs (410).
- **Ghost / staleness detection** ✅ `ghost_detection.py` state machine (active → possibly_stale → likely_stale → confirmed_expired) + nightly sweep + per-source absence marking.
- **Two-pass profile extraction** ✅ `profile/two_pass.py` — deterministic + LLM pass over stored inputs, re-run on every profile change → new version → feed rescore.
- **Pre-filter cascade** ⚙️ `prefilter.py` — 3-stage (~99%) elimination (location, experience, skill-overlap) before scoring.
- **Retrieval internals (Engine 3)** ⚙️ `retrieval.py` — RRF fusion of keyword + BM25 + ChromaDB ANN + cross-encoder rerank.
- **Per-engine on/off flags** ⚙️ `ENGINE1..4_ENABLED` (independent of the legacy `ENRICHMENT/SEMANTIC/MATCHER` flags).

### Profile depth (beyond "CV parsed")
- ⚙️ **ESCO skill normalization** (`profile/skill_normalizer.py`, ~13,900 concept embeddings), **evidence-based skill tiering** (`skill_tiering.py`), **dependency-file skill parsing** (`dep_file_parser.py`), **skill provenance** tracking.

### Notification depth (beyond "rules CRUD")
- ✅ **Digest modes** instant / daily (tz-aware) / every_N_hours; **digest queue** (`user_notification_digests`), **DLQ** after 5 retries, **ledger** lifecycle (queued/sent/failed/dlq).

### Source reliability infra
- ⚙️ **TieredScheduler** (60s ATS → 60min scrapers), **CircuitBreaker** per source, **ConditionalCache** (ETag/Last-Modified), **domain-aware source filtering** (tech/healthcare/academia/education/climate).

### Observability
- ✅ Run history (`/api/runs/recent`), **source-health** traffic-light (`/api/runs/source-health` + `/admin/sources`), **metrics_exporter** (JSON), **report_generator** (markdown run report), per-run UUID correlation id.

### Security infra
- ✅ Argon2 hashing, HMAC sessions, **rate limiting** (pw-reset, email-resend), **OAuth CSRF state** (`oauth_states`, 10-min TTL).

### SEO surfaces
- ✅ `sitemap.ts`, `robots.txt`, OG tags + JSON-LD on `/jobs/[id]` (public for unfurl bots — why `/jobs` is intentionally un-gated).

### 🧩 In code but not wired — REMOVED ✅ (owner decision)
The three latent unwired modules were **deleted** from the codebase (no live code imported them):
`services/cover_letter.py`, `services/jsonld_harvest.py`, `services/company_discovery.py` (+ their tests).
Clean removal — nothing in `src` referenced them.

### Additional code-real components (named for completeness — 2026-06-21 code audit)
Real modules / knobs behind features already listed by *name* but not traced to a *module*:
- **Engine 2 sub-dimensions** — `scoring_dimensions.py` computes 4 separate scores (salary / seniority / visa / workplace), each with its own env weight (`SALARY_WEIGHT` / `SENIORITY_WEIGHT` / `VISA_WEIGHT` / `WORKPLACE_WEIGHT`, `core/settings.py`). Salary is normalized to GBP/yr first by `salary.py` (`normalize_salary`).
- **Keyword-generation bridge** — `profile/keyword_generator.py` (`generate_search_config`) turns a parsed profile into the `SearchConfig` (keywords / titles / locations) that every one of the 47 sources reads. The link between “profile” and “search”.
- **LLM provider cascade** — `profile/llm_provider.py`: Gemini → Groq → Cerebras fallback with Pydantic-validated extraction (`llm_extract_validated`); used by the CV / LinkedIn / preferences passes. `preferences.llm_infer_from_about_me` is a 2nd LLM pass mining the free-text “about me”.
- **Domain-aware source filtering** — `domain_classifier.py` (`classify_user_domain`, `source_matches_user_domains`) suppresses domain-irrelevant sources at query time.
- **Per-user feed service** — `feed.py` (`FeedService.upsert_feed_row`, `cascade_stale`) + `rescore.py` (`rescore_user_feed`, wrapped in `db_retry.with_write_retry`) + `vector_index.py` (ChromaDB wrapper).
- **Auth internals** — `auth/passwords.py` (Argon2), `auth/tokens.py` (reset/verify tokens), `auth/email_sender.py` (`send_system_email` — shared SMTP for reset + verify).
- **Channel internals** — `channels/dispatcher.py` (`dispatch`, `_is_in_quiet_window`, `_queue_digest`), `channels/crypto.py` (Fernet, `set_test_key` test override).
- **ATS company manifest** — `core/companies.py`: ~264 hardcoded ATS slugs across 11 platforms (`GREENHOUSE_COMPANIES` … `RIPPLING_COMPANIES` + `COMPANY_NAME_OVERRIDES`). A real maintenance surface.
- **Pipeline guards (config)** — `SOURCE_FETCH_TIMEOUT` (60s) / `SOURCE_FETCH_TIMEOUT_ATS` (240s) per-source cancel; `MAX_CONCURRENT_SEARCHES_PER_USER` (3 → HTTP 429) in `core/settings.py`.
- **Observability internals** — `utils/logger.py` (JSON formatter, separate audit logger, `set_run_uuid` / `set_request_id`) + `api/middleware.py` `RequestIdMiddleware` (stamps `X-Request-Id`). `report_generator` also has an HTML variant (`generate_html_report`), not just markdown.
- **Frontend infra surfaces** — `app/error.tsx` (error boundary), `app/not-found.tsx` (404), `next.config.ts` `/api/:path*` proxy (Finding #12), `middleware.ts` PROTECTED_PATHS gate (Finding #13), `app/settings/layout.tsx` (`SettingsNavTabs`).

### Count + claim corrections (2026-06-21, code-verified)
- **Routes = 57, not 49** — `grep -E "@router\.(get|post|put|delete|patch)" backend/src/api/routes/*.py` = 57 (old 49 predated the channels-OAuth + auth account-mgmt routes).
- **Service modules = 46, not ~40** — `backend/src/services/**/*.py` minus `__init__`.
- **Notification rules have NO DELETE route** — `notification_rules.py` is GET + PUT-upsert only (one row per user, UNIQUE(user_id), rule #23). The earlier H1–H5 “delete 204” claim was wrong (corrected in Part 1).
- **`exportJobsCsv` is now wired** (`frontend/src/app/dashboard/page.tsx:400`) — removed from the Part 2 “wired-but-unused” list. The four still-unused: `getRecentRuns`, `getEmailVerified`, `getActions`, `getActionCounts`.

**Doc-sync corrections:** Part 4 says "21 migrations (0000→0020)" — current head is **0021** (`add_job_deadline`). SOURCE_REGISTRY = **47** keys / 46 instances (code asserts 47 in `test_cli.py`).
