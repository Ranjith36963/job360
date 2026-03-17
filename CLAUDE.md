# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Job360 is an automated UK job search system supporting **any professional domain**. It aggregates jobs from 48 sources (via `SOURCE_REGISTRY` in `src/main.py`), scores them 0-100 against a user profile, deduplicates across sources, and delivers results via CLI, email, Slack, Discord, CSV, and a Streamlit dashboard. Users can personalise searches by providing a CV (PDF/DOCX), LinkedIn data export (ZIP), and/or GitHub username. When a user profile exists (`data/user_profile.json`), keywords are generated dynamically from CV + preferences + LinkedIn + GitHub via `SearchConfig`. Without a profile, it defaults to the original AI/ML keywords.

## Tech Stack

| Package | Version | Purpose |
|---------|---------|---------|
| aiohttp | >=3.9.0 | Async HTTP client for source fetching |
| aiosqlite | >=0.19.0 | Async SQLite for job storage |
| python-dotenv | >=1.0.0 | .env file loading |
| jinja2 | >=3.1.0 | HTML report templates |
| click | >=8.1.0 | CLI framework |
| streamlit | >=1.30.0 | Web dashboard |
| pandas | >=2.0.0 | Data manipulation in dashboard |
| plotly | >=5.18.0 | Charts in dashboard |
| pdfplumber | >=0.10.0 | PDF text extraction for CV parsing |
| python-docx | >=1.1.0 | DOCX text extraction for CV parsing |
| rich | >=13.0.0 | Terminal table rendering |
| humanize | >=4.9.0 | Relative time formatting |

**Dev/test extras** (in `requirements-dev.txt`): pytest >=8.0.0, pytest-asyncio >=0.23.0, aioresponses >=0.7.0, fpdf2 >=2.7.0

**Optional** (not in requirements.txt): `python-jobspy` — used by `src/sources/indeed.py` for Indeed/Glassdoor scraping. The source gracefully skips with a warning if not installed.

**Python:** 3.9+ required.

## Commands

```bash
# Setup
bash setup.sh                  # Creates venv, installs deps, validates .env
source venv/bin/activate       # Activate virtualenv (Linux/Mac)

# Run the pipeline
python -m src.cli run                              # Full pipeline (all 48 sources)
python -m src.cli run --source arbeitnow           # Single source
python -m src.cli run --dry-run --log-level DEBUG   # Dry run with debug
python -m src.cli run --db-path /tmp/test.db        # Custom DB path
python -m src.cli run --no-email                    # Skip notifications
python -m src.cli run --dashboard                   # Launch dashboard after

# Profile setup (personalise for any domain)
python -m src.cli setup-profile --cv path/to/cv.pdf                    # CV only
python -m src.cli setup-profile --cv cv.pdf --linkedin linkedin.zip    # CV + LinkedIn
python -m src.cli setup-profile --cv cv.pdf --github username          # CV + GitHub
python -m src.cli setup-profile --linkedin data.zip --github user      # All enrichment sources

# Other CLI commands
python -m src.cli dashboard    # Launch Streamlit UI
python -m src.cli status       # Last run stats
python -m src.cli sources      # List all 48 sources
python -m src.cli view --hours 24 --min-score 50   # Rich terminal table
python -m src.cli view --visa-only                  # Filter by visa

# Tests (all use mocked HTTP via aioresponses)
python -m pytest tests/ -v                              # Run all 376 tests
python -m pytest tests/test_scorer.py -v                # Scoring tests (58)
python -m pytest tests/test_sources.py -v               # All 48 sources (65)
python -m pytest tests/test_profile.py -v               # Profile system (56)
python -m pytest tests/test_linkedin_github.py -v       # LinkedIn/GitHub enrichment (54)
python -m pytest tests/test_scorer.py::test_name -v     # Single test
```

## Folder Structure

```
job360/
├── src/
│   ├── main.py              # Orchestrator: run_search(), SOURCE_REGISTRY (48), _build_sources()
│   ├── cli.py               # Click CLI: run, dashboard, status, sources, view, setup-profile
│   ├── cli_view.py          # Rich terminal table viewer (time-bucketed)
│   ├── dashboard.py         # Streamlit web dashboard with profile setup sidebar
│   ├── models.py            # Job dataclass with normalized_key() for dedup
│   ├── config/
│   │   ├── settings.py      # Env vars, paths, RATE_LIMITS (48 entries), thresholds
│   │   ├── keywords.py      # Default AI/ML keywords + KNOWN_SKILLS (326) + KNOWN_TITLE_PATTERNS
│   │   └── companies.py     # ATS company slugs (~104 companies across 10 ATS platforms)
│   ├── profile/
│   │   ├── models.py        # CVData, UserPreferences, UserProfile, SearchConfig dataclasses
│   │   ├── cv_parser.py     # PDF/DOCX text extraction, section detection, skill/title extraction
│   │   ├── preferences.py   # Form validation, CV+preferences merge
│   │   ├── storage.py       # JSON persistence at data/user_profile.json
│   │   ├── keyword_generator.py  # UserProfile → SearchConfig conversion
│   │   ├── linkedin_parser.py    # LinkedIn ZIP export parser (positions, skills, education)
│   │   └── github_enricher.py    # GitHub public API enricher (repos, languages, topics)
│   ├── sources/             # 47 source files (48 registry entries; indeed+glassdoor share one)
│   │   ├── base.py          # BaseJobSource ABC: retry, rate limiting, keyword properties
│   │   └── *.py             # One file per source implementation
│   ├── filters/
│   │   ├── skill_matcher.py # Scoring (score_job + JobScorer), visa detection, experience level
│   │   └── deduplicator.py  # Group by normalized_key, keep highest-scored
│   ├── storage/
│   │   ├── database.py      # Async SQLite (aiosqlite), jobs + run_log tables, auto-purge
│   │   └── csv_export.py    # CSV export per run
│   ├── notifications/
│   │   ├── base.py          # NotificationChannel ABC, get_configured_channels()
│   │   ├── email_notify.py  # Gmail SMTP (HTML + CSV attachment)
│   │   ├── slack_notify.py  # Slack Block Kit webhook
│   │   ├── discord_notify.py # Discord embed webhook
│   │   └── report_generator.py  # Markdown + HTML report templates
│   └── utils/
│       ├── logger.py        # Rotating file + console logging (5MB, 3 backups)
│       ├── rate_limiter.py  # Async semaphore + delay rate limiter
│       └── time_buckets.py  # Time bucketing for CLI view + console summary
├── tests/                   # 376 tests across 17 files
│   ├── conftest.py          # Shared fixtures (sample_ai_job, sample_visa_job, etc.)
│   └── test_*.py            # 17 test modules
├── data/                    # Runtime data (gitignored)
│   ├── jobs.db              # SQLite database
│   ├── user_profile.json    # User profile (optional)
│   ├── exports/             # CSV exports per run
│   ├── reports/             # Markdown reports per run
│   └── logs/                # Rotating log files
├── requirements.txt         # Production dependencies (12 packages)
├── requirements-dev.txt     # Test dependencies (includes prod via -r)
├── .env.example             # Template for API keys and webhooks
├── setup.sh                 # Setup script (Python 3.9+ check, venv, deps, .env validation)
└── cron_setup.sh            # Cron scheduling (4AM/4PM Europe/London)
```

## Architecture

The pipeline flows: **CLI (Click)** → **Orchestrator (`src/main.py`)** → **Sources (async fetch via `asyncio.gather`)** → **Scorer** → **Deduplicator** → **SQLite DB** → **Notifications + Reports + CSV**

### Key modules

- `src/main.py` — Central orchestrator with `run_search()` and `SOURCE_REGISTRY` dict (48 entries) mapping source names to classes. `_build_sources()` instantiates all sources with their config. Both `"indeed"` and `"glassdoor"` map to `JobSpySource`.
- `src/cli.py` — Click CLI with `run`, `dashboard`, `status`, `sources`, `view`, `setup-profile` commands. `setup-profile` accepts `--cv`, `--linkedin`, and `--github` options.
- `src/cli_view.py` — Rich terminal table viewer for browsing jobs from the DB
- `src/models.py` — `Job` dataclass with `normalized_key()` for dedup (strips company suffixes like Ltd/Inc/PLC and region suffixes like UK/US/EMEA, lowercases)
- `src/config/settings.py` — All env vars, paths, `RATE_LIMITS` dict (48 entries, per-source), thresholds. Constants: `MIN_MATCH_SCORE=30`, `MAX_RETRIES=3`, `RETRY_BACKOFF=[1,2,4]`, `REQUEST_TIMEOUT=30`.
- `src/config/keywords.py` — Default AI/ML keywords: `JOB_TITLES` (25), skills in 3 tiers (`PRIMARY_SKILLS` 15 / `SECONDARY_SKILLS` 17 / `TERTIARY_SKILLS` 11), `LOCATIONS` (24 UK + Remote/Hybrid), `RELEVANCE_KEYWORDS`, `NEGATIVE_TITLE_KEYWORDS` (60 entries across 12 categories), `KNOWN_SKILLS` (326-entry set for CV parsing), `KNOWN_TITLE_PATTERNS`. Used as fallback when no user profile exists.
- `src/config/companies.py` — ATS company slugs: Greenhouse (25), Lever (12), Workable (8), Ashby (9), SmartRecruiters (6), Pinpoint (8), Recruitee (8), Workday (15 — dict format with tenant/wd/site/name), Personio (10), SuccessFactors (3 — dict format with name/sitemap_url). ~104 companies total.
- `src/profile/` — Dynamic user profile system:
  - `models.py` — `CVData` (includes linkedin_positions, linkedin_skills, github_languages, github_topics, github_skills_inferred fields), `UserPreferences` (includes github_username), `UserProfile`, `SearchConfig` dataclasses. `SearchConfig.from_defaults()` returns the hard-coded AI/ML keywords.
  - `cv_parser.py` — PDF (pdfplumber) / DOCX (python-docx) text extraction, section detection, skill/title extraction using `KNOWN_SKILLS` and `KNOWN_TITLE_PATTERNS`
  - `preferences.py` — Validates/normalises form data, merges CV data with user preferences
  - `storage.py` — JSON persistence at `data/user_profile.json`
  - `keyword_generator.py` — Converts `UserProfile` → `SearchConfig` (auto-tiers skills, builds relevance keywords, generates search queries)
  - `linkedin_parser.py` — Parses LinkedIn data export ZIP (positions.csv, skills.csv, education.csv). `parse_linkedin_zip()` extracts structured data, `enrich_cv_from_linkedin()` merges into CVData.
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

**376 tests** across 17 test files. Shared fixtures in `tests/conftest.py` (provides `sample_ai_job`, `sample_unrelated_job`, `sample_duplicate_jobs`, `sample_visa_job`). All HTTP calls mocked with `aioresponses`. Uses `pytest-asyncio` for async tests.

Key test files:
- `test_sources.py` — 65 tests: all 48 sources with mocked HTTP responses
- `test_scorer.py` — 58 tests: scoring components, penalties, word boundaries, experience detection
- `test_profile.py` — 56 tests: SearchConfig defaults, UserProfile, CV parser, preferences, storage, keyword generator, JobScorer (including cross-domain scoring)
- `test_linkedin_github.py` — 54 tests: LinkedIn ZIP parsing, GitHub API enrichment, CVData merging
- `test_time_buckets.py` — 33 tests: time bucket grouping logic
- `test_models.py` — 19 tests: Job dataclass, normalization, salary sanitization
- `test_notifications.py` — 19 tests: Email, Slack, Discord sending
- `test_deduplicator.py` — 13 tests: dedup logic, company suffix stripping
- `test_cli.py` — 11 tests: CLI commands, `len(SOURCE_REGISTRY) == 48` assertion (update when adding/removing sources)
- `test_main.py` — 9 tests: orchestrator with mocked sources

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
- The `job360/` subdirectory is a stale copy — the active code is in the root `src/` and `tests/` directories

## Rules for Working on This Codebase

1. **Never touch `normalized_key()` in `models.py`** without verifying the deduplicator and database UNIQUE constraint still work. Changing normalization logic can cause duplicate entries or missed dedup.
2. **Never change `BaseJobSource`** (constructor, properties, retry logic, `_get_json`/`_post_json`/`_get_text`) without checking all 47 source files that inherit from it. Changes propagate to every source.
3. **Never touch database purge logic** (`purge_old_jobs` in `database.py`) without explicit confirmation. Incorrect purge thresholds can delete valid data.
4. **Always mock HTTP in tests** — never make live HTTP requests. Use `aioresponses` for all source and notification tests. The test suite must run offline.
5. **Always run the relevant test suite** after any change: `python -m pytest tests/ -v` for broad changes, or the specific test file for targeted changes.
6. **Read a file fully before editing it.** Understand the existing logic, imports, and how other modules depend on it.
7. **Check if something exists before creating it.** Search for existing implementations before adding new files or functions.
8. **When adding/removing sources:** Update `SOURCE_REGISTRY` dict, `_build_sources()` list, `RATE_LIMITS` dict, the test assertion `len(SOURCE_REGISTRY) == N` in `test_cli.py`, and the expected source set in the same file.
9. **Scoring changes require test verification.** The scoring algorithm is tested with 58 tests across edge cases. Run `test_scorer.py` and `test_profile.py` after any change to `skill_matcher.py`.

## Related Documentation

- `STATUS.md` — Project phase status, what's complete, what's next, known issues, test coverage table
- `ARCHITECTURE.md` — Deep technical reference: data flow diagrams, directory structure, scoring algorithm detail, source categories, database schema, config variables, dependency list
