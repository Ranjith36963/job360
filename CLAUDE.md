# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Job360 is an automated UK job search system supporting **any professional domain**. It aggregates jobs from 48 sources (via `SOURCE_REGISTRY` in `src/main.py`), scores them 0-100 against a user profile, deduplicates across sources, and delivers results via CLI, email, Slack, Discord, CSV, and a Streamlit dashboard. When a user profile exists (`data/user_profile.json`), keywords are generated dynamically from CV + preferences via `SearchConfig`. Without a profile, it defaults to the original AI/ML keywords.

## Commands

```bash
# Setup
bash setup.sh                  # Creates venv, installs deps, validates .env
source venv/bin/activate       # Activate virtualenv (Linux/Mac)

# Run the pipeline
python -m src.cli run                              # Full pipeline
python -m src.cli run --source arbeitnow           # Single source
python -m src.cli run --dry-run --log-level DEBUG   # Dry run with debug
python -m src.cli run --db-path /tmp/test.db        # Custom DB path
python -m src.cli run --no-email                    # Skip notifications
python -m src.cli run --dashboard                   # Launch dashboard after

# Profile setup (personalise for any domain)
python -m src.cli setup-profile --cv path/to/cv.pdf   # Interactive profile wizard

# Other CLI commands
python -m src.cli dashboard    # Launch Streamlit UI
python -m src.cli status       # Last run stats
python -m src.cli sources      # List all sources
python -m src.cli view --hours 24 --min-score 50   # Rich terminal table
python -m src.cli view --visa-only                  # Filter by visa

# Tests (all use mocked HTTP via aioresponses)
python -m pytest tests/ -v                  # Run all tests
python -m pytest tests/test_scorer.py -v    # Single test file
python -m pytest tests/test_scorer.py::test_name -v  # Single test
```

## Architecture

The pipeline flows: **CLI (Click)** → **Orchestrator (`src/main.py`)** → **Sources (async fetch via `asyncio.gather`)** → **Scorer** → **Deduplicator** → **SQLite DB** → **Notifications + Reports + CSV**

### Key modules

- `src/main.py` — Central orchestrator with `run_search()` and `SOURCE_REGISTRY` dict (48 entries) mapping source names to classes. `_build_sources()` instantiates all sources with their config.
- `src/cli.py` — Click CLI with `run`, `dashboard`, `status`, `sources`, `view`, `setup-profile` commands
- `src/cli_view.py` — Rich terminal table viewer for browsing jobs from the DB
- `src/models.py` — `Job` dataclass with `normalized_key()` for dedup (strips company suffixes, lowercases)
- `src/config/settings.py` — All env vars, paths, `RATE_LIMITS` dict (per-source), thresholds (loaded from `.env` via python-dotenv)
- `src/config/keywords.py` — Default AI/ML keywords: `JOB_TITLES` (25), skills in 3 tiers (`PRIMARY_SKILLS`/`SECONDARY_SKILLS`/`TERTIARY_SKILLS`), `LOCATIONS`, `RELEVANCE_KEYWORDS`, `NEGATIVE_TITLE_KEYWORDS`. Used as fallback when no user profile exists.
- `src/config/companies.py` — ATS company slugs for Greenhouse, Lever, Workable, Ashby, SmartRecruiters, Pinpoint, Recruitee, Workday, Personio, SuccessFactors
- `src/profile/` — Dynamic user profile system:
  - `models.py` — `CVData`, `UserPreferences`, `UserProfile`, `SearchConfig` dataclasses. `SearchConfig.from_defaults()` returns the hard-coded AI/ML keywords.
  - `cv_parser.py` — PDF (pdfplumber) / DOCX (python-docx) text extraction, section detection, skill/title extraction
  - `preferences.py` — Validates/normalises form data, merges CV data with user preferences
  - `storage.py` — JSON persistence at `data/user_profile.json`
  - `keyword_generator.py` — Converts `UserProfile` → `SearchConfig` (auto-tiers skills, builds relevance keywords, generates search queries)

### Sources (`src/sources/`)

All sources extend `BaseJobSource` in `src/sources/base.py` which provides `_get_json()`, `_post_json()`, `_get_text()` with built-in retry (3 attempts, exponential backoff) and rate limiting. The `_is_uk_or_remote()` helper (also in `base.py`) checks location strings against `UK_TERMS`, `REMOTE_TERMS`, and `FOREIGN_INDICATORS` from `skill_matcher.py`. Sources are grouped in `_build_sources()`:

- **Keyed APIs** (7): Reed, Adzuna, JSearch, Jooble, Google Jobs (SerpApi), Careerjet, Findwork — skip gracefully when API key is empty
- **Free JSON APIs** (10): Arbeitnow, RemoteOK, Jobicy, Himalayas, Remotive, DevITjobs, Landing.jobs, AIJobs.net, HN Jobs (Firebase), YC Companies
- **ATS boards** (10): Greenhouse, Lever, Workable, Ashby, SmartRecruiters, Pinpoint, Recruitee, Workday, Personio, SuccessFactors — iterate over company slugs in `companies.py`
- **RSS/XML feeds** (8): jobs.ac.uk, NHS Jobs, WorkAnywhere, WeWorkRemotely, RealWorkFromAnywhere, BioSpace, University Jobs (6 UK unis), FindAJob (UK GOV)
- **HTML scrapers** (7): LinkedIn, JobTensor, Climatebase, 80000Hours, BCS Jobs, AIJobs Global, AIJobs AI
- **Other** (4): Indeed/Glassdoor (python-jobspy), HackerNews (Algolia "Who is Hiring"), TheMuse, NoFluffJobs
- **Market intelligence** (1): Nomis (UK GOV vacancy statistics, not individual listings)

Each source uses `self.relevance_keywords` (property on `BaseJobSource`) to filter irrelevant jobs. This returns dynamic keywords from `SearchConfig` when a profile exists, or falls back to the hard-coded `RELEVANCE_KEYWORDS` from `keywords.py`. Sources with custom queries (JSearch, LinkedIn, FindAJob, NHS Jobs) also check `self.search_queries` before using their hard-coded query lists.

### Scoring (`src/filters/skill_matcher.py`)

Four components summing to 0-100: Title match (0-40), Skill match (0-40, primary=3pts/secondary=2pts/tertiary=1pt), Location (0-10), Recency (0-10). Penalties: Negative title keywords (-30), Foreign location (-15). Minimum threshold is `MIN_MATCH_SCORE=30` in settings. Also detects visa sponsorship (`check_visa_flag`) and experience level from title. Two scoring paths: `score_job()` (module-level, uses hard-coded keywords) and `JobScorer(config).score()` (instance-based, uses dynamic `SearchConfig`). The orchestrator selects based on whether a user profile exists.

### Deduplication (`src/filters/deduplicator.py`)

Groups by `job.normalized_key()` = (normalized company, normalized title). Keeps the best per group by highest `match_score`, then by data completeness (salary, description, location).

### Notifications (`src/notifications/`)

`NotificationChannel` ABC in `base.py` with auto-discovery via `get_configured_channels()`. Channels: Email (Gmail SMTP), Slack (Block Kit webhook), Discord (embed webhook). Each channel activates only when its env vars are set.

### Storage (`src/storage/`)

- `database.py` — Async SQLite (via aiosqlite) with `jobs` table (unique on normalized_company+normalized_title) and `run_log` table. Auto-purges jobs >30 days old.
- `csv_export.py` — CSV export per run to `data/exports/`

## Testing

**310 tests** across 17 test files. Shared fixtures in `tests/conftest.py` (provides `sample_ai_job`, `sample_unrelated_job`, `sample_duplicate_jobs`, `sample_visa_job`). All HTTP calls mocked with `aioresponses`. Uses `pytest-asyncio` for async tests.

Key test files:
- `test_profile.py` — 52 tests: SearchConfig defaults, UserProfile, CV parser, preferences, storage, keyword generator, JobScorer (including cross-domain scoring)
- `test_sources.py` — 57 tests: all 48 sources with mocked HTTP responses
- `test_scorer.py` — 43 tests: scoring components, penalties, word boundaries, experience detection
- `test_main.py` — 9 tests: orchestrator with mocked sources
- `test_models.py` — 18 tests: Job dataclass, normalization, salary sanitization
- `test_deduplicator.py` — 13 tests: dedup logic, company suffix stripping
- `test_cli.py` — 8 tests: CLI commands, `len(SOURCE_REGISTRY) == 48` assertion (update when adding/removing sources)

## Environment

- Python 3.9+ required
- Dependencies: `requirements.txt` (prod), `requirements-dev.txt` (test — includes prod via `-r`)
- `.env` file for API keys and webhook URLs (see `.env.example`); free sources work without any keys
- Data outputs go to `data/` directory (gitignored): `data/exports/`, `data/reports/`, `data/logs/`, `data/jobs.db`, `data/user_profile.json`

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

## Related Documentation

- `STATUS.md` — Project phase status, what's complete, what's next, known issues, test coverage table
- `ARCHITECTURE.md` — Deep technical reference: data flow diagrams, directory structure, scoring algorithm detail, source categories, database schema, config variables, dependency list
