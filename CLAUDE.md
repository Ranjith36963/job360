# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## How to talk to me (STRICT — always follow)

**Explain everything in simple, plain English.** This is a strict rule, never skip it.

- Use short sentences and easy words. Imagine explaining to a smart friend who is not a coding expert.
- Avoid jargon. If a technical word is needed, say what it means in plain words right after it (one short line).
- No long walls of text. Get to the point: what happened, what I did, what's next.
- When I ask for something, show me the result in plain words first, then the details if needed.

## Quick Orientation

If you have time for nothing else: **read this section, the Hard Rules index below, and the Commands section**. Then dive into the task.

- **Branch:** `main`. Multi-commit work demands a preflight: verify `git branch --show-current`, clean tree, and `git fetch origin <branch>` HEAD alignment. Halt and surface to the user on divergence — never silent rebase.
- **Canonical pre-commit verification:** `cd backend && python -m pytest -q -p no:randomly` (~1,720 collected offline, 2 live tests deselected — defer to the runtime collected count). `test_main.py` is included: it was rehabbed offline in the M8 batch (JobSpy stubbed via autouse fixture) and runs in ~8 s.
- **State of play:** Step 3 (control-surface batch) merged at origin/main `7194d0e`. Post-Step-3, the funnel→judge matcher batch (a925f42..d801f78, migration 0017) landed on branch `fix/per-user-search-and-scoring-gate`, adding the LLM judge engine (engine #4). An autonomous maintenance loop (worker/integrator/scout/health agents, missions in `docs/maintenance/MISSIONS.md`) is running. The app is LIVE on Railway. Step 4 (ops hardening) is **partially shipped** by the 2026-07-09 top-5-leverage batch (stacked PRs #27→#31: CI now green, request-timeout middleware, healthcheck fixes, backup runbook, plus the tp-final extraction rescue). See `STATUS.md` for current phase + carry-overs; see `docs/IMPLEMENTATION_LOG.md` for the batch-by-batch history.
- **Two deployables:** `backend/` (Python 3.9+, FastAPI, async SQLite) and `frontend/` (Next.js 16, React 19). Runtime data lives in `backend/data/`.
- **What surprises new sessions:** `SOURCE_REGISTRY` has 47 entries but only 46 unique source classes (`indeed` and `glassdoor` both alias `JobSpySource`). Heavy deps must be lazy-imported (rules #11 + #16). Next.js 16 broke `params` to async (rule #22). Adding/removing a source touches **five** files, not four (rule #13).

## Hard Rules (load-bearing, numbered, do not violate)

Rules are reference material — keep them in one place. Long-form context for each lives in the phase sections at the bottom of this file.

### Schema + data integrity

1. **Never touch `normalized_key()` in `models.py`** without verifying the deduplicator and DB UNIQUE constraint still work. Wrong normalization = duplicate rows or missed dedup.
3. **Never touch database purge logic** (`purge_old_jobs` in `database.py`) without explicit confirmation. Wrong threshold = data loss.
10. **Never INSERT into `jobs` with `user_id` or `tenant_id`** — `jobs` is the shared catalog by design (blueprint §3). Per-user state lives in `user_feed`, `user_actions`, `applications`.
17. **`job_enrichment` and `job_embeddings` must NOT gain `user_id`** — same rationale as #10. Per-user scoring against the enrichment happens at read time in `JobScorer(..., user_preferences=..., enrichment_lookup=...)`.

### Sources

2. **Never change `BaseJobSource`** (constructor, properties, retry, `_get_json`/`_post_json`/`_get_text`) without checking all 46 source files that inherit from it.
8. **When adding/removing sources: update the FIVE load-bearing surfaces** — `SOURCE_REGISTRY` dict, `_build_sources()` list, `RATE_LIMITS` dict, `tests/test_cli.py` (`len == N` + expected set), AND `tests/test_api.py` (rule #13 below). Current count: **47**.
13. **The fifth surface (`tests/test_api.py`) has hardcoded `== N` checks** inside `test_sources_returns_*` + `test_status_returns_counts` + `test_full_api_workflow`. Rotating the count requires all five to move together.
14. **Conditional fetch is opt-in.** Sources whose upstream honours ETag/Last-Modified call `self._get_json_conditional(url)`; everyone else stays on `self._get_json(url)`. Don't pollute the cache with un-validatable entries.
15. **New sources MUST set `.category`** to one of: `"ats"`, `"rss"`, `"keyed_api"`, `"free_json"`, `"scrapers"`, `"other"` — or add a `NAME_TIER[source.name]` override in `scheduler.py`. Untagged sources fall to the 60-min default.

### Heavy imports

11. **Never import `apprise` at module top level.** ~30 MB of deps. Lazy-import inside the function that uses it (see `dispatcher._get_apprise_cls`).
16. **Same rule extends to `sentence_transformers`, `chromadb`, `rapidfuzz`, `sklearn`** — all lazy-imported inside functions. Top-level imports cost 150 ms – 2 s per pytest collection.

### Auth + multi-tenant routes

12. **Every per-user FastAPI route MUST `Depends(require_user)`** and scope queries by `user.id`. Never accept `user_id` from URL/body — trivial IDOR.
25. **Per-user mutating routes MUST scope by `user.id`** (extends #12). Step 3's reviewer R-1..R-3 caught 3 IDOR violations where `user_id` came from path/body. Pattern: derive from session cookie, never accept as parameter.
26. **Account-mgmt routes (password/email/delete) MUST verify current password BEFORE the mutation, then invalidate the session cookie** (forces re-login). Pattern: `verify_password(...)` → mutation → `response.delete_cookie("job360_session")`.

### Scoring + enrichment

9. **Scoring changes require running `test_scorer.py` AND `test_profile.py`.** 53 + 55 tests cover edge cases.
18. **Pillar 2 flags default off.** When `ENRICHMENT_ENABLED` / `SEMANTIC_ENABLED` are false, behaviour must **exactly** match pre-Pillar-2 — no implicit semantic queries, no LLM calls. Test with both flags OFF.
19. **`JobScorer` legacy default = 4-component formula.** New 7-dim scoring activates only when `JobScorer(config, user_preferences=..., enrichment_lookup=...)` gets all three kwargs. Don't flip defaults silently.
20. **Multi-dim scoring requires both `user_preferences` AND `enrichment_lookup`** — pass both or neither. Passing only `user_preferences` produces silent zeros for the new dim scorers; the combined `match_score` looks legacy but the dim columns mislead.
27. **Multi-dim weights total 30 on top of the legacy 100; the clamp to [0, 100] is load-bearing.** `SALARY_WEIGHT` (10) + `SENIORITY_WEIGHT` (8) + `VISA_WEIGHT` (6) + `WORKPLACE_WEIGHT` (6) are added to the legacy 4-component sum, so the raw max is **130**. The final `match_score` is clamped to `[0, 100]` — never remove the clamp.

### Extraction must be data-driven (no hardcoded keyword lists)

28. **STRICT — ZERO hardcoded skill/keyword lists in profile extraction (`src/services/profile/`).** Extraction is **data-driven (ESCO ontology, loaded from `data/esco/`) + LLM** only. Hand-typed skill maps overfit one CV, are brittle (new skill = code edit), and inflate eval scores dishonestly. **Banned in code:** any `*_SKILL_TERMS` / `*_TO_SKILL` / skill-keyword dict/denylist. Offenders being removed: `cv_parser._PROSE_SKILL_TERMS` / `_COMMON_TOOL_TERMS`, `github_enricher._DESC_SKILL_TERMS` / `LANGUAGE_TO_SKILL` / `TOPIC_TO_SKILL` / `_DEV_TOOLING_DENYLIST`, `dependency_map.py`, `core/skill_synonyms.py`. Deterministic pass = STRUCTURE only (sections/bullets/exact tokens) + ESCO-vocabulary match; semantic prose→skill mapping belongs to the LLM (prompt-steering is fine, that's not hardcoding). If you find a keyword list in extraction, remove it and route through ESCO data or the LLM.

### Notifications

23. **Notifications are ONE rule row per user** (`notification_rules`, `UNIQUE(user_id)` — migration 0020). The rule governs ALL of the user's enabled channels at once (no per-channel rules). Dispatch loads the single rule, then converts UTC `now` to the user's `users.timezone` (IANA) via `zoneinfo.ZoneInfo` before comparing against `quiet_hours_start/end` HH:MM strings. Skipping the timezone conversion silently leaks notifications during BST/DST transitions. Use stdlib `zoneinfo` — not `pytz`.
24. **`notify_mode` is `instant` | `daily` | `every_n_hours`.** `instant` sends inline from the worker the moment a job matches; `daily`/`every_n_hours` (and any send caught inside quiet hours) queue into `user_notification_digests`. The `notification_tick` ARQ cron (every 5 min) decides when a bundle is due (daily clock vs `interval_hours` elapsed vs quiet-hours flush) and enqueues `send_bundle`, which drains the queue, dispatches with `force=True` (bypassing the mode/quiet gate), records the ledger, marks rows `sent` on success / leaves them on failure / `dlq` after 5 retries, and stamps `last_sent_at`. Tests for new dispatch paths must cover all three modes AND both quiet-hours states. The legacy global `.env`-webhook path was removed — the only path is worker/tick → `dispatcher.dispatch()` → Apprise → `notification_ledger`.

### Process + verification

4. **Always mock HTTP in tests** with `aioresponses`. Never live HTTP. The suite must run offline.
5. **Always run the relevant test suite** after a change.
6. **Read a file fully before editing.** Understand existing logic, imports, dependents.
7. **Check if something exists before creating it.**
21. **Value-presence > schema-presence for new engine-side fields.** Schema-presence tests (`assert "field" in body`) pass against `field: int = 0` defaults and serializers that never read the column. Add a value-presence test that runs a real input through end-to-end and asserts the value is non-zero / non-default. Pattern: `tests/test_database.py::test_dim_columns_round_trip` + `tests/test_api.py::test_jobs_response_includes_score_dim_breakdown`.
22. **Frontend code touching Next.js App Router patterns MUST consult Context7 docs first.** Training data for Next.js 14–15 is **not reliable** for 16. Breaking changes: `params` is `Promise<{...}>` and must be `await`ed; `"use client"` on `page.tsx` silently disables `generateMetadata`. Run `mcp__plugin_context7_context7__query-docs` for `next.js` and read `frontend/node_modules/next/dist/docs/` before any App Router work.

### Branch hygiene

- **Multi-commit batch preflight (saved-memory-driven):** verify branch + clean tree + `git fetch origin <branch>` HEAD alignment before any fix or implementation batch lands. Halt on divergence — do not silently rebase or merge.

## Common Gotchas

- **`test_main.py` is now offline and fast** (~8 s, 14 tests). The M8 batch stubbed JobSpy (`fetch_jobs → []` via autouse fixture) and patched `load_profile`. It is part of the canonical run — do NOT add `--ignore=tests/test_main.py` back.
- **`indeed` and `glassdoor` registry keys both → `JobSpySource`.** 47 registry entries, 46 unique classes. Test assertions treat 47 as authoritative.
- **`teaching_vacancies` lives in `apis_free/` but declares `category="rss"`** for the 15-min scheduler tier. Folder location ≠ scheduler tier. (`gov_apprenticeships` is now a `keyed_api` source restored 2026-06-16 on the DfE Display Advert API v2 — it is NOT in the `rss` tier.)
- **Pillar 2 toggles default off.** Don't assume embeddings or LLM enrichment runs by default — they don't.
- **Heavy deps lazy-imported.** Don't add a top-level `import sentence_transformers` even "just for typing" — see rules #11 + #16.
- **Migrations auto-apply on FastAPI boot** via `lifespan` in `src/api/dependencies.py`. The CLI `python -m migrations.runner up` is for non-API contexts.

## Project Overview

Job360 is an automated UK job search system supporting **any professional domain**. It aggregates jobs from 47 sources (via `SOURCE_REGISTRY` in `src/main.py`), scores them 0-100 against a user profile, deduplicates across sources, and delivers results via CLI, email, Slack, Discord, CSV, and a Next.js frontend (backed by FastAPI). Users can personalise searches by providing a CV (PDF/DOCX), a LinkedIn profile PDF (profile → More → Save to PDF), and/or GitHub username. When a user profile exists (`data/user_profile.json`), keywords are generated dynamically from CV + preferences + LinkedIn + GitHub via `SearchConfig`. Without a profile, the default keyword lists are empty (emptied 2026-04-09, commit `3ba1342`) — a profile is required for meaningful results.

## Tech Stack

| Package | Version | Purpose |
|---------|---------|---------|
| aiohttp | >=3.9.0 | Async HTTP client for source fetching |
| aiosqlite | >=0.19.0 | Async SQLite for job storage |
| python-dotenv | >=1.0.0 | .env file loading |
| jinja2 | >=3.1.0 | HTML report templates |
| click | >=8.1.0 | CLI framework |
| pandas | >=2.0.0 | DataFrame support for python-jobspy (Indeed/Glassdoor) |
| pdfplumber | >=0.10.0 | PDF text extraction (CV parsing) |
| python-docx | >=1.1.0 | DOCX text extraction (CV parsing) |
| rich | >=13.0.0 | Terminal table rendering |
| humanize | >=4.9.0 | Relative time formatting |
| fastapi | >=0.115.0 | API server for Next.js frontend |
| uvicorn[standard] | >=0.30.0 | ASGI server for FastAPI |
| python-multipart | >=0.0.9 | File upload support |
| httpx | >=0.27.0 | Async HTTP client (used by API + LLM providers) |
| google-generativeai / groq / cerebras-cloud-sdk | latest | Multi-provider LLM client for CV parsing |
| argon2-cffi / itsdangerous / cryptography | latest | Auth + signed sessions + Fernet (Batch 2) |
| apprise | >=1.9.9 | Multi-channel notification dispatch (Batch 2; lazy-imported) |
| rapidfuzz / scikit-learn | latest | Pillar 2 dedup layers 2–3 (lazy-imported) |
| sentence-transformers / numpy / chromadb | `[semantic]` extra (~300 MB) | Pillar 2 embeddings + ChromaDB (lazy-imported, opt-in) |

**Dev/test extras** (`[dev]`): pytest, pytest-asyncio, aioresponses, fpdf2, pytest-randomly, ruff, pre-commit. **Optional**: `python-jobspy` (Indeed/Glassdoor — gracefully skipped if not installed). **Python:** 3.9+ required.

## Commands

All backend commands run from `backend/`. Frontend commands run from `frontend/`.

```bash
# Setup (from project root)
bash setup.sh                  # Creates venv, installs deps, validates .env
source venv/bin/activate       # Activate virtualenv (Linux/Mac)

# Backend — all commands below run from backend/
cd backend

# API server
python main.py                                      # Start FastAPI on :8000
uvicorn main:app --host 0.0.0.0 --port 8000         # Production-style
python -m src.cli api --port 3001 --host 0.0.0.0    # Custom host/port

# Pipeline
python -m src.cli run                               # Full pipeline (46 instances)
python -m src.cli run --source arbeitnow            # Single source
python -m src.cli run --dry-run --log-level DEBUG    # Dry run with debug
python -m src.cli run --no-email                     # Skip notifications

# Profile
python -m src.cli setup-profile --cv path/to/cv.pdf
python -m src.cli setup-profile --cv cv.pdf --linkedin linkedin.pdf
python -m src.cli setup-profile --cv cv.pdf --github username

# Other CLI
python -m src.cli status       # Last run stats
python -m src.cli sources      # List all 47 sources
python -m src.cli view --hours 24 --min-score 50
python -m src.cli view --visa-only

# Tests (run from backend/ — pytest picks up pyproject.toml pythonpath=["."])
python -m pytest tests/ -v                              # All tests
python -m pytest -q -p no:randomly   # Canonical fast run (test_main.py included — offline since M8)
python -m pytest tests/test_scorer.py::test_name -v     # Single test

# Migrations
python -m migrations.runner up         # Apply pending
python -m migrations.runner status     # Show applied/pending
python -m migrations.runner down       # Reverse last
```

```bash
# Frontend — all commands below run from frontend/
cd ../frontend

# ⚠️ Next.js 16 — see rule #22. Training data for 14–15 is unreliable here.
# Before any App Router pattern, read frontend/node_modules/next/dist/docs/.

npm run dev                    # localhost:3000
npm run build                  # Production build
npm run lint                   # ESLint
npm run type-check             # tsc --noEmit
npm run test:unit              # Vitest
npm run test:e2e               # Playwright
```

## Architecture (high-level)

The pipeline flows: **CLI (Click)** → **Orchestrator (`src/main.py`)** → **Sources (async fetch via `asyncio.gather`)** → **Scorer** → **Deduplicator** → **SQLite DB** → **Notifications + Reports + CSV**.

> **Full directory layout, data-flow diagrams, DB schema, and dependency tables: see `ARCHITECTURE.md`.** This file holds only the high-level picture + Claude-specific guidance.

### Top-level layout

```
job360/
├── backend/
│   ├── main.py                # FastAPI uvicorn entry (thin)
│   ├── pyproject.toml         # Deps + ruff/mypy/pytest config
│   ├── data/                  # Runtime: jobs.db, user_profile.json, exports/, reports/, logs/, chroma/
│   ├── migrations/            # 22 forward+reverse SQL migration pairs (0000 → 0021) + runner.py
│   ├── src/
│   │   ├── main.py            # Pipeline orchestrator: run_search(), SOURCE_REGISTRY (47), _build_sources()
│   │   ├── cli.py             # Click CLI: run, api, status, sources, view, setup-profile
│   │   ├── models.py          # Job dataclass with normalized_key() for dedup
│   │   ├── api/               # FastAPI app + 11 route modules (46 endpoints, all per-user routes gated)
│   │   ├── core/              # settings, keywords, companies, skill_synonyms, fx, tenancy
│   │   ├── services/          # skill_matcher, deduplicator, scoring_dimensions, retrieval, embeddings, vector_index, job_enrichment, prefilter, scheduler, circuit_breaker, conditional_cache, ghost_detection, salary, domain_classifier, feed, auth/, channels/, notifications/, profile/
│   │   ├── repositories/      # database, csv_export
│   │   ├── sources/           # 46 source files in 6 category subfolders; 47 SOURCE_REGISTRY entries
│   │   ├── workers/           # ARQ tasks + WorkerSettings (cron: nightly_ghost_sweep @02:00, notification_tick @every 5m)
│   │   └── utils/             # logger, rate_limiter, time_buckets
│   └── tests/                 # ~1,720 collected offline (defer to runtime collected count)
└── frontend/                  # Next.js 16 + React 19 + Tailwind 4 + shadcn 4
    └── src/
        ├── app/               # App Router: dashboard, jobs/[id], pipeline, profile, channels (top-level), settings/{layout,notifications,account}, notifications (ledger)
        ├── components/        # ui/ (shadcn), jobs/, profile/, pipeline/, layout/
        └── lib/               # api.ts, types.ts, queryKeys.ts, api-error.ts, utils.ts
```

### Key engine modules

- `src/main.py` — `run_search()` + `SOURCE_REGISTRY` (47 entries — `indeed` and `glassdoor` alias `JobSpySource`) + `_build_sources()`.
- `src/services/skill_matcher.py` — Scoring. Two paths: legacy `score_job()` (module-level, hard-coded keywords) and `JobScorer(config, user_preferences=None, enrichment_lookup=None).score()` (instance, dynamic + optional 7-dim per rules #19/#20). 4-component default: Title 40 / Skill 40 / Location 10 / Recency 10. Penalties: −30 negative title / −15 foreign location.
- `src/services/deduplicator.py` — 4-layer dedup (exact key → RapidFuzz → TF-IDF → embedding repost; layers 2–4 lazy-imported per rule #16; layer 4 gated on `SEMANTIC_ENABLED`).
- `src/services/scheduler.py` — `TieredScheduler` + `TIER_INTERVALS_SECONDS` (60s ATS / 5m keyed / 15m RSS / 60m scrapers). Consults `circuit_breaker.BreakerRegistry` before each tick.
- `src/services/channels/dispatcher.py` — Apprise wrapper. Consults the single per-user `notification_rules` row: score-threshold filter, timezone-aware quiet-hours hold-and-queue, mode routing (instant send vs daily/every_n_hours queue); `force=True` bypasses the gate for bundle sends (rules #23/#24).
- `src/repositories/database.py` — Async SQLite (aiosqlite), WAL, 5s busy timeout. Shared catalog: `jobs`, `run_log`, `job_enrichment`, `job_embeddings`. Per-user: `users`, `sessions`, `user_feed`, `user_actions`, `applications`, `notification_ledger`, `user_channels`, `user_profiles`, `user_profile_versions`, `notification_rules`, `user_notification_digests`, `application_stage_history`. Auto-purges shared `jobs` >30 days old via `purge_old_jobs()` (rule #3).

### Sources

All extend `BaseJobSource` in `src/sources/base.py` (3-attempt retry with exp backoff 1s/2s/4s, rate limiting via `RATE_LIMITS`, `_is_uk_or_remote` helper). Each source uses `self.relevance_keywords` / `self.job_titles` / `self.search_queries` properties — these return `SearchConfig` values when a profile is loaded, empty `keywords.py` defaults otherwise (lists emptied 2026-04-09 — a profile is effectively required). Categories:

- **Keyed APIs** (8): Reed, Adzuna, JSearch, Jooble, Google Jobs (SerpApi), Careerjet, Findwork, gov_apprenticeships (DfE Display Advert API v2) — skip gracefully when API key empty.
- **Free JSON APIs** (9 in `apis_free/` with `category="free_json"`): Arbeitnow, RemoteOK, Jobicy, Himalayas, Remotive, DevITjobs, Landing.jobs, AIJobs.net, HN Jobs.
- **ATS boards** (11): Greenhouse, Lever, Workable, Ashby, SmartRecruiters, Pinpoint, Recruitee, Workday, Personio, SuccessFactors, Rippling.
- **RSS/XML feeds** (9 with `category="rss"` — 8 in `feeds/` + 1 in `apis_free/`): jobs.ac.uk, NHS Jobs, NHS Jobs XML, WorkAnywhere, WeWorkRemotely, RealWorkFromAnywhere, BioSpace, University Jobs, Teaching Vacancies (apis_free).
- **HTML scrapers** (5): LinkedIn, Climatebase, 80000Hours (Algolia), BCS Jobs, AIJobs AI.
- **Other** (4 unique classes / 5 registry entries): JobSpy (`indeed` + `glassdoor` keys), HackerNews, TheMuse, NoFluffJobs.

Dropped in Batch 3: `yc_companies`, `nomis`, `findajob`. Dropped 2026-06 (M6 rotation): `jobtensor`, `comeet`, `aijobs_global` — all upstream-dead. `gov_apprenticeships` was dropped in M6 but **restored 2026-06-16** on the DfE Display Advert API v2 (keyed; env `DFE_APPRENTICESHIPS_API_KEY`). Sources with custom queries (JSearch, LinkedIn, NHS Jobs) check `self.search_queries` before falling back to hard-coded query lists.

## Environment

- Python 3.9+ required.
- Dependencies installed via `pip install -e ".[dev]"` from `backend/`.
- `.env` in repo root for API keys + webhooks (see `.env.example`); 39 of 47 sources work without any keys.
- Data outputs go to `backend/data/` (gitignored): `exports/`, `reports/`, `logs/`, `jobs.db`, `user_profile.json`, `chroma/`.

### Environment variables

| Variable | Required | Used by |
|----------|----------|---------|
| `REED_API_KEY` / `ADZUNA_APP_ID` + `ADZUNA_APP_KEY` / `JSEARCH_API_KEY` / `JOOBLE_API_KEY` / `SERPAPI_KEY` / `CAREERJET_AFFID` / `FINDWORK_API_KEY` / `DFE_APPRENTICESHIPS_API_KEY` | No | Keyed API sources (skip on empty) |
| `GITHUB_TOKEN` | No | Higher GitHub API rate limit (5000/hr vs 60/hr) |
| `SMTP_EMAIL` + `SMTP_PASSWORD` + `NOTIFY_EMAIL` / `SLACK_WEBHOOK_URL` / `DISCORD_WEBHOOK_URL` | No | Built-in notification channels |
| `TARGET_SALARY_MIN` / `TARGET_SALARY_MAX` | No | Salary range tiebreaker (default 40k–120k) |
| `SESSION_SECRET` | Yes in prod | `itsdangerous` HMAC for session cookies |
| `CHANNEL_ENCRYPTION_KEY` | Yes in prod | Fernet encryption of channel credentials |
| `FRONTEND_ORIGIN` | No (default `http://localhost:3000`) | CORS allow-list (comma-sep) |
| `REDIS_URL` | Only for ARQ worker | ARQ broker (default `redis://localhost:6379`) |
| `ENGINE1_ENABLED` / `ENGINE2_ENABLED` / `ENGINE3_ENABLED` / `ENGINE4_ENABLED` | No (E1 default `true`; E2/E3/E4 default to their legacy flag) | **Independent per-engine switches** — any combo. Effective gate is `ENGINEx_ENABLED OR <legacy flag>` (E2↔`ENRICHMENT_ENABLED`, E3↔`SEMANTIC_ENABLED`, E4↔`MATCHER_ENABLED`). E1 (keyword) had no prior flag. |
| `ENRICHMENT_ENABLED` / `SEMANTIC_ENABLED` | No (default `false`) | Pillar 2 opt-in toggles (legacy gates for E2 / E3) |
| `MIN_TITLE_GATE` / `MIN_SKILL_GATE` | No (default `0.15` / `0.15`) | Pillar 2.2 gate thresholds |
| `SALARY_WEIGHT` / `SENIORITY_WEIGHT` / `VISA_WEIGHT` / `WORKPLACE_WEIGHT` | No (defaults 10/8/6/6) | Pillar 2.9 dimension weights |
| `MATCHER_ENABLED` | No (default `false`) | LLM judge (engine #4) opt-in toggle |
| `MATCHER_THRESHOLD` | No (default `30`) | Min keyword score for a job to be judged |
| `MATCHER_MAX_JOBS` | No (default `30`) | Max jobs per user per run sent to the judge |
| `SOURCE_FETCH_TIMEOUT` | No (default `60`) | Per-source fetch ceiling in seconds |
| `SOURCE_FETCH_TIMEOUT_ATS` | No (default `240`) | ATS category fetch ceiling in seconds |
| `API_REQUEST_TIMEOUT_SECONDS` | No (default `60`) | **Inbound** API request ceiling — `RequestTimeoutMiddleware` returns `504`. `/api/tailor` + `/api/profile` (LLM-heavy) are exempt. Distinct from the outbound `SOURCE_FETCH_TIMEOUT*` knobs. |
| `OPENAI_API_KEY` / `OPENAI_MODEL` | No (model default `gpt-4o-mini`) | CV-extraction PRIMARY provider (paid; removes free-tier 429 silent-empties). Falls through to Gemini→Groq→Cerebras when empty. |
| `RESEND_API_KEY` / `SMTP_FROM` | No | Transactional email (magic-link/verification). `email_sender.py` prefers Resend when `RESEND_API_KEY` set, else SMTP. `SMTP_FROM` must be on a Resend-verified domain or the send 403s. |
| `DATABASE_URL` | Yes in prod (default local dev DSN) | Postgres connection string — the app runs Postgres-only via the `pg.py` shim. |
| `SENTRY_DSN` | No | Sentry error tracking; empty disables it. |
| `BACKUP_DIR` / `BACKUP_KEEP` | No (defaults `./backups` / `14`) | Manual `scripts/backup_db.py` output dir + retention. See `docs/RUNBOOK-backups.md` (no automated CI backup — public repo). |

## Important Patterns

- **Adding a new job source:** Class extends `BaseJobSource`, implements `async fetch_jobs() -> list[Job]`, sets `category` (rule #15). Use `self.relevance_keywords` / `self.job_titles` (not direct imports). If custom `__init__`, accept `search_config=None` and pass through to `super().__init__(session, search_config=search_config)`. Update **five surfaces** per rule #8/#13. Add mocked `aioresponses` tests.
- **Adding a notification channel:** Implement `NotificationChannel` ABC, register in `get_all_channels()` in `src/services/notifications/base.py`. For multi-channel routing via Apprise, work in `src/services/channels/dispatcher.py` instead — and ensure rules #23 + #24 are respected.
- **Keyed source pattern:** Accept `api_key`, return `[]` early with info-log if empty, pass `search_config` through.
- **ATS source pattern:** Accept `companies` list and `search_config=None`; iterate slugs from `companies.py`.
- **RSS/XML source pattern:** `_get_text()` → parse with stdlib `xml.etree.ElementTree`. Consider `_get_json_conditional` if upstream honours ETag (rule #14).
- **HTML scraper pattern:** `_get_text()` → regex parse. Fragile by nature — tag scrapers in `STATUS.md`'s "fragile/risky" table.

## Testing

**~1,720 collected offline** (2 live tests deselected — defer to the runtime collected count), 0 failing (bash-only `setup.sh` / `cron_run.sh` tests skip on Windows). Shared fixtures in `tests/conftest.py`. All HTTP mocked with `aioresponses`. `pytest-asyncio` for async tests.

`make test` runs the full suite including `test_main.py` (rehabbed offline in M8 — JobSpy stubbed, runs in ~8 s).

The per-test-file breakdown is in `STATUS.md` and grows with each batch — always defer to the runtime collected count, not the historical breakdown.

## Phase summaries (current invariants only — full history in `docs/IMPLEMENTATION_LOG.md`)

The codebase has accumulated 4 major phases of work. Each section here gives **only the invariants that future Claude needs to respect**. Long-form history (what shipped when, reviewer findings, deferred items, decision rationales) lives in `docs/IMPLEMENTATION_LOG.md` and the per-step plan files under `docs/`.

### Phase 1–2.5 (legacy, profile system + reliability)

Done in commits `0d3ef72` → `a814ae8`. Outcome: dynamic per-user profile (CV + LinkedIn PDF + GitHub) replaces hard-coded AI/ML keywords; clean-architecture restructure (`backend/src/`, `frontend/src/`, phase-4 rename of `filters/` → `services/`, `notifications/` + `profile/` → `services/{notifications,profile}/`, `storage/` → `repositories/`, `config/` → `core/`); LLM-only CV parsing via Gemini/Groq/Cerebras fallback chain.

### Pillar 3 Batch 2 (multi-user delivery layer)

Adds: auth (`users`, `sessions`), per-tenant isolation (`user_actions` / `applications` rebuilt with `user_id`), SSOT `user_feed`, `notification_ledger` (per-channel idempotency), Fernet-encrypted `user_channels`, ARQ worker, Apprise dispatcher (lazy-imported per rule #11), `_schema_migrations` registry. Pre-Batch-2 rows backfilled to `DEFAULT_TENANT_ID` (`00000000-0000-0000-0000-000000000001`). The `jobs` table remains shared catalog (rule #10). Rules added: #10 / #11 / #12.

### Pillar 3 Batch 3 (tiered polling + source expansion)

Adds: `TieredScheduler` (60s ATS / 5m keyed / 15m RSS / 60m scrapers), per-source `CircuitBreaker` (5-failure threshold, 300s cooldown), FIFO `ConditionalCache` for ETag/Last-Modified validators, +5 sources (teaching_vacancies, gov_apprenticeships, nhs_jobs_xml, rippling, comeet), −3 drops (yc_companies, nomis, findajob), ATS slug catalog 104 → 268, source count 48 → 50. Rules added: #13 / #14 / #15. M6 rotation (2026-06): −4 upstream-dead sources (jobtensor, comeet, gov_apprenticeships, aijobs_global), source count 50 → 46. gov_apprenticeships restored 2026-06-16 on the DfE Display Advert API v2 (keyed), source count 46 → 47.

### Pillar 2 (search & match engine upgrade)

Adds: `skill_synonyms.py` (493-entry alias dict), `fx.py` (18-currency → GBP), `domain_classifier.py` (source routing), `salary.py` (cadence normalizer), 4 new dim scorers (`scoring_dimensions.py`), `JobEnrichment` Pydantic schema (18 fields, 8 enums), `embeddings.py` + `vector_index.py` (ChromaDB-backed), `retrieval.py` (RRF + cross-encoder rerank), 4-layer `deduplicator.py`. Tables: `job_enrichment` (migration 0008), `job_embeddings` (migration 0009) — both shared catalog. Toggles: `ENRICHMENT_ENABLED`, `SEMANTIC_ENABLED` (both default off, rule #18). Rules added: #16 / #17 / #18 / #19 / #20 / #21 / #22.

### Step 3 (control-surface batch — closed at origin/main `7194d0e` PR #9)

Adds: 8 new endpoints (account-mgmt × 3, notification-rules × 4, runs × 1, plus `/jobs/{id}/duplicates`, `/profile/versions/{a}/diff/{b}`, `/pipeline/{id}/{timeline,notes}`); migrations 0012 (`notification_rules` + `users.timezone`), 0013 (`user_notification_digests`), 0014 (`applications.{last_advanced_at,interview_dates,notes_history}` + `application_stage_history`); dispatcher rule consultation with timezone-aware quiet hours; ARQ periodic tasks `send_daily_digest` + `nightly_ghost_sweep`; 5 new frontend pages (`/settings/{layout,page,notifications,account}`, `/notifications`); KanbanBoard polish (timeline drawer, notes editor, filter panel, confirmation dialogs). Reviewer pass closed R-1..R-7. Rules added: #23 / #24 / #25 / #26.

**Step 3 carry-overs — all DONE/shipped:** RHF + zod form validation (V-01..V-03) ✓, CV upload size cap + MIME allowlist 413/415 (V-04) ✓, OpenAPI → TS codegen M7 (V-05) ✓, `@dnd-kit/*` keyboard a11y on KanbanBoard (C-07) ✓.

### Matcher batch (funnel → judge, post-Step-3)

Commits a925f42..d801f78, plus 76f6ca7 (Python 3.9 compat fix) and 6974bb6 (dashboard sort fix). Branch: `fix/per-user-search-and-scoring-gate`.

Adds engine #4 — the LLM judge: `services/llm_matcher.py` (`MatchVerdict`, `match_batch` with semaphore-3 concurrency, skip-existing logic); migration 0017 (`user_feed` gains `llm_fit_score`, `llm_verdict`, `llm_reason`, `llm_matched_at`); `_run_matcher_stage` pipeline stage runs after the per-user feed write in `src/main.py`; API `/api/jobs` response now exposes `llm_*` fields; `user_feed` reads rank by `COALESCE(llm_fit_score, score) DESC`; frontend dashboard shows an AI-verdict badge and sorts by the judge score.

**Rule analog (same spirit as rule #18):** `MATCHER_ENABLED` defaults `false`. With the flag off, pipeline behaviour is byte-identical to pre-batch — no extra LLM calls, no extra DB writes. With it on, only jobs whose keyword `match_score >= MATCHER_THRESHOLD` (default 30) are judged, up to `MATCHER_MAX_JOBS` (default 30) per user per run.

**Measured performance:** 18/18 jobs judged in 89.8 s (concurrency 3, Groq/Cerebras chain, zero provider failures). Judge spread 20–92 vs keyword engine 30–43 on the same corpus. Fit-bucket accuracy 10/10 on the labeled sample; correctly rejected every intern role for a senior-level profile.

**Known follow-ons (backlog):** ~~re-judge when profile changes (#8)~~ **DONE (2026-06-13, migration 0018 + rescore.py)**, judge telemetry (#9), Level-6 single-call experiment combining enrichment + judge (#10).

**Profile-version re-score (automatic, no new flags).** Every `user_feed` row is now stamped with the `user_profile_versions` ID that produced its score. Two modes:
- **Profile changes** → the API trigger in `profile.py` detects the change, clears old LLM verdicts, and re-scores the full 30-day catalog in the background against the new profile (keyword re-score always; LLM re-judge only if `MATCHER_ENABLED=true`).
- **Ordinary search** → only newly-fetched jobs are scored; existing rows keep their scores and verdicts untouched.
A job's score changes only when the profile changes — never just because time passed.

### Two-pass profile extraction (branch `feat/two-pass-profile-extraction`, 2026-06-17)

Every profile input now runs a **deterministic pass** (plain code) AND an **LLM enhance pass**, merged into one `CVData`. The two passes per input:
- **CV** — `cv_parser.deterministic_cv_fields(raw_text)` (no-LLM skills/summary) + `cv_parser.llm_cv_fields_from_text(raw_text)` (LLM).
- **LinkedIn** — deterministic header/skills split + `linkedin_parser.parse_linkedin_from_text` (LLM prose). Now stores `cv.linkedin_raw_text`.
- **GitHub** — deterministic lookup tables + NEW `github_enricher.llm_infer_github_skills(repos_brief)` (LLM reads repo prose). Stores `cv.github_repos_brief`.
- **Preferences** — plain form parse + NEW `preferences.llm_infer_from_about_me(about_me)` (LLM mines free text).

`services/profile/two_pass.py` orchestrates: `run_two_pass_extraction(profile)` re-runs both passes for all four inputs **from stored data only** (no re-upload, no GitHub re-fetch); each pass no-ops when its input/keys are absent and never raises. `reextract_and_rescore(user_id)` = load → re-extract → `save_profile(..., "two_pass_reextract")` (new version id) → `rescore_user_feed`. The `profile.py` change trigger now schedules `reextract_and_rescore` instead of bare `rescore_user_feed`.

**Rule analog (same spirit as #18):** new `CVData` fields (`linkedin_raw_text`, `github_repos_brief`, `github_llm_skills`, `about_me_inferred_skills`) need **no migration** — profiles store as a JSON blob and `storage._filter_fields` drops unknown keys, so old rows load with defaults. New skill-tiering sources: `about_me_llm` (2.0) and `github_llm` (1.5) in `skill_tiering._SOURCE_WEIGHTS`. **Cost note:** any input change re-runs all LLM passes in the background (faithful to design); gate behind a flag later if it proves expensive. M2 / the LLM judge is untouched.

## Related documentation

- **`STATUS.md`** — Current phase, what's complete/next, known issues, fragile-source table.
- **`ARCHITECTURE.md`** — Deep technical reference: full directory tree, data flow diagrams, scoring algorithm detail, DB schema, config variables.
- **`docs/IMPLEMENTATION_LOG.md`** — Append-only batch-by-batch history (read FIRST when picking up unfamiliar work).
- **`docs/README.md`** — Docs index + plan-file map.
- **`docs/plans/batch-2-decisions.md`** — Irreversible architectural choices (ARQ, Apprise, polling, session cookies, SQLite-for-now).
- **`docs/step_{1,1_5,2,3}_plan.md`** — Per-step execution plans + reviewer findings.
- **`docs/evaluation_report.md`** — Production-readiness evaluation (post-Step-3).
- **`CONTRIBUTING.md`** — Branch / commit / PR conventions, test-before-merge gate.
- **`backend/README.md` / `frontend/README.md`** — Service-specific install + run instructions.
