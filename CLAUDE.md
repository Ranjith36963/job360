# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Job360 is an automated UK AI/ML job search system. It aggregates jobs from 24 sources, scores them 0-100 against a CV profile, deduplicates across sources, and delivers results via CLI, email, Slack, Discord, CSV, and a Streamlit dashboard.

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

- `src/main.py` — Central orchestrator with `run_search()` and `SOURCE_REGISTRY` dict mapping source names to classes. `_build_sources()` instantiates all sources with their config.
- `src/cli.py` — Click CLI with `run`, `dashboard`, `status`, `sources`, `view` commands
- `src/cli_view.py` — Rich terminal table viewer for browsing jobs from the DB
- `src/models.py` — `Job` dataclass with `normalized_key()` for dedup (strips company suffixes, lowercases)
- `src/config/settings.py` — All env vars, paths, rate limits, thresholds (loaded from `.env` via python-dotenv)
- `src/config/keywords.py` — `JOB_TITLES` (25), skills in 3 tiers (`PRIMARY_SKILLS`/`SECONDARY_SKILLS`/`TERTIARY_SKILLS`), `LOCATIONS`, `RELEVANCE_KEYWORDS`, `NEGATIVE_TITLE_KEYWORDS`
- `src/config/companies.py` — ATS company slugs for Greenhouse (19), Lever (10), Workable, Ashby, SmartRecruiters, Pinpoint, Recruitee, Workday

### Sources (`src/sources/`)

All sources extend `BaseJobSource` in `src/sources/base.py` which provides `_get_json()`, `_post_json()`, `_get_text()` with built-in retry (3 attempts, exponential backoff) and rate limiting. Sources are grouped in `_build_sources()`:

- **Keyed APIs**: Reed, Adzuna, JSearch, Jooble, Google Jobs (SerpApi) — skip gracefully when API key is empty
- **Free APIs**: Arbeitnow, RemoteOK, Jobicy, Himalayas, Remotive, DevITjobs, Landing.jobs — no config needed
- **ATS boards**: Greenhouse, Lever, Workable, Ashby, SmartRecruiters, Pinpoint, Recruitee, Workday — iterate over company slugs in `companies.py`
- **Scrapers**: LinkedIn (guest API), Indeed/Glassdoor (via python-jobspy)
- **Government**: FindAJob — UK GOV RSS feed

Each source uses `RELEVANCE_KEYWORDS` from `keywords.py` to filter irrelevant jobs before returning.

### Scoring (`src/filters/skill_matcher.py`)

Four components summing to 0-100: Title match (0-40), Skill match (0-40, primary=3pts/secondary=2pts/tertiary=1pt), Location (0-10), Recency (0-10). Minimum threshold is `MIN_MATCH_SCORE=30` in settings. Also detects visa sponsorship (`check_visa_flag`) and experience level from title.

### Deduplication (`src/filters/deduplicator.py`)

Groups by `job.normalized_key()` = (normalized company, normalized title). Keeps the best per group by highest `match_score`, then by data completeness (salary, description, location).

### Notifications (`src/notifications/`)

`NotificationChannel` ABC in `base.py` with auto-discovery via `get_configured_channels()`. Channels: Email (Gmail SMTP), Slack (Block Kit webhook), Discord (embed webhook). Each channel activates only when its env vars are set.

### Storage (`src/storage/`)

- `database.py` — Async SQLite (via aiosqlite) with `jobs` table (unique on normalized_company+normalized_title) and `run_log` table. Auto-purges jobs >30 days old.
- `csv_export.py` — CSV export per run to `data/exports/`

## Testing

Tests are in `tests/` with shared fixtures in `tests/conftest.py` (provides `sample_ai_job`, `sample_unrelated_job`, `sample_duplicate_jobs`, `sample_visa_job`). All HTTP calls are mocked with `aioresponses`. Uses `pytest-asyncio` for async tests.

## Environment

- Python 3.9+ required
- Dependencies: `requirements.txt` (prod), `requirements-dev.txt` (test — includes prod via `-r`)
- `.env` file for API keys and webhook URLs (see `.env.example`); free sources work without any keys
- Data outputs go to `data/` directory (gitignored): `data/exports/`, `data/reports/`, `data/logs/`, `data/jobs.db`

## Important Patterns

- **Adding a new job source:** Create class in `src/sources/`, extend `BaseJobSource`, implement `async fetch_jobs() -> list[Job]`. Register in `SOURCE_REGISTRY` dict and `_build_sources()` in `src/main.py`. Add rate limit entry in `RATE_LIMITS` dict in `settings.py`. Add mocked tests in `tests/test_sources.py`. If keyed, add env var to `settings.py` and `.env.example`.
- **Adding a new notification channel:** Implement `NotificationChannel` ABC, register in `get_all_channels()` in `src/notifications/base.py`
- **Keyed source pattern:** Accept `api_key` in `__init__`, return `[]` early with an info log if key is empty
- **Free source pattern:** Filter results using `RELEVANCE_KEYWORDS` on title+description, no auth needed
- **ATS source pattern:** Iterate over company slugs from `companies.py`, fetch each company's board API
- The `job360/` subdirectory is a stale copy — the active code is in the root `src/` and `tests/` directories
