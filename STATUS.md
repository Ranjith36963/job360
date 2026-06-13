# Job360 Project Status

## Current State: Post-Step-3 matcher batch merged; Step 4 (ops hardening) is next; autonomous maintenance loop running

**Last updated:** 2026-06-11
**Total tests:** 1,285 passing / 0 failing / 3 skipped (1,288 collected as of `a4fe829` — defer to the runtime collected count, not this figure)
**Source files:** 45 source files in `backend/src/sources/` (excluding `__init__.py` and `base.py`) split into 6 category subfolders | **Test files:** 60+ test modules
**Job sources:** 46 registered in `SOURCE_REGISTRY` post-M6 rotation (Batch 3 added teaching_vacancies, gov_apprenticeships, nhs_jobs_xml, rippling, comeet, dropped yc_companies/nomis/findajob; M6 2026-06 dropped jobtensor, comeet, gov_apprenticeships, aijobs_global — all upstream-dead). See CLAUDE.md rule #13 for the five load-bearing surfaces that move together on a registry change.
**Latest merged head:** `a4fe829` on `origin/main` (post-Step-3 matcher batch + autonomous maintenance rounds). Pillar-2/-3 + Step 0/1/1.5/1.6/2/3 + matcher batch all merged.
**Sentinel:** `.claude/step-3-verified.txt` → `337fbda19b5ae30d55dba061bc6658a49bcd208d` (post-reviewer-fix SHA).

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
- `len(SOURCE_REGISTRY) == N` test assertion still in `tests/test_cli.py` (current N = 46, post-M6-rotation)
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

## Matching Engines

Four engines are available, stacked funnel→judge. All default OFF except the keyword funnel:

| Engine | Service | Flag | Default |
|--------|---------|------|---------|
| #1 Keyword funnel | `services/skill_matcher.py` (JobScorer) | always on | ON |
| #2 Enrichment | `services/job_enrichment.py` | `ENRICHMENT_ENABLED` | false |
| #3 Semantic | `services/embeddings.py` + `vector_index.py` + `retrieval.py` | `SEMANTIC_ENABLED` | false |
| #4 LLM judge | `services/llm_matcher.py` (MatchVerdict) | `MATCHER_ENABLED` | false |

Engine #4 runs after per-user feed write (`_run_matcher_stage`). Results stored on `user_feed` (migration 0017). Feed reads rank by `COALESCE(llm_fit_score, score) DESC`. Measured: 18/18 judged in 89.8 s at concurrency 3; judge spread 20–92 vs keyword 30–43; 10/10 fit accuracy on labeled sample.

**Profile-version re-score (migration 0018, automatic, no new flags):** every `user_feed` row is now stamped with the profile version that produced its score. When a user saves a new profile (CV / LinkedIn / GitHub / preferences), the system detects whether the content actually changed, clears old LLM verdicts, and re-scores the full 30-day catalog in the background against the new profile. Ordinary searches only score newly-fetched jobs — existing rows keep their scores. A job's score changes only when the profile changes, never just because time passed. Service: `src/services/rescore.py`; trigger: `src/api/routes/profile.py`.

---

## What's Next (Step 4 — Ops hardening / Batch 4 — Launch readiness)

**Step 0..3 status:** all green and merged on origin/main.
- Step 0 (pre-flight hardening) closed 2026-04-24 at `e31cac7` with 1,018 tests + Makefile + bootstrap_dev + migration 0010 + check_env_example + pytest-xdist.
- Step 1 (engine→API seam) closed with multi-dim scoring + hybrid retrieval + per-dim score columns wired to `/api/jobs` (migration 0011).
- Step 1.5 (post-Step-1 stabilisation) shipped reviewer fixes + dataclass round-trips + lazy-import startup safety.
- Step 1.6 locked the generator/reviewer worktree contract (`.claude/generator-commit.md` + `.claude/reviewer-verdict.md` + `make verify-batch`).
- Step 2 (API→UI seam) closed at `5cf60ea` with all 5 cohorts (foundations + components + page surfaces + SEO + TanStack Query + run-surface + E2E smokes) + reviewer R-1..R-4 fixes + sentinel.
- **Step 3 (new endpoints + Settings UI) closed at origin/main `7194d0e` PR #9 merge** — 8 new backend endpoints, migrations 0012/0013/0014, dispatcher rule consultation with timezone-aware quiet hours, ARQ digest + ghost-sweep periodic tasks, 5 new frontend pages, KanbanBoard polish, Cohort D toasts/a11y/loading skeletons, reviewer R-1..R-7 closed, sentinel `337fbda`.
- **Matcher batch (funnel→judge) merged post-Step-3** on `fix/per-user-search-and-scoring-gate` — `services/llm_matcher.py`, migration 0017 (`user_feed` llm_* columns), `_run_matcher_stage` in pipeline, API llm_* fields, dashboard AI-verdict badge + COALESCE sort, MATCHER_ENABLED/THRESHOLD/MAX_JOBS flags (all default off). Measured: 18/18 in 89.8 s, judge spread 20–92, 10/10 fit accuracy.
- **Autonomous maintenance loop** running — worker/integrator/scout/health agents processing missions from `docs/maintenance/MISSIONS.md`.

**Step 4 — ops hardening (next):** GitHub Actions CI matrix, Dockerfile + docker-compose, deploy platform config, secret manager integration, security headers middleware, `/livez` + `/readyz` split, worker timeouts, pip-audit + npm audit + gitleaks + bandit in CI, FastAPI request timeout middleware, LLM call timeouts, per-query DB deadlines, DB backup script + restore drill, `/admin/runs` UI consuming `GET /api/runs/recent` (Step-3 backend already shipped this).

**Step 5 / Batch 4 — launch readiness:** scope-down to top 10-15 sources, freemium metering, ICO £40 registration, privacy notice + LIA, ASA-compliant marketing copy, Amazon SES wiring (unblocks magic-link email change), full password-reset (forgot-password) flow, friend dogfood, prod-Redis smoke.

**Step 3 carry-overs (technical debt to close in Step 3.5 stabilisation or Step 4):**
- V-01..V-03 form-validation library (RHF + zod) — never installed; new C-02/C-03 forms ship with bespoke `useState` validation.
- V-04 CV upload size cap + MIME allowlist — verify or backfill.
- V-05 OpenAPI → TS codegen — explicitly P2; deferred.
- C-07 `@dnd-kit/core` + `@dnd-kit/sortable` — KanbanBoard ships without these libs; if keyboard a11y on cards is needed, reintroduce.

---

## What Is Working Right Now

- Full 46-source pipeline runs end-to-end (async fetch, score, dedup, store, notify) with `TieredScheduler` wired into `run_search` (Batch 3 / 3.5; M6 rotation removed 4 dead sources)
- Profile system: CV + LinkedIn + GitHub enrichment → dynamic keywords → personalised search (LLM-only CV parser via multi-provider fallback: Gemini / Groq / Cerebras)
- Multi-user delivery layer (Batch 2): auth + per-tenant isolation + ARQ worker (`WorkerSettings` + `send_notification`) + Apprise dispatcher + `FeedService` SSOT
- Multi-user profile storage (Batch 3.5.2): migration `0006_user_profiles` + per-user `_search_config_for`
- Conditional-cache pilot (Batch 3.5.3): `nhs_jobs_xml` confirmed live ETag → 304; `backend/scripts/preflight_conditional_cache.py` for future candidates
- All 7 keyed APIs skip gracefully when keys are empty
- All ATS boards iterate over ~264 company slugs (11 platforms; comeet removed in M6 rotation)
- All RSS/XML feeds parse correctly with mocked data
- All HTML scrapers extract job data with regex
- Pillar 2 multi-dim scoring available when `JobScorer(..., user_preferences=..., enrichment_lookup=...)` is wired (7-dim: title/skill/location/recency + seniority/salary/visa/workplace); legacy 4-component path unchanged by default
- Pillar 2 opt-in features behind flags (OFF by default): `ENRICHMENT_ENABLED` (LLM enrichment pipeline), `SEMANTIC_ENABLED` (sentence-transformers + ChromaDB)
- SQLite database with auto-purge (30 days); shared `jobs` catalog + per-user `user_feed` / `user_actions` / `applications`
- Email, Slack, Discord (built-in channels) + Apprise-backed multi-channel dispatch (Batch 2)
- CLI commands: run, view, api, status, sources, setup-profile
- Next.js frontend (at `frontend/`) + FastAPI backend (at `backend/src/api/`) deliver the interactive UI
- 1,285 tests pass, 3 skip on Windows (bash-only `setup.sh` / `cron_run.sh` tests) — 1,288 collected as of `a4fe829`

---

## What Is Fragile or Risky

| Source/Component | Risk | Notes |
|------------------|------|-------|
| **HTML scrapers** (5) | High | LinkedIn, Climatebase, 80000Hours, BCS Jobs, AIJobs AI all use regex parsing on HTML. Any layout change breaks them silently (returns 0 jobs, no error). |
| **python-jobspy** (Indeed/Glassdoor) | Medium | Not in backend/pyproject.toml. Optional dependency. If Indeed/Glassdoor change their site, python-jobspy breaks. |
| **Workday ATS** | Medium | Complex dict-format config (tenant/wd/site). Workday API endpoints change occasionally. 15 companies = 15 potential breakpoints. |
| **SuccessFactors** | Medium | Parses sitemap.xml files. Only 3 companies. MBDA already removed (DNS failure). |
| **Personio** | Medium | Uses XML job feed API. 10 companies. Personio may restrict access. |
| **LinkedIn guest API** | High | Unofficial, can break or get rate-limited at any time. |
| **HackerNews sources** | Low | Algolia API is stable, but "Who is Hiring" thread format could change. |
| **CV parser** | Medium | Regex-based section detection. Works for ~80% of CVs. Non-standard formats may miss skills. |

---

## Known Issues

| Issue | Severity | Notes |
|-------|----------|-------|
| 3 tests skip on Windows | Low | bash-only tests for `setup.sh` and `cron_run.sh` — pass on Linux/Mac |
| `test_main.py` still hits live Indeed | Medium | JobSpy source lacks mock coverage; full suite run can take ~32 min. Documented in MEMORY notes; mocking tracked for a future batch. Rule #4 (mock all HTTP) is otherwise clean across the 1,285-test baseline. The `make test` target uses `--ignore=tests/test_main.py` for this reason. |
| Layer-4 embedding repost dedup not activated | Medium | Scaffolded in `backend/src/services/deduplicator.py` but gated behind `SEMANTIC_ENABLED`; ChromaDB-backed layer is opt-in and not yet wired into the default pipeline path. |
| ~~Batch 2.7 hybrid mode flag not wired to HTTP routes~~ RESOLVED 2026-06-10 | — | `GET /api/jobs?mode=hybrid` consults the retrieval stack and the dashboard requests it by default (a19db65); the LLM judge re-ranks on top via `COALESCE(llm_fit_score, score)`. |
| No skill inference beyond what the LLM extracts | Medium | Profile system relies on LLM-extracted skills + explicit user additions; implicit skill expansion from titles ("Data Scientist" → Python/SQL) not implemented. Partially mitigated by `skill_synonyms.py` canonicalisation (Batch 2.3). |
| python-jobspy not in backend/pyproject.toml core deps | Low | Intentionally optional (heavy dependencies). Indeed/Glassdoor source skips with warning if not installed. |
| GITHUB_TOKEN optional | Low | Without token, GitHub API rate limit is 60 req/hr. With token: 5000 req/hr. Profile enrichment may fail for users with many repos without a token. |
| pdfminer/cryptography conflict | Low | Environment-specific: pyo3 panic in cryptography lib breaks pdfplumber import in some environments |
| Heavy deps must stay lazy-imported | Low (guardrail) | `sentence_transformers`, `chromadb`, `rapidfuzz`, `sklearn`, `apprise` must be imported inside functions, never at module top level. Enforced by CLAUDE.md rules #11 + #16. A stray top-level import regresses pytest collection time by 150 ms – 2 s per process. |

---

## Test Coverage by Module

| Test file | Module tested | Tests |
|-----------|--------------|-------|
| `test_sources.py` | All 46 sources | 55+ |
| `test_profile.py` | `backend/src/services/profile/*`, `JobScorer` | 55 |
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
| (Plus Pillar-2/-3 + Step-0/1/1.5/2/3 additions) | migrations, auth, feed, prefilter, channels, crypto, dispatcher (rule consultation + timezone-aware quiet hours), scheduler, circuit_breaker, conditional_cache, embeddings, retrieval, enrichment, dedup layers, Pillar-2 scoring dims, score-dim columns, multi-dim scoring, hybrid retrieval, IDOR, account-mgmt, ghost-sweep, application history, notification rules, ledger filters, dim-score round-trips | +~744 |
| **Total (current green baseline)** | | **1,285** passing / 0 failing / 3 skipped on Windows (1,288 collected as of `a4fe829` — defer to runtime count) |

### Not covered or lightly covered

- `backend/src/utils/rate_limiter.py` — now has 5 dedicated tests in `test_rate_limiter.py`
- Live HTTP behavior — all tests use mocked responses, so real API format changes are not caught by tests
- Next.js frontend at `frontend/` — no automated UI tests yet (would need Playwright or similar)
- Edge cases in LinkedIn ZIP parsing — malformed ZIPs, missing CSVs tested but exotic edge cases possible

---

## Quick Verification

```bash
# All tests pass
python -m pytest backend/tests/ -v

# Profile setup works (all enrichment sources)
python -m src.cli setup-profile --cv path/to/cv.pdf --linkedin export.zip --github username

# Pipeline with profile
python -m src.cli run --dry-run --log-level DEBUG
# Log: "Using dynamic keywords from user profile"

# Pipeline without profile
rm backend/data/user_profile.json
python -m src.cli run --dry-run --log-level DEBUG
# Log: "No user profile found, using default keywords"

# Check source count
python -c "from src.main import SOURCE_REGISTRY; print(len(SOURCE_REGISTRY))"
# Output: 46 (post-M6 rotation; 4 upstream-dead sources removed 2026-06)
```
