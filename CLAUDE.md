# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Job360 is an automated UK job search system supporting **any professional domain**. It aggregates jobs from 48 sources (via `SOURCE_REGISTRY` in `src/main.py`), scores them 0-100 against a user profile, deduplicates across sources, and delivers results via CLI, email, Slack, Discord, CSV, and a Next.js frontend (backed by FastAPI). Users can personalise searches by providing a CV (PDF/DOCX), a LinkedIn profile PDF (profile → More → Save to PDF), and/or GitHub username. When a user profile exists (`data/user_profile.json`), keywords are generated dynamically from CV + preferences + LinkedIn + GitHub via `SearchConfig`. Without a profile, it defaults to the original AI/ML keywords.

## Tech Stack

| Package | Version | Purpose |
|---------|---------|---------|
| aiohttp | >=3.9.0 | Async HTTP client for source fetching |
| aiosqlite | >=0.19.0 | Async SQLite for job storage |
| python-dotenv | >=1.0.0 | .env file loading |
| jinja2 | >=3.1.0 | HTML report templates |
| click | >=8.1.0 | CLI framework |
| pandas | >=2.0.0 | DataFrame support for python-jobspy (Indeed/Glassdoor) |
| pdfplumber | >=0.10.0 | PDF text extraction for CV parsing |
| python-docx | >=1.1.0 | DOCX text extraction for CV parsing |
| rich | >=13.0.0 | Terminal table rendering |
| humanize | >=4.9.0 | Relative time formatting |
| fastapi | >=0.115.0 | API server for Next.js frontend |
| uvicorn[standard] | >=0.30.0 | ASGI server for FastAPI |
| python-multipart | >=0.0.9 | File upload support for FastAPI |
| httpx | >=0.27.0 | Async HTTP client (used by API + LLM providers) |
| google-generativeai | >=0.8.0 | Gemini LLM provider for CV parsing |
| groq | >=0.11.0 | Groq LLM provider for CV parsing |
| cerebras-cloud-sdk | >=1.0.0 | Cerebras LLM provider for CV parsing |

**Dev/test extras** (in `requirements-dev.txt`): pytest >=8.0.0, pytest-asyncio >=0.23.0, aioresponses >=0.7.0, fpdf2 >=2.7.0

**Optional** (not in requirements.txt): `python-jobspy` — used by `src/sources/indeed.py` for Indeed/Glassdoor scraping. The source gracefully skips with a warning if not installed.

**Python:** 3.9+ required.

## Commands

**All backend commands run from `backend/`** (after the directory restructure — see Folder Structure below). Frontend commands run from `frontend/`.

```bash
# Setup (from project root)
bash setup.sh                  # Creates venv, installs deps, validates .env
source venv/bin/activate       # Activate virtualenv (Linux/Mac)

# Backend — all commands below run from backend/
cd backend

# API server (for Next.js frontend)
python main.py                                      # Start FastAPI on localhost:8000
uvicorn main:app --host 0.0.0.0 --port 8000         # Production-style
python -m src.cli api                               # Alternative entry via CLI
python -m src.cli api --port 3001 --host 0.0.0.0    # Custom host/port

# Run the pipeline
python -m src.cli run                               # Full pipeline (47 source instances)
python -m src.cli run --source arbeitnow            # Single source
python -m src.cli run --dry-run --log-level DEBUG    # Dry run with debug
python -m src.cli run --db-path /tmp/test.db         # Custom DB path
python -m src.cli run --no-email                     # Skip notifications

# Profile setup (personalise for any domain)
python -m src.cli setup-profile --cv path/to/cv.pdf                    # CV only
python -m src.cli setup-profile --cv cv.pdf --linkedin linkedin.pdf    # CV + LinkedIn (profile PDF: More → Save to PDF)
python -m src.cli setup-profile --cv cv.pdf --github username          # CV + GitHub

# Other CLI commands
python -m src.cli status       # Last run stats
python -m src.cli sources      # List all 48 sources
python -m src.cli view --hours 24 --min-score 50   # Rich terminal table
python -m src.cli view --visa-only                  # Filter by visa

# Tests (run from backend/ — pytest picks up pyproject.toml pythonpath=["."])
python -m pytest tests/ -v                              # Run all 412 tests
python -m pytest tests/test_scorer.py -v                # Scoring tests
python -m pytest tests/test_sources.py -v               # All sources (71)
python -m pytest tests/test_profile.py -v               # Profile system (55)
python -m pytest tests/test_api.py -v                   # FastAPI endpoints (9)
python -m pytest tests/test_scorer.py::test_name -v     # Single test

# Frontend — all commands below run from frontend/
cd ../frontend
npm run dev                    # Next.js dev server (localhost:3000)
npm run build                  # Production build
npm run lint                   # ESLint
```

## Folder Structure

The repo is split into two top-level deployables: **`backend/`** (Python + FastAPI) and **`frontend/`** (Next.js 16). Runtime data lives inside `backend/data/` so each side is self-contained.

```
job360/
├── backend/
│   ├── main.py                 # FastAPI entrypoint (uvicorn target) — thin, imports src/api/main.py
│   ├── pyproject.toml          # Deps + dev + indeed extras, ruff/mypy/pytest config, pythonpath=["."]
│   ├── data/                   # Runtime data (gitignored): jobs.db, user_profile.json, exports/, reports/, logs/
│   ├── src/
│   │   ├── main.py             # Pipeline orchestrator: run_search(), SOURCE_REGISTRY (48), _build_sources()
│   │   ├── cli.py              # Click CLI: run, api, status, sources, view, setup-profile
│   │   ├── cli_view.py         # Rich terminal table viewer (time-bucketed)
│   │   ├── models.py           # Job dataclass with normalized_key() for dedup
│   │   │
│   │   ├── api/                # Delivery layer (FastAPI)
│   │   │   ├── main.py         # FastAPI app: CORS, lifespan, route registration
│   │   │   ├── dependencies.py # Shared deps: get_db(), save_upload_to_temp()
│   │   │   ├── models.py       # Pydantic request/response models (matches frontend types.ts)
│   │   │   └── routes/         # 7 route modules: health, jobs, actions, profile, search, pipeline
│   │   │
│   │   ├── core/               # App config + constants (phase 4 rename from config/)
│   │   │   ├── settings.py     # Env vars, paths, RATE_LIMITS, thresholds
│   │   │   ├── keywords.py     # Default keywords (emptied in a01c1b3 — LLM-driven only)
│   │   │   └── companies.py    # ATS company slugs (~104 companies across 10 platforms)
│   │   │
│   │   ├── services/           # Business logic (phase 4 merge of filters/ + notifications/ + profile/)
│   │   │   ├── skill_matcher.py  # JobScorer: title/skill/location/recency scoring, visa detection
│   │   │   ├── deduplicator.py   # Group by normalized_key, keep highest-scored
│   │   │   ├── notifications/    # Email / Slack / Discord channels + report_generator
│   │   │   │   ├── base.py       # NotificationChannel ABC + get_configured_channels()
│   │   │   │   ├── email_notify.py
│   │   │   │   ├── slack_notify.py
│   │   │   │   ├── discord_notify.py
│   │   │   │   └── report_generator.py
│   │   │   └── profile/          # CV + LinkedIn + GitHub enrichment, LLM-driven
│   │   │       ├── models.py     # CVData, UserPreferences, UserProfile, SearchConfig
│   │   │       ├── cv_parser.py  # PDF/DOCX extraction; LLM-only skill/title extraction
│   │   │       ├── llm_provider.py  # Multi-provider LLM client (Gemini/Groq/Cerebras)
│   │   │       ├── preferences.py
│   │   │       ├── storage.py
│   │   │       ├── keyword_generator.py
│   │   │       ├── linkedin_parser.py
│   │   │       └── github_enricher.py
│   │   │
│   │   ├── repositories/       # Data access (phase 4 rename from storage/)
│   │   │   ├── database.py     # Async SQLite (aiosqlite), WAL, auto-purge >30 days
│   │   │   └── csv_export.py
│   │   │
│   │   ├── sources/            # 47 source files split by category (phase 2)
│   │   │   ├── base.py         # BaseJobSource ABC + _is_uk_or_remote helper
│   │   │   ├── apis_keyed/     # 7: adzuna, careerjet, findwork, google_jobs, jooble, jsearch, reed
│   │   │   ├── apis_free/      # 10: aijobs, arbeitnow, devitjobs, himalayas, hn_jobs, jobicy, landingjobs, remoteok, remotive, yc_companies
│   │   │   ├── ats/            # 10: ashby, greenhouse, lever, personio, pinpoint, recruitee, smartrecruiters, successfactors, workable, workday
│   │   │   ├── feeds/          # 8 RSS/XML: biospace, findajob, jobs_ac_uk, nhs_jobs, realworkfromanywhere, uni_jobs, workanywhere, weworkremotely
│   │   │   ├── scrapers/       # 7 HTML: aijobs_ai, aijobs_global, bcs_jobs, climatebase, eightykhours, jobtensor, linkedin
│   │   │   └── other/          # 5: hackernews, indeed (jobspy), nofluffjobs, nomis, themuse
│   │   │
│   │   └── utils/              # Cross-cutting helpers
│   │       ├── logger.py       # Rotating file + console logging
│   │       ├── rate_limiter.py # Async semaphore + delay
│   │       └── time_buckets.py
│   └── tests/                  # 412 tests across 23 files (pytest pythonpath=["."])
│       ├── conftest.py
│       └── test_*.py           # Mirrors src/ layout
│
├── frontend/                   # Next.js 16 + React 19 + Tailwind 4 + shadcn 4
│   ├── next.config.ts
│   ├── tsconfig.json           # "@/*" → "./src/*"
│   ├── components.json         # shadcn config
│   ├── package.json
│   ├── public/
│   └── src/
│       ├── app/                # App Router: layout.tsx, page.tsx, dashboard/, jobs/[id]/, pipeline/, profile/
│       ├── components/
│       │   ├── ui/             # shadcn primitives: button, card, dialog, input, ...
│       │   ├── jobs/           # JobCard, JobList, FilterPanel, ScoreRadar, TimeBuckets
│       │   ├── profile/        # CVUpload, CVViewer, PreferencesForm
│       │   ├── pipeline/       # KanbanBoard
│       │   └── layout/         # Navbar, Footer, FloatingIcons
│       └── lib/
│           ├── api.ts          # fetch-based API client (typed against types.ts)
│           ├── types.ts        # TypeScript types mirroring backend Pydantic models
│           └── utils.ts        # cn() etc.
│
├── scripts/
│   └── split_sources_by_category.py  # One-shot migration used during phase 2 restructure
├── docs/                       # Architecture, ADRs (future)
├── .env.example
├── setup.sh
├── cron_setup.sh
├── CLAUDE.md
├── README.md
└── ARCHITECTURE.md
```

**Restructure note (phases 1–4):** The codebase was previously flat at project root (`src/`, `tests/`, `pyproject.toml`). The clean-architecture layout was built up in four commits:
- `0d3ef72` — phase 1: outer move (`src/` → `backend/src/`, `tests/` → `backend/tests/`, `pyproject.toml` → `backend/pyproject.toml`, `data/` → `backend/data/`, merged `requirements*.txt` into `[project.dependencies]`)
- `bd8f952` — phase 2: sources split (47 sources → 6 category subfolders under `backend/src/sources/`)
- `e4b7c07` — phase 3: `backend/main.py` uvicorn entry + `frontend/src/` wrapper
- `a814ae8` — phase 4: internal rename — `filters/` → `services/`, `notifications/` + `profile/` → `services/{notifications,profile}/`, `storage/` → `repositories/`, `config/` → `core/`. 197 import rewrites across 51 files. Module paths are now `src.services.X`, `src.repositories.X`, `src.core.X`.

**Deferred:** `api/` → `api/v1/` routing upgrade (coordinates with frontend API client change). `models.py` split into `models/{domain,schemas}/` would collide with `repositories/database.py` naming and has been left as a single module at `backend/src/models.py`.

## Architecture

The pipeline flows: **CLI (Click)** → **Orchestrator (`src/main.py`)** → **Sources (async fetch via `asyncio.gather`)** → **Scorer** → **Deduplicator** → **SQLite DB** → **Notifications + Reports + CSV**

### Key modules

- `src/main.py` — Central orchestrator with `run_search()` and `SOURCE_REGISTRY` dict (48 entries) mapping source names to classes. `_build_sources()` instantiates all sources with their config. Both `"indeed"` and `"glassdoor"` map to `JobSpySource`.
- `src/cli.py` — Click CLI with `run`, `api`, `status`, `sources`, `view`, `setup-profile` commands. `setup-profile` accepts `--cv`, `--linkedin`, and `--github` options.
- `src/cli_view.py` — Rich terminal table viewer for browsing jobs from the DB
- `src/models.py` — `Job` dataclass with `normalized_key()` for dedup (strips company suffixes like Ltd/Inc/PLC and region suffixes like UK/US/EMEA, lowercases)
- `src/config/settings.py` — All env vars, paths, `RATE_LIMITS` dict (48 entries, per-source), thresholds. Constants: `MIN_MATCH_SCORE=30`, `MAX_RETRIES=3`, `RETRY_BACKOFF=[1,2,4]`, `REQUEST_TIMEOUT=30`.
- `src/config/keywords.py` — Default AI/ML keywords: `JOB_TITLES` (25), skills in 3 tiers (`PRIMARY_SKILLS` 15 / `SECONDARY_SKILLS` 17 / `TERTIARY_SKILLS` 11), `LOCATIONS` (26: 24 UK + Remote + Hybrid), `RELEVANCE_KEYWORDS`, `NEGATIVE_TITLE_KEYWORDS` (60 entries across 12 categories). `KNOWN_SKILLS` and `KNOWN_TITLE_PATTERNS` were removed in commit 3ba1342 — CV parsing is now LLM-only via `src/profile/llm_provider.py`. Used as fallback when no user profile exists.
- `src/config/companies.py` — ATS company slugs: Greenhouse (25), Lever (12), Workable (8), Ashby (9), SmartRecruiters (6), Pinpoint (8), Recruitee (8), Workday (15 — dict format with tenant/wd/site/name), Personio (10), SuccessFactors (3 — dict format with name/sitemap_url). ~104 companies total.
- `src/profile/` — Dynamic user profile system:
  - `models.py` — `CVData` (includes linkedin_positions, linkedin_skills, github_languages, github_topics, github_skills_inferred fields), `UserPreferences` (includes github_username), `UserProfile`, `SearchConfig` dataclasses. `SearchConfig.from_defaults()` returns the hard-coded AI/ML keywords.
  - `cv_parser.py` — PDF (pdfplumber) / DOCX (python-docx) text extraction, section detection. Skill/title extraction is LLM-only via `llm_provider.py` — the regex `KNOWN_SKILLS`/`KNOWN_TITLE_PATTERNS` approach was removed in commit 804725c
  - `llm_provider.py` — Multi-provider LLM client (Gemini, Groq, Cerebras) with free-tier fallback chain for CV parsing
  - `preferences.py` — Validates/normalises form data, merges CV data with user preferences
  - `storage.py` — JSON persistence at `data/user_profile.json`
  - `keyword_generator.py` — Converts `UserProfile` → `SearchConfig` (auto-tiers skills, builds relevance keywords, generates search queries)
  - `linkedin_parser.py` — Parses a LinkedIn "Save to PDF" profile export. `parse_linkedin_pdf()` extracts structured data via pdfplumber section-splitting (deterministic) plus LLM structuring of Experience/Education/Certifications sections; returns the same dict schema the old ZIP parser produced. `is_linkedin_pdf()` detects LinkedIn PDFs vs regular CVs via a 2-of-3 heuristic (linkedin.com/in/ URL, ≥3 known section headings, "Page N of M" footer). `enrich_cv_from_linkedin()` merges into CVData — signature unchanged.
  - `github_enricher.py` — Fetches public GitHub repos via API, infers skills from languages/topics/README. `fetch_github_profile()` is async, `enrich_cv_from_github()` merges into CVData. Uses optional `GITHUB_TOKEN` for higher rate limits.

### Sources (`src/sources/`)

All sources extend `BaseJobSource` in `src/sources/base.py` which provides `_get_json()`, `_post_json()`, `_get_text()` with built-in retry (3 attempts, exponential backoff 1s/2s/4s) and rate limiting. The `_is_uk_or_remote()` helper (also in `base.py`) checks location strings against `UK_TERMS`, `REMOTE_TERMS`, and `FOREIGN_INDICATORS` from `skill_matcher.py`. Sources are grouped in `_build_sources()`:

- **Keyed APIs** (7): Reed, Adzuna, JSearch, Jooble, Google Jobs (SerpApi), Careerjet, Findwork — skip gracefully when API key is empty
- **Free JSON APIs** (10): Arbeitnow, RemoteOK, Jobicy, Himalayas, Remotive, DevITjobs, Landing.jobs, AIJobs.net, HN Jobs (Firebase), YC Companies
- **ATS boards** (10): Greenhouse, Lever, Workable, Ashby, SmartRecruiters, Pinpoint, Recruitee, Workday, Personio, SuccessFactors — iterate over company slugs in `companies.py`
- **RSS/XML feeds** (8): jobs.ac.uk, NHS Jobs, WorkAnywhere, WeWorkRemotely, RealWorkFromAnywhere, BioSpace, University Jobs (6 UK unis), FindAJob (UK GOV)
- **HTML scrapers** (7): LinkedIn, JobTensor, Climatebase, 80000Hours (Algolia API), BCS Jobs, AIJobs Global, AIJobs AI
- **Other** (4): Indeed/Glassdoor (python-jobspy, optional), HackerNews (Algolia "Who is Hiring"), TheMuse, NoFluffJobs
- **Market intelligence** (1): Nomis (UK GOV vacancy statistics, not individual listings)

Each source uses `self.relevance_keywords` (property on `BaseJobSource`) to filter irrelevant jobs. This returns dynamic keywords from `SearchConfig` when a profile exists, or falls back to the hard-coded `RELEVANCE_KEYWORDS` from `keywords.py`. Sources with custom queries (JSearch, LinkedIn, FindAJob, NHS Jobs) also check `self.search_queries` before using their hard-coded query lists.

### Scoring (`src/filters/skill_matcher.py`)

Four components summing to 0-100: Title match (0-40), Skill match (0-40, primary=3pts/secondary=2pts/tertiary=1pt), Location (0-10), Recency (0-10). Penalties: Negative title keywords (-30), Foreign location (-15). Minimum threshold is `MIN_MATCH_SCORE=30` in settings. Also detects visa sponsorship (`check_visa_flag`) and experience level from title. Two scoring paths: `score_job()` (module-level, uses hard-coded keywords) and `JobScorer(config).score()` (instance-based, uses dynamic `SearchConfig`). The orchestrator selects based on whether a user profile exists.

### Deduplication (`src/filters/deduplicator.py`)

Groups by `job.normalized_key()` = (normalized company, normalized title). Keeps the best per group by highest `match_score`, then by data completeness (salary, description, location).

### Notifications (`src/notifications/`)

`NotificationChannel` ABC in `base.py` with auto-discovery via `get_configured_channels()`. Channels: Email (Gmail SMTP), Slack (Block Kit webhook), Discord (embed webhook). Each channel activates only when its env vars are set.

### Storage (`src/storage/`)

- `database.py` — Async SQLite (via aiosqlite) with `jobs` table (unique on normalized_company+normalized_title), `run_log` table (includes sources_queried column), and indexes on date_found, first_seen, match_score. Uses WAL journal mode and 5s busy timeout. Auto-purges jobs >30 days old via `purge_old_jobs()`.
- `csv_export.py` — CSV export per run to `data/exports/`

## Testing

**412 tests** across 21 test files (count is `pytest --collect-only` output; parametrized tests expand into multiple collected items). Shared fixtures in `tests/conftest.py` (provides `sample_ai_job`, `sample_unrelated_job`, `sample_duplicate_jobs`, `sample_visa_job`, `sample_non_uk_job`, `sample_empty_description_job`). All HTTP calls mocked with `aioresponses`. Uses `pytest-asyncio` for async tests.

Key test files:
- `test_sources.py` — 71 tests: all 48 sources with mocked HTTP responses
- `test_profile.py` — 55 tests: SearchConfig defaults, UserProfile, CV parser, preferences, storage, keyword generator, JobScorer (including cross-domain scoring)
- `test_linkedin_github.py` — 58 tests: LinkedIn PDF parsing (detection, section split, deterministic + LLM extraction), GitHub API enrichment, CVData merging
- `test_scorer.py` — 53 tests: scoring components, penalties, word boundaries, experience detection, visa negation, location ordering
- `test_time_buckets.py` — 33 tests: time bucket grouping logic
- `test_models.py` — 21 tests: Job dataclass, normalization, salary sanitization, normalization divergence documentation
- `test_notifications.py` — 19 tests: Email, Slack, Discord sending
- `test_deduplicator.py` — 13 tests: dedup logic, company suffix stripping
- `test_main.py` — 12 tests: orchestrator with mocked sources
- `test_cli.py` — 11 tests: CLI commands, `len(SOURCE_REGISTRY) == 48` assertion (update when adding/removing sources)
- `test_database.py` — 9 tests: SQLite operations, migrations, source history
- `test_api.py` — 9 tests: FastAPI endpoints (health, status, sources, jobs, actions, profile, pipeline, integration)
- `test_llm_provider.py` — 8 tests: multi-provider LLM client for CV parsing
- `test_notification_base.py` — 7 tests: ABC, format_salary, channel discovery
- `test_setup.py` — 6 tests: setup.sh validation
- `test_reports.py` — 6 tests: Markdown + HTML report generation
- `test_rate_limiter.py` — 5 tests: async rate limiter (acquire/release, concurrency, delay)
- `test_cron.py` — 5 tests: cron_setup.sh validation
- `test_cli_view.py` — 5 tests: Rich terminal table viewer
- `test_csv_export.py` — 4 tests: CSV export format

## Environment

- Python 3.9+ required
- Dependencies: `requirements.txt` (prod, 12 packages), `requirements-dev.txt` (test — includes prod via `-r`, adds pytest/aioresponses/fpdf2)
- `.env` file for API keys and webhook URLs (see `.env.example`); free sources (41 of 48) work without any keys
- Data outputs go to `data/` directory (gitignored): `data/exports/`, `data/reports/`, `data/logs/`, `data/jobs.db`, `data/user_profile.json`

### Environment Variables

| Variable | Required | Used by |
|----------|----------|---------|
| `REED_API_KEY` | No | ReedSource |
| `ADZUNA_APP_ID` + `ADZUNA_APP_KEY` | No | AdzunaSource |
| `JSEARCH_API_KEY` | No | JSearchSource |
| `JOOBLE_API_KEY` | No | JoobleSource |
| `SERPAPI_KEY` | No | GoogleJobsSource |
| `CAREERJET_AFFID` | No | CareerjetSource |
| `FINDWORK_API_KEY` | No | FindworkSource |
| `GITHUB_TOKEN` | No | GitHub profile enrichment (higher rate limits) |
| `SMTP_EMAIL` + `SMTP_PASSWORD` + `NOTIFY_EMAIL` | No | Email notifications |
| `SLACK_WEBHOOK_URL` | No | Slack notifications |
| `DISCORD_WEBHOOK_URL` | No | Discord notifications |
| `TARGET_SALARY_MIN` / `TARGET_SALARY_MAX` | No | Salary range tiebreaker sorting (default 40k-120k) |

## Important Patterns

- **Adding a new job source:** Create class in `src/sources/`, extend `BaseJobSource`, implement `async fetch_jobs() -> list[Job]`. Use `self.relevance_keywords` and `self.job_titles` (not direct imports). If custom `__init__`, accept `search_config=None` and pass to `super().__init__(session, search_config=search_config)`. Register in both `SOURCE_REGISTRY` dict and `_build_sources()` list in `src/main.py` (passing `search_config=sc`). Add rate limit entry in `RATE_LIMITS` dict in `settings.py`. Add mocked tests in `tests/test_sources.py`. Update the registry count assertion in `tests/test_cli.py`. If keyed, add env var to `settings.py` and `.env.example`.
- **Adding a new notification channel:** Implement `NotificationChannel` ABC, register in `get_all_channels()` in `src/notifications/base.py`
- **Keyed source pattern:** Accept `api_key` in `__init__`, return `[]` early with an info log if key is empty. Pass `search_config` through to super.
- **Free source pattern:** Filter results using `self.relevance_keywords` on title+description, use `_is_uk_or_remote()` on location, no auth needed
- **ATS source pattern:** Accept `companies` list and `search_config=None` in `__init__` with default from `companies.py`, iterate over company slugs, fetch each company's board API
- **RSS/XML source pattern:** Use `_get_text()` to fetch, parse with `xml.etree.ElementTree` (stdlib), extract `<item>` elements from `<channel>`
- **HTML scraper pattern:** Use `_get_text()` to fetch, parse with `re` regex patterns to extract job cards, links, company names
- **Dynamic keywords:** Sources access keywords via `self.relevance_keywords`, `self.job_titles`, `self.search_queries` (properties on `BaseJobSource`). These return `SearchConfig` values when a profile is loaded, or hard-coded defaults from `keywords.py` when `search_config=None`.


## Rules for Working on This Codebase

1. **Never touch `normalized_key()` in `models.py`** without verifying the deduplicator and database UNIQUE constraint still work. Changing normalization logic can cause duplicate entries or missed dedup.
2. **Never change `BaseJobSource`** (constructor, properties, retry logic, `_get_json`/`_post_json`/`_get_text`) without checking all 47 source files that inherit from it. Changes propagate to every source.
3. **Never touch database purge logic** (`purge_old_jobs` in `database.py`) without explicit confirmation. Incorrect purge thresholds can delete valid data.
4. **Always mock HTTP in tests** — never make live HTTP requests. Use `aioresponses` for all source and notification tests. The test suite must run offline.
5. **Always run the relevant test suite** after any change: `python -m pytest tests/ -v` for broad changes, or the specific test file for targeted changes.
6. **Read a file fully before editing it.** Understand the existing logic, imports, and how other modules depend on it.
7. **Check if something exists before creating it.** Search for existing implementations before adding new files or functions.
8. **When adding/removing sources:** Update `SOURCE_REGISTRY` dict, `_build_sources()` list, `RATE_LIMITS` dict, the test assertion `len(SOURCE_REGISTRY) == N` in `test_cli.py`, and the expected source set in the same file.
9. **Scoring changes require test verification.** The scoring algorithm is tested with 53 tests across edge cases. Run `test_scorer.py` and `test_profile.py` after any change to `skill_matcher.py`.

## Batch 2 additions (pillar 3 multi-user delivery layer)

Multi-user delivery landed in `pillar3/batch-2` (see `docs/plans/batch-2-plan.md`).
Key surfaces a future session needs to know:

### New tables (managed by `backend/migrations/`)

| Table | Owner migration | Purpose |
|---|---|---|
| `users` | `0001_auth` | authenticated users |
| `sessions` | `0001_auth` | signed-cookie session rows (FK → users) |
| `user_feed` | `0003_user_feed` | SSOT per-user view — dashboard + notification worker both read |
| `notification_ledger` | `0004_notification_ledger` | per-channel idempotency + retry audit |
| `user_channels` | `0005_user_channels` | Fernet-encrypted Apprise credentials |
| `_schema_migrations` | `runner.py` on first run | applied-migration registry |

`user_actions` and `applications` were rebuilt in `0002_multi_tenant` to add
`user_id` + widen `UNIQUE(job_id)` → `UNIQUE(user_id, job_id)`. Pre-Batch-2
rows were backfilled to the placeholder user
`00000000-0000-0000-0000-000000000001` (see `src/core/tenancy.DEFAULT_TENANT_ID`).
The `jobs` table is **unchanged** — it remains a shared catalog (rule #1).

### New modules

- `backend/migrations/runner.py` — forward/reverse SQL migration runner (CLI: `python -m migrations.runner {up|down|status} [db_path]`)
- `src/services/auth/{passwords,sessions}.py` — argon2id + itsdangerous-signed cookies (30-day expiry)
- `src/services/feed.py` — `FeedService` with `list_for_user`, `list_pending_notifications`, `mark_notified`, `update_status`, `cascade_stale`, `upsert_feed_row`
- `src/services/prefilter.py` — 3-stage cascade (location → experience → skill overlap) — blueprint §2 99% elimination rule
- `src/services/channels/{crypto,dispatcher}.py` — Fernet encryption + Apprise wrapper (lazy import; tests monkeypatch `apprise.Apprise`)
- `src/workers/tasks.py` — `score_and_ingest`, `mark_ledger_sent/failed`, `idempotency_key` — pure async (no `arq` import) so pytest never touches Redis
- `src/api/auth_deps.py` — `require_user` / `optional_user` FastAPI dependencies reading the `job360_session` cookie
- `src/api/routes/{auth,channels}.py` — `/api/auth/*`, `/api/settings/channels/*`

### New env vars

| Var | Required | Default | Used by |
|---|---|---|---|
| `SESSION_SECRET` | Yes in prod | dev fallback | `itsdangerous` HMAC for session cookies |
| `CHANNEL_ENCRYPTION_KEY` | Yes in prod | dev fallback | Fernet encryption of channel credentials |
| `FRONTEND_ORIGIN` | No | `http://localhost:3000` | CORS allow-list (comma-sep) |
| `REDIS_URL` | Only when running ARQ worker | `redis://localhost:6379` | ARQ broker (runtime only, tests skip) |

### New deps

- `argon2-cffi>=25.1.0` — argon2id password hashing
- `itsdangerous>=2.2.0` — signed cookie HMAC
- `apprise>=1.9.9` — multi-channel notification sending
- `pydantic[email]` (via `email-validator>=2.3.0`) — `EmailStr` validation
- `cryptography>=42.0.0` — Fernet (transitive already, now explicit)

### How to run

```bash
# Apply Batch 2 migrations (idempotent — safe on every boot)
cd backend && python -m migrations.runner up

# FastAPI boot auto-runs the migration runner after legacy init_db()
# — see src/api/dependencies.py

# ARQ worker (not required for CLI / read-only usage):
#   arq src.workers.settings.WorkerSettings       # to be added in follow-up
```

### Additional CLAUDE rules

10. **Never INSERT into `jobs` with a `user_id` or `tenant_id` column** — it does not have one, by design. `jobs` is the shared catalog (blueprint §3). Per-user state lives in `user_feed`, `user_actions`, `applications`.
11. **Never import `apprise` at module top level** in library code — Apprise pulls ~30 MB of deps. Import lazily inside the function that uses it (see `dispatcher._get_apprise_cls`). Tests monkeypatch `apprise.Apprise`; real sends happen only under ARQ worker context.
12. **Every new per-user FastAPI route MUST take `user: CurrentUser = Depends(require_user)`** and scope queries by `user.id`. Never accept `user_id` from a URL parameter or body — that is a trivial IDOR vulnerability.

## Related Documentation

- `STATUS.md` — Project phase status, what's complete, what's next, known issues, test coverage table
- `ARCHITECTURE.md` — Deep technical reference: data flow diagrams, directory structure, scoring algorithm detail, source categories, database schema, config variables, dependency list
- `docs/IMPLEMENTATION_LOG.md` — pillar 3 batch-by-batch completion log (read FIRST for current state)
- `docs/plans/batch-2-decisions.md` — irreversible architectural decisions (ARQ, Apprise, polling, session cookies, SQLite-for-now)
- `docs/plans/batch-2-plan.md` — Batch 2 TDD implementation plan with locked baseline
