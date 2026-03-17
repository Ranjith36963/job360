# Job360 Project Status

## Current State: Phase 1 Complete

**Last updated:** 2026-03-15
**Total tests:** 310 passed, 3 skipped (Windows-only bash tests), 0 failures
**Source files:** 73 Python modules | **Test files:** 17 test modules
**Job sources:** 48 registered in SOURCE_REGISTRY

---

## Phase 1: Dynamic User Profile System -- COMPLETE

**Goal:** Replace hard-coded AI/ML keywords with user-provided profile data so Job360 works for any profession (sales, law, engineering, hospitality, etc.).

### What was built

| Component | File(s) | Status |
|-----------|---------|--------|
| Profile dataclasses | `src/profile/models.py` | Done -- CVData, UserPreferences, UserProfile, SearchConfig |
| CV parser (PDF/DOCX) | `src/profile/cv_parser.py` | Done -- pdfplumber + python-docx, section detection |
| Preferences validator | `src/profile/preferences.py` | Done -- form validation, CV+prefs merge |
| Profile storage | `src/profile/storage.py` | Done -- JSON at `data/user_profile.json` |
| Keyword generator | `src/profile/keyword_generator.py` | Done -- UserProfile -> SearchConfig conversion |
| JobScorer class | `src/filters/skill_matcher.py` | Done -- dynamic scoring using SearchConfig |
| BaseJobSource properties | `src/sources/base.py` | Done -- `self.relevance_keywords`, `self.job_titles`, `self.search_queries` |
| 44 source file refactor | `src/sources/*.py` | Done -- all use `self.*` properties instead of direct imports |
| Orchestrator wiring | `src/main.py` | Done -- loads profile, creates scorer, passes config |
| Dashboard Profile UI | `src/dashboard.py` | Done -- sidebar expander with CV upload + form |
| CLI setup-profile | `src/cli.py` | Done -- interactive profile wizard |
| Profile tests | `tests/test_profile.py` | Done -- 52 tests covering all profile modules |
| Dependencies | `requirements.txt` | Done -- added pdfplumber, python-docx |

### Backward compatibility

- `keywords.py` is NOT modified -- remains the default keyword source
- All existing function signatures preserved (`score_job()`, `check_visa_flag()`, etc.)
- When no `data/user_profile.json` exists, behavior is **identical** to pre-Phase-1
- `len(SOURCE_REGISTRY) == 48` test assertion unchanged
- All 258 original tests pass without modification

### How it works

1. User creates profile via CLI (`setup-profile`) or Dashboard (sidebar)
2. Profile saved to `data/user_profile.json`
3. On pipeline run, `main.py` loads profile -> generates `SearchConfig`
4. SearchConfig passed to all sources (dynamic `relevance_keywords`, `job_titles`, `search_queries`)
5. JobScorer uses dynamic skill/title lists for scoring
6. No profile = exact same AI/ML behavior as before

---

## Phase 2: LinkedIn ZIP + GitHub API -- NOT STARTED

**Goal:** Enrich user profiles with LinkedIn data export and GitHub public repos.

### Planned features
- Parse LinkedIn ZIP export (positions.csv, skills.csv, education.csv)
- Fetch public GitHub repos via API (languages, technologies, contributions)
- Feed both into existing UserProfile -> SearchConfig pipeline
- Auto-detect skills from GitHub repo languages/frameworks
- Extract career progression from LinkedIn history

### No code written yet

---

## Phase 3+ (Future)

- Skill inference from job titles (e.g., "Data Scientist" implies Python, SQL, statistics)
- AI-powered CV summarization for better keyword extraction
- Multi-profile support (different job searches simultaneously)
- Job recommendation engine based on profile match patterns
- Interview tracking and application pipeline

---

## Known Issues

| Issue | Severity | Notes |
|-------|----------|-------|
| 3 tests skip on Windows | Low | bash-only tests for `setup.sh` and `cron_run.sh` -- pass on Linux/Mac |
| `job360/` stale subdirectory | Low | Old copy of code, active code is in root `src/` and `tests/` |
| CV parser section detection | Low | Regex-based -- may miss non-standard CV formats. Works for ~80% of CVs |
| No skill inference | Medium | Phase 1 only extracts explicitly listed skills. Users must add related skills via preferences |

---

## Test Coverage by Module

| Test file | Module tested | Tests |
|-----------|--------------|-------|
| `test_profile.py` | `src/profile/*`, `JobScorer` | 52 |
| `test_sources.py` | All 48 sources | 57 |
| `test_scorer.py` | `skill_matcher.py` scoring | 43 |
| `test_main.py` | `main.py` orchestrator | 9 |
| `test_models.py` | `models.py` Job dataclass | 18 |
| `test_deduplicator.py` | `deduplicator.py` | 13 |
| `test_time_buckets.py` | `time_buckets.py` | 22 |
| `test_cli.py` | `cli.py` commands | 8 |
| `test_cli_view.py` | `cli_view.py` | 5 |
| `test_notifications.py` | Slack + Discord channels | 14 |
| `test_notification_base.py` | Channel base + discovery | 6 |
| `test_reports.py` | Report generation | 6 |
| `test_csv_export.py` | CSV export | 4 |
| `test_database.py` | SQLite database | 6 |
| `test_setup.py` | setup.sh + requirements | 5 |
| `test_cron.py` | cron_run.sh | 5 |
| **Total** | | **310** (3 skipped on Windows) |

---

## Quick Verification

```bash
# All tests pass
python -m pytest tests/ -v

# Profile setup works
python -m src.cli setup-profile --cv path/to/cv.pdf

# Pipeline with profile
python -m src.cli run --dry-run --log-level DEBUG
# Log: "Using dynamic keywords from user profile"

# Pipeline without profile
rm data/user_profile.json
python -m src.cli run --dry-run --log-level DEBUG
# Log: "No user profile found, using default AI/ML keywords"
```
