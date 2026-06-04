# Job360 Project Status

## Current State: Pillar 2 + Pillar 3 + Step 0 → Step 3 all merged

**Last updated:** 2026-05-28
**Test count:** **1000+ test functions** across 30+ test files. (The hard-coded "600" you'll see in some older sections of this doc was the post-3.5.4 green baseline; run `cd backend && python -m pytest --collect-only -q | tail -1` for the live current number.)
**Source files:** 49 source classes in `backend/src/sources/` split across 6 category subfolders (`apis_keyed/`, `apis_free/`, `ats/`, `feeds/`, `scrapers/`, `other/`).
**Job sources:** 50 keys registered in `SOURCE_REGISTRY` (Batch-3 roster: added `teaching_vacancies`, `gov_apprenticeships`, `nhs_jobs_xml`, `rippling`, `comeet`; dropped `yc_companies`, `nomis`, `findajob`). `SOURCE_INSTANCE_COUNT = 49` (indeed+glassdoor share `JobSpySource`). See CLAUDE.md rule #13 for the FIVE load-bearing surfaces that move together on a registry change.
**Latest merged head:** `a7a2268` on `main` (Step-3 dashboard polish merge, 2026-04-30). All of Pillar 2, Pillar 3 (Batches 1 → 3.5.4), Step 0 (pre-flight), Step 1 (engine→API seam), Step 1.5 (the "bombshell" fix for missing dim columns + serializer), Step 2 (server/client split), Step 3 (observability + notification UX + dashboard polish) merged on top.
**Authoritative architecture reference:** `docs/pillars/` — three pillar manuals (User / Search & Match Engine / Job Providers) plus `glossary.md` + `runbook.md`. The Phase 1 / Phase 2 / Phase 2.5 tables below are *historical* (pre-Phase-4 folder layout — paths like `backend/src/profile/` no longer exist; current paths are `backend/src/services/profile/`, etc.).

---

## Phase 1: Dynamic User Profile System -- COMPLETE

**Goal:** Replace hard-coded AI/ML keywords with user-provided profile data so Job360 works for any profession (sales, law, engineering, hospitality, etc.).

### What was built

| Component | File(s) | Status |
|-----------|---------|--------|
| Profile dataclasses | `backend/src/profile/models.py` | Done -- CVData, UserPreferences, UserProfile, SearchConfig |
| CV parser (PDF/DOCX) | `backend/src/profile/cv_parser.py` | Done -- pdfplumber + python-docx text extraction, LLM-only skill/title extraction via `llm_provider.py` (KNOWN_SKILLS regex removed in commit 804725c) |
| Preferences validator | `backend/src/profile/preferences.py` | Done -- form validation, CV+prefs merge |
| Profile storage | `backend/src/profile/storage.py` | Done -- JSON at `backend/data/user_profile.json` |
| Keyword generator | `backend/src/profile/keyword_generator.py` | Done -- UserProfile -> SearchConfig conversion |
| JobScorer class | `backend/src/filters/skill_matcher.py` | Done -- dynamic scoring using SearchConfig |
| BaseJobSource properties | `backend/src/sources/base.py` | Done -- `self.relevance_keywords`, `self.job_titles`, `self.search_queries` |
| 47 source file refactor | `backend/src/sources/*.py` | Done -- all use `self.*` properties instead of direct imports |
| Orchestrator wiring | `backend/src/main.py` | Done -- loads profile, creates scorer, passes config |
| CLI setup-profile | `backend/src/cli.py` | Done -- interactive profile wizard |
| Profile tests | `backend/tests/test_profile.py` | Done -- 56 tests covering all profile modules |
| Dependencies | `backend/pyproject.toml` | Done -- added pdfplumber, python-docx |

### Backward compatibility

- `keywords.py` is NOT modified -- remains the default keyword source
- All existing function signatures preserved (`score_job()`, `check_visa_flag()`, etc.)
- When no `backend/data/user_profile.json` exists, behavior is **identical** to pre-Phase-1
- `len(SOURCE_REGISTRY) == 48` test assertion unchanged
- All original tests pass without modification

---

## Phase 2: LinkedIn + GitHub API -- COMPLETE (LinkedIn ingest later replaced with PDF)

**Goal:** Enrich user profiles with LinkedIn data and GitHub public repos.

**Superseded note:** the original phase-2 ingest was a LinkedIn Data Export ZIP of CSVs. This was later replaced with a LinkedIn "Save to PDF" parser (`parse_linkedin_pdf` in `backend/src/services/profile/linkedin_parser.py`) — same output schema, same `enrich_cv_from_linkedin()` merge logic. The rest of this section describes the historical ZIP flow.

### What was built

| Component | File(s) | Status |
|-----------|---------|--------|
| LinkedIn ZIP parser | `backend/src/profile/linkedin_parser.py` | Done -- parses positions.csv, skills.csv, education.csv from ZIP |
| LinkedIn CVData enrichment | `backend/src/profile/linkedin_parser.py:enrich_cv_from_linkedin()` | Done -- merges LinkedIn data into CVData |
| GitHub API enricher | `backend/src/profile/github_enricher.py` | Done -- fetches repos, languages, topics; infers skills |
| GitHub CVData enrichment | `backend/src/profile/github_enricher.py:enrich_cv_from_github()` | Done -- merges GitHub data into CVData |
| CVData model fields | `backend/src/profile/models.py` | Done -- linkedin_positions, linkedin_skills, linkedin_industry, github_languages, github_topics, github_skills_inferred |
| UserPreferences field | `backend/src/profile/models.py` | Done -- github_username field |
| CLI --linkedin option | `backend/src/cli.py:setup-profile` | Done -- accepts LinkedIn ZIP path |
| CLI --github option | `backend/src/cli.py:setup-profile` | Done -- accepts GitHub username |
| GITHUB_TOKEN env var | `backend/src/config/settings.py`, `.env.example` | Done -- optional, for higher API rate limits |
| LinkedIn/GitHub tests | `backend/tests/test_linkedin_github.py` | Done -- 54 tests |

### How it works

1. User runs `setup-profile --cv cv.pdf --linkedin export.zip --github username`
2. CV parsed first (existing Phase 1 flow)
3. LinkedIn ZIP parsed: positions, skills, education extracted from CSVs
4. GitHub repos fetched: languages and topics mapped to skills via LANGUAGE_TO_SKILL dict
5. Both merged into CVData via `enrich_cv_from_linkedin()` and `enrich_cv_from_github()`
6. Combined CVData + preferences saved as UserProfile
7. On next pipeline run, all enrichment data feeds into SearchConfig generation

---

## Phase 2.5: Reliability & Extensibility Improvements -- COMPLETE

**Goal:** Fix identified issues from codebase analysis — error handling, schema safety, source health, test coverage, and source metadata.

### What was built/fixed

| Component | File(s) | Status |
|-----------|---------|--------|
| DB error logging | `backend/src/cli_view.py` | Done -- `except Exception` blocks now log errors before returning empty |
| Magic number elimination | `backend/src/main.py`, `backend/tests/test_main.py` | Done -- `SOURCE_INSTANCE_COUNT` constant replaces hard-coded 47 |
| Schema migration | `backend/src/storage/database.py` | Done -- `_migrate()` method uses PRAGMA table_info + ALTER TABLE for future columns |
| Source health tracking | `backend/src/main.py`, `backend/src/storage/database.py` | Done -- detects sources returning 0 that previously had jobs, warns in logs |
| Rate limiter tests | `backend/tests/test_rate_limiter.py` | Done -- 5 tests: acquire/release, context manager, concurrency limit, delay, multi-concurrent |
| Source category metadata | `backend/src/sources/base.py`, all 46 source files | Done -- `category` class attribute (keyed_api/free_json/ats/rss/scraper/other) |
| Integration tests | `backend/tests/test_main.py`, `backend/tests/test_database.py` | Done -- SOURCE_INSTANCE_COUNT validation, failed source tracking, migration, source history |

---

## Phase 3+ (Future)

- Skill inference from job titles (e.g., "Data Scientist" implies Python, SQL, statistics)
- AI-powered CV summarization for better keyword extraction
- Multi-profile support (different job searches simultaneously)
- Job recommendation engine based on profile match patterns
- Interview tracking and application pipeline

---

## What's Done (Steps 0 → 3 all MERGED)

**Step 0 — Pre-flight hardening — MERGED.** Tier A/B/C landed: inspection scripts, fresh-clone DB fix, pre-commit install, setup.bat, bootstrap_dev smoke, migration `0010_run_log_observability` (added `run_uuid` + per-source error/duration columns + `user_id`), `LOG_LEVEL` threading, `.env.example` groupings, frontend/backend READMEs, `docs/README.md` index, `.gitattributes`, `_TEST_NOW` determinism, CONTRIBUTING.md, `frontend/.env.local.example`, setup.sh pyproject/backend-data fix, docs/troubleshooting.md, Makefile + `verify-step-0` + `check_env_example.py`, pytest-xdist opt-in fast marker, migrations runner `status` command, down-migration integration test.

**Step 1 — engine → API seam — MERGED.** `JobScorer(config, user_preferences, enrichment_lookup)` wired into `run_search()` (`main.py:389`) and `score_and_ingest()` worker task; multi-dim scoring activates on both paths. `/api/jobs` + `/api/search` consult enrichment + dimension scores. Migration `0011_score_dimensions` added 9 per-dim columns to `jobs`.

**Step 1.5 — "bombshell" fix — MERGED.** The Step-1 ship had a bug: serializer hard-coded zeros for the new dim columns, the migration was missing, every test only asserted *fields exist* (not *values*). Step 1.5 added the migration, fixed the serializer, added round-trip tests at the DB layer + value-presence tests at the HTTP layer. Now codified as CLAUDE.md rule #21.

**Step 2 — server/client split — MERGED.** Next.js 16 server pages (`generateMetadata` + JSON-LD SEO on `/jobs/[id]`) plus the client interactivity split. Caught the `params: Promise<...>` trap mid-sprint (now CLAUDE.md rule #22 and `frontend/AGENTS.md`).

**Step 3 — observability + notification UX + dashboard polish — MERGED.** Migrations `0012` (per-channel notification rules + `users.timezone`), `0013` (digest queue), `0014` (application stage history + interview dates + notes versioning). New routes: `auth`, `channels`, `notifications`, `notification_rules`, `runs`. Three rounds of dashboard polish (B-1/2/3/6/7 + B-5/11 + B-4 + R-11 carry-forward). Notification ledger UI + per-channel stats + recent-runs view all live.

## What's Next

**Pillar-3 Batch 4 — launch readiness.** Scope-down to top 10–15 sources for first public release; freemium metering; ICO £40 registration; privacy notice + LIA; ASA-compliant copy; Amazon SES; prod-Redis smoke test. See MEMORY notes for carried-forward P3 items from Batch 3.5.

**Pillar 2 advanced surfaces — production rollout decision.** `ENRICHMENT_ENABLED` and `SEMANTIC_ENABLED` are wired and tested but still default OFF (rule #18). Flipping them on in prod requires (a) LLM cost budgeting, (b) ChromaDB persistent-volume sizing, (c) re-embedding plan when the embedding model version changes. No engineering blocker; product/ops decision.

**Doc maintenance — open.** The legacy "Phase 1 / Phase 2 / Phase 2.5" sections below this point use **pre-Phase-4 paths** (`backend/src/profile/`, `backend/src/filters/`, `backend/src/storage/`, `backend/src/notifications/`). They are preserved as a record of *what was built when*, not as current architecture. Read `docs/pillars/` for the current source-of-truth folder structure.

---

## What Is Working Right Now

- Full 50-source pipeline runs end-to-end (async fetch, score, dedup, store, notify) with `TieredScheduler` wired into `run_search` (Batch 3 / 3.5)
- Profile system: CV + LinkedIn + GitHub enrichment → dynamic keywords → personalised search (LLM-only CV parser via multi-provider fallback: Gemini / Groq / Cerebras)
- Multi-user delivery layer (Batch 2): auth + per-tenant isolation + ARQ worker (`WorkerSettings` + `send_notification`) + Apprise dispatcher + `FeedService` SSOT
- Multi-user profile storage (Batch 3.5.2): migration `0006_user_profiles` + per-user `_search_config_for`
- Conditional-cache pilot (Batch 3.5.3): `nhs_jobs_xml` confirmed live ETag → 304; `scripts/preflight_conditional_cache.py` for future candidates
- All 7 keyed APIs skip gracefully when keys are empty
- All ATS boards iterate over ~268 company slugs (10 platforms including Rippling + Comeet from Batch 3)
- All RSS/XML feeds parse correctly with mocked data
- All HTML scrapers extract job data with regex
- Pillar 2 multi-dim scoring available when `JobScorer(..., user_preferences=..., enrichment_lookup=...)` is wired (7-dim: title/skill/location/recency + seniority/salary/visa/workplace); legacy 4-component path unchanged by default
- Pillar 2 opt-in features behind flags (OFF by default): `ENRICHMENT_ENABLED` (LLM enrichment pipeline), `SEMANTIC_ENABLED` (sentence-transformers + ChromaDB)
- SQLite database with auto-purge (30 days); shared `jobs` catalog + per-user `user_feed` / `user_actions` / `applications`
- Email, Slack, Discord (built-in channels) + Apprise-backed multi-channel dispatch (Batch 2)
- CLI commands: run, view, api, status, sources, setup-profile
- Next.js frontend (at `frontend/`) + FastAPI backend (at `backend/src/api/`) deliver the interactive UI
- 600 tests pass (3 skip on Windows — bash-only `setup.sh` / `cron_run.sh` tests)

---

## What Is Fragile or Risky

| Source/Component | Risk | Notes |
|------------------|------|-------|
| **HTML scrapers** (7) | High | LinkedIn, JobTensor, Climatebase, 80000Hours, BCS Jobs, AIJobs Global, AIJobs AI all use regex parsing on HTML. Any layout change breaks them silently (returns 0 jobs, no error). |
| **python-jobspy** (Indeed/Glassdoor) | Medium | Not in backend/pyproject.toml. Optional dependency. If Indeed/Glassdoor change their site, python-jobspy breaks. |
| **Workday ATS** | Medium | Complex dict-format config (tenant/wd/site). Workday API endpoints change occasionally. 15 companies = 15 potential breakpoints. |
| **SuccessFactors** | Medium | Parses sitemap.xml files. Only 3 companies. MBDA already removed (DNS failure). |
| **Personio** | Medium | Uses XML job feed API. 10 companies. Personio may restrict access. |
| **LinkedIn guest API** | High | Unofficial, can break or get rate-limited at any time. |
| **HackerNews sources** | Low | Algolia API is stable, but "Who is Hiring" thread format could change. |
| **CV parser** | Medium | Regex-based section detection. Works for ~80% of CVs. Non-standard formats may miss skills. |
| **Nomis** | Low | UK GOV stats API. Not individual listings. Useful for market intelligence only. |

---

## Known Issues

| Issue | Severity | Notes |
|-------|----------|-------|
| 3 tests skip on Windows | Low | bash-only tests for `setup.sh` and `cron_run.sh` — pass on Linux/Mac |
| `test_main.py` still hits live Indeed | Medium | JobSpy source lacks mock coverage; full suite run can take ~32 min. Documented in MEMORY notes; mocking tracked for a future batch. Rule #4 (mock all HTTP) is otherwise clean across the 600-test baseline. |
| Layer-4 embedding repost dedup not activated | Medium | Scaffolded in `backend/src/services/deduplicator.py` but gated behind `SEMANTIC_ENABLED`; ChromaDB-backed layer is opt-in and not yet wired into the default pipeline path. |
| Batch 2.7 hybrid mode flag not wired to HTTP routes | Medium | `retrieval.reciprocal_rank_fusion` / `is_hybrid_available()` exist but no `/api/jobs` route consults them yet — only CLI / worker consumers. Pillar-2 user-visible hybrid ranking still behind the flag and not surfaced in the dashboard. |
| No skill inference beyond what the LLM extracts | Medium | Profile system relies on LLM-extracted skills + explicit user additions; implicit skill expansion from titles ("Data Scientist" → Python/SQL) not implemented. Partially mitigated by `skill_synonyms.py` canonicalisation (Batch 2.3). |
| python-jobspy not in backend/pyproject.toml core deps | Low | Intentionally optional (heavy dependencies). Indeed/Glassdoor source skips with warning if not installed. |
| GITHUB_TOKEN optional | Low | Without token, GitHub API rate limit is 60 req/hr. With token: 5000 req/hr. Profile enrichment may fail for users with many repos without a token. |
| pdfminer/cryptography conflict | Low | Environment-specific: pyo3 panic in cryptography lib breaks pdfplumber import in some environments |
| Heavy deps must stay lazy-imported | Low (guardrail) | `sentence_transformers`, `chromadb`, `rapidfuzz`, `sklearn`, `apprise` must be imported inside functions, never at module top level. Enforced by CLAUDE.md rules #11 + #16. A stray top-level import regresses pytest collection time by 150 ms – 2 s per process. |

---

## Test Coverage by Module

| Test file | Module tested | Tests |
|-----------|--------------|-------|
| `test_sources.py` | All 50 sources | 71+ |
| `test_profile.py` | `backend/src/profile/*`, `JobScorer` | 55 |
| `test_linkedin_github.py` | LinkedIn parser, GitHub enricher | 54 |
| `test_scorer.py` | `skill_matcher.py` scoring | 53 |
| `test_time_buckets.py` | `time_buckets.py` | 33 |
| `test_models.py` | `models.py` Job dataclass | 21 |
| `test_notifications.py` | Slack + Discord + Email channels | 19 |
| `test_deduplicator.py` | `deduplicator.py` | 13 |
| `test_main.py` | `main.py` orchestrator + error paths | 12 |
| `test_cli.py` | `cli.py` commands + SOURCE_REGISTRY | 11 |
| `test_database.py` | SQLite database + migration + source history | 9 |
| `test_api.py` | FastAPI endpoints (health, jobs, actions, profile, search, pipeline) | 9 |
| `test_llm_provider.py` | Multi-provider LLM client for CV parsing | 8 |
| `test_notification_base.py` | Channel base + discovery | 7 |
| `test_reports.py` | Report generation | 6 |
| `test_setup.py` | setup.sh + requirements | 6 |
| `test_rate_limiter.py` | `rate_limiter.py` | 5 |
| `test_cron.py` | cron_run.sh | 5 |
| `test_cli_view.py` | `cli_view.py` | 5 |
| `test_csv_export.py` | CSV export | 4 |
| (Plus Pillar-2/-3 additions) | migrations, auth, feed, prefilter, channels, crypto, dispatcher, scheduler, circuit_breaker, conditional_cache, embeddings, retrieval, enrichment, dedup layers, Pillar-2 scoring dims | +~190 |
| **Total (current green baseline)** | | **600** passing / 0 failing / 3 skipped on Windows (post-3.5.4) |

### Not covered or lightly covered

- `backend/src/utils/rate_limiter.py` — now has 5 dedicated tests in `test_rate_limiter.py`
- Live HTTP behavior — all tests use mocked responses, so real API format changes are not caught by tests
- Next.js frontend at `frontend/` — no automated UI tests yet (would need Playwright or similar)
- Edge cases in LinkedIn ZIP parsing — malformed ZIPs, missing CSVs tested but exotic edge cases possible

---

## Quick Verification

```bash
cd backend

# Live test count
python -m pytest --collect-only -q | tail -1

# Run the full suite
python -m pytest tests/ -v -p no:randomly

# Apply pending migrations (idempotent)
python -m migrations.runner up
python -m migrations.runner status

# Profile setup (LinkedIn arg now takes a "Save to PDF" — not a Data Export ZIP)
python -m src.cli setup-profile --cv path/to/cv.pdf --linkedin linkedin-profile.pdf --github username

# Pipeline with profile (the only meaningful mode post-2026-04-09)
python -m src.cli run --dry-run --log-level DEBUG

# Source count assertions
python -c "from src.main import SOURCE_REGISTRY, SOURCE_INSTANCE_COUNT; print(f'registry keys: {len(SOURCE_REGISTRY)}, instances: {SOURCE_INSTANCE_COUNT}')"
# Output: registry keys: 50, instances: 49
```

**Note (post-3ba1342):** there is no longer a meaningful "pipeline without profile" path — `keywords.py` defaults are empty, so the legacy `score_job()` route scores against `[]` and returns near-zero scores for everything. Running the pipeline without first running `setup-profile` produces an empty `user_feed` for any logged-in user.
