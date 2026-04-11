# Job360 Project Status

## Current State: Phase 2 Complete

**Last updated:** 2026-04-11
**Total tests:** 412 across 21 test modules
**Source files:** 47 source files in `backend/src/sources/` (excluding `__init__.py` and `base.py`) | **Test files:** 21 test modules
**Job sources:** 48 registered in SOURCE_REGISTRY (47 source files; indeed.py handles both Indeed + Glassdoor)

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
| Dashboard Profile UI | `backend/src/dashboard.py` | Done -- sidebar expander with CV upload + form |
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

## Phase 2: LinkedIn ZIP + GitHub API -- COMPLETE

**Goal:** Enrich user profiles with LinkedIn data export and GitHub public repos.

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
| DB error logging | `backend/src/cli_view.py`, `backend/src/dashboard.py` | Done -- `except Exception` blocks now log errors before returning empty |
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

## What Is Working Right Now

- Full 48-source pipeline runs end-to-end (async fetch, score, dedup, store, notify)
- Profile system: CV + LinkedIn + GitHub enrichment → dynamic keywords → personalised search
- All 7 keyed APIs skip gracefully when keys are empty
- All 10 ATS boards iterate over ~104 company slugs
- All 8 RSS/XML feeds parse correctly with mocked data
- All 7 HTML scrapers extract job data with regex
- Scoring produces 0-100 with correct penalties
- Deduplication merges same jobs from different sources
- SQLite database with auto-purge (30 days)
- Email, Slack, Discord notifications (when configured)
- CLI commands: run, view, dashboard, status, sources, setup-profile
- Streamlit dashboard with filters, charts, profile setup
- 412 tests pass (3 skip on Windows)

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
| CV parser section detection | Low | Regex-based — may miss non-standard CV formats. Works for ~80% of CVs |
| No skill inference | Medium | Profile system only extracts explicitly listed skills. Users must add related skills via preferences |
| python-jobspy not in backend/pyproject.toml | Low | Intentionally optional (heavy dependencies). Indeed/Glassdoor source skips with warning if not installed. |
| GITHUB_TOKEN optional | Low | Without token, GitHub API rate limit is 60 req/hr. With token: 5000 req/hr. Profile enrichment may fail for users with many repos without a token. |
| pdfminer/cryptography conflict | Low | Environment-specific: pyo3 panic in cryptography lib breaks pdfplumber import in some environments |

---

## Test Coverage by Module

| Test file | Module tested | Tests |
|-----------|--------------|-------|
| `test_sources.py` | All 48 sources | 71 |
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
| `test_dashboard.py` | URL sanitization (`_safe_url`) for XSS prevention | 6 |
| `test_rate_limiter.py` | `rate_limiter.py` | 5 |
| `test_cron.py` | cron_run.sh | 5 |
| `test_cli_view.py` | `cli_view.py` | 5 |
| `test_csv_export.py` | CSV export | 4 |
| **Total** | | **412** (3 skip on Windows) |

### Not covered or lightly covered

- `backend/src/dashboard.py` — Streamlit UI is not unit-tested (would need Streamlit testing framework)
- `backend/src/utils/rate_limiter.py` — now has 5 dedicated tests in `test_rate_limiter.py`
- Live HTTP behavior — all tests use mocked responses, so real API format changes are not caught by tests
- Profile dashboard sidebar — interactive Streamlit profile form is not tested
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
# Output: 48
```
