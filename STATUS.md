# Job360 Project Status

## Current State: Phase 2 Complete

**Last updated:** 2026-03-17
**Total tests:** 376 across 17 test modules
**Source files:** 73+ Python modules | **Test files:** 17 test modules
**Job sources:** 48 registered in SOURCE_REGISTRY

---

## Phase 1: Dynamic User Profile System -- COMPLETE

**Goal:** Replace hard-coded AI/ML keywords with user-provided profile data so Job360 works for any profession (sales, law, engineering, hospitality, etc.).

### What was built

| Component | File(s) | Status |
|-----------|---------|--------|
| Profile dataclasses | `src/profile/models.py` | Done -- CVData, UserPreferences, UserProfile, SearchConfig |
| CV parser (PDF/DOCX) | `src/profile/cv_parser.py` | Done -- pdfplumber + python-docx, section detection, KNOWN_SKILLS matching |
| Preferences validator | `src/profile/preferences.py` | Done -- form validation, CV+prefs merge |
| Profile storage | `src/profile/storage.py` | Done -- JSON at `data/user_profile.json` |
| Keyword generator | `src/profile/keyword_generator.py` | Done -- UserProfile -> SearchConfig conversion |
| JobScorer class | `src/filters/skill_matcher.py` | Done -- dynamic scoring using SearchConfig |
| BaseJobSource properties | `src/sources/base.py` | Done -- `self.relevance_keywords`, `self.job_titles`, `self.search_queries` |
| 47 source file refactor | `src/sources/*.py` | Done -- all use `self.*` properties instead of direct imports |
| Orchestrator wiring | `src/main.py` | Done -- loads profile, creates scorer, passes config |
| Dashboard Profile UI | `src/dashboard.py` | Done -- sidebar expander with CV upload + form |
| CLI setup-profile | `src/cli.py` | Done -- interactive profile wizard |
| Profile tests | `tests/test_profile.py` | Done -- 56 tests covering all profile modules |
| Dependencies | `requirements.txt` | Done -- added pdfplumber, python-docx |

### Backward compatibility

- `keywords.py` is NOT modified -- remains the default keyword source
- All existing function signatures preserved (`score_job()`, `check_visa_flag()`, etc.)
- When no `data/user_profile.json` exists, behavior is **identical** to pre-Phase-1
- `len(SOURCE_REGISTRY) == 48` test assertion unchanged
- All original tests pass without modification

---

## Phase 2: LinkedIn ZIP + GitHub API -- COMPLETE

**Goal:** Enrich user profiles with LinkedIn data export and GitHub public repos.

### What was built

| Component | File(s) | Status |
|-----------|---------|--------|
| LinkedIn ZIP parser | `src/profile/linkedin_parser.py` | Done -- parses positions.csv, skills.csv, education.csv from ZIP |
| LinkedIn CVData enrichment | `src/profile/linkedin_parser.py:enrich_cv_from_linkedin()` | Done -- merges LinkedIn data into CVData |
| GitHub API enricher | `src/profile/github_enricher.py` | Done -- fetches repos, languages, topics; infers skills |
| GitHub CVData enrichment | `src/profile/github_enricher.py:enrich_cv_from_github()` | Done -- merges GitHub data into CVData |
| CVData model fields | `src/profile/models.py` | Done -- linkedin_positions, linkedin_skills, linkedin_industry, github_languages, github_topics, github_skills_inferred |
| UserPreferences field | `src/profile/models.py` | Done -- github_username field |
| CLI --linkedin option | `src/cli.py:setup-profile` | Done -- accepts LinkedIn ZIP path |
| CLI --github option | `src/cli.py:setup-profile` | Done -- accepts GitHub username |
| GITHUB_TOKEN env var | `src/config/settings.py`, `.env.example` | Done -- optional, for higher API rate limits |
| LinkedIn/GitHub tests | `tests/test_linkedin_github.py` | Done -- 54 tests |

### How it works

1. User runs `setup-profile --cv cv.pdf --linkedin export.zip --github username`
2. CV parsed first (existing Phase 1 flow)
3. LinkedIn ZIP parsed: positions, skills, education extracted from CSVs
4. GitHub repos fetched: languages and topics mapped to skills via LANGUAGE_TO_SKILL dict
5. Both merged into CVData via `enrich_cv_from_linkedin()` and `enrich_cv_from_github()`
6. Combined CVData + preferences saved as UserProfile
7. On next pipeline run, all enrichment data feeds into SearchConfig generation

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
- 376 tests pass (3 skip on Windows)

---

## What Is Fragile or Risky

| Source/Component | Risk | Notes |
|------------------|------|-------|
| **HTML scrapers** (7) | High | LinkedIn, JobTensor, Climatebase, 80000Hours, BCS Jobs, AIJobs Global, AIJobs AI all use regex parsing on HTML. Any layout change breaks them silently (returns 0 jobs, no error). |
| **python-jobspy** (Indeed/Glassdoor) | Medium | Not in requirements.txt. Optional dependency. If Indeed/Glassdoor change their site, python-jobspy breaks. |
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
| `job360/` stale subdirectory | Low | Old copy of code, active code is in root `src/` and `tests/`. Should be deleted. |
| CV parser section detection | Low | Regex-based — may miss non-standard CV formats. Works for ~80% of CVs |
| No skill inference | Medium | Profile system only extracts explicitly listed skills. Users must add related skills via preferences |
| python-jobspy not in requirements.txt | Low | Intentionally optional (heavy dependencies). Indeed/Glassdoor source skips with warning if not installed. |
| GITHUB_TOKEN optional | Low | Without token, GitHub API rate limit is 60 req/hr. With token: 5000 req/hr. Profile enrichment may fail for users with many repos without a token. |

---

## Test Coverage by Module

| Test file | Module tested | Tests |
|-----------|--------------|-------|
| `test_sources.py` | All 48 sources | 65 |
| `test_scorer.py` | `skill_matcher.py` scoring | 58 |
| `test_profile.py` | `src/profile/*`, `JobScorer` | 56 |
| `test_linkedin_github.py` | LinkedIn parser, GitHub enricher | 54 |
| `test_time_buckets.py` | `time_buckets.py` | 33 |
| `test_models.py` | `models.py` Job dataclass | 19 |
| `test_notifications.py` | Slack + Discord + Email channels | 19 |
| `test_deduplicator.py` | `deduplicator.py` | 13 |
| `test_cli.py` | `cli.py` commands + SOURCE_REGISTRY | 11 |
| `test_main.py` | `main.py` orchestrator | 9 |
| `test_notification_base.py` | Channel base + discovery | 7 |
| `test_reports.py` | Report generation | 6 |
| `test_database.py` | SQLite database | 6 |
| `test_setup.py` | setup.sh + requirements | 6 |
| `test_cli_view.py` | `cli_view.py` | 5 |
| `test_cron.py` | cron_run.sh | 5 |
| `test_csv_export.py` | CSV export | 4 |
| **Total** | | **376** (3 skip on Windows) |

### Not covered or lightly covered

- `src/dashboard.py` — Streamlit UI is not unit-tested (would need Streamlit testing framework)
- `src/utils/rate_limiter.py` — rate limiting tested indirectly through source tests, no dedicated tests
- Live HTTP behavior — all tests use mocked responses, so real API format changes are not caught by tests
- Profile dashboard sidebar — interactive Streamlit profile form is not tested
- Edge cases in LinkedIn ZIP parsing — malformed ZIPs, missing CSVs tested but exotic edge cases possible

---

## Quick Verification

```bash
# All tests pass
python -m pytest tests/ -v

# Profile setup works (all enrichment sources)
python -m src.cli setup-profile --cv path/to/cv.pdf --linkedin export.zip --github username

# Pipeline with profile
python -m src.cli run --dry-run --log-level DEBUG
# Log: "Using dynamic keywords from user profile"

# Pipeline without profile
rm data/user_profile.json
python -m src.cli run --dry-run --log-level DEBUG
# Log: "No user profile found, using default keywords"

# Check source count
python -c "from src.main import SOURCE_REGISTRY; print(len(SOURCE_REGISTRY))"
# Output: 48
```
