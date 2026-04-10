# Dead Code Report — Job360

**Generated:** 2026-04-10
**Scope:** `src/`, `tests/`, top-level docs (excluding `.claude/worktrees/` which are git worktree copies)
**Method:** Parallel static analysis using `vulture 2.16` (AST), `ruff` (F401/F811/F841), and cross-reference grep
**Status:** Report only — no files modified.

---

## Executive Summary

The three recent LLM-migration commits (`804725c`, `8c4ed82`, `3ba1342`) were surgical on the CV-parsing side but left behind two kinds of debris:

1. **Import-level leftovers** — 34 unused `aiohttp` imports across `src/sources/` (sources no longer fetch HTTP directly; they use `BaseJobSource._get_json` / `_get_text`).
2. **A parallel scoring code path** — `score_job()` + `PRIMARY/SECONDARY/TERTIARY_SKILLS` are technically reachable but practically dead because `SearchConfig.from_defaults()` now returns empty lists.

The docs haven't caught up to either change — test counts, file counts, and the "hard-coded AI/ML keywords" phrasing are all wrong in 4 of the project's `.md` files.

### Scoreboard

| Category                            | Count              | Risk                                                       |
| ----------------------------------- | ------------------ | ---------------------------------------------------------- |
| Unused imports (ruff F401)          | **56**             | Zero — purely cosmetic, auto-fixable                       |
| Unused local variables (ruff F841)  | 2                  | Zero                                                       |
| Unused functions/classes (vulture)  | 2                  | Low — verify no external importers before deleting        |
| Unused test fixtures                | 4                  | Low                                                        |
| Unused test module constants        | 2                  | Low                                                        |
| Truly dead config constants         | 1                  | Low                                                        |
| "Ghost" constants (already removed) | 2                  | Already clean in code; docs still reference                |
| Dead-in-spirit constants            | 6                  | **Design decision** — removing requires killing legacy path |
| Orphan Python files                 | **0**              | —                                                          |
| Orphan tests                        | **0**              | —                                                          |
| Unused env vars in `.env.example`   | **0**              | —                                                          |
| Stale doc claims                    | **40+** across 4 files | Zero code impact, high confusion impact                |

### Tool output totals

- **Vulture (80% confidence):** 4 findings (3 are standard `__exit__` parameters, 1 is a `PropertyMock` import)
- **Vulture (60% confidence):** 104 findings total (100 additional beyond 80%), ~70 are false positives (Pydantic fields, Click commands, FastAPI routes, mock setters, `row_factory`)
- **Ruff F401 (unused imports):** 56 findings (36 in `src/`, 20 in `tests/`)
- **Ruff F811/F841/F501:** 2 findings (both F841)
- **57 of 59 ruff findings are auto-fixable** via `ruff check --fix`

---

## 1. High-Confidence Dead Code in `src/`

### 1.1 Unused Imports — Systematic `aiohttp` Leftover (34 files)

Every source file below imports `aiohttp` but never uses it directly — all HTTP calls now go through `BaseJobSource._get_json` / `_get_text`. This is a mechanical leftover from a past refactor.

```
src/sources/aijobs.py:4
src/sources/aijobs_ai.py:5
src/sources/aijobs_global.py:5
src/sources/arbeitnow.py:4
src/sources/bcs_jobs.py:5
src/sources/biospace.py:5
src/sources/climatebase.py:6
src/sources/devitjobs.py:4
src/sources/eightykhours.py:5
src/sources/hackernews.py:5
src/sources/himalayas.py:4
src/sources/hn_jobs.py:5
src/sources/jobicy.py:4
src/sources/jobs_ac_uk.py:5
src/sources/jobtensor.py:6
src/sources/landingjobs.py:4
src/sources/nhs_jobs.py:5
src/sources/nofluffjobs.py:4
src/sources/nomis.py:4
src/sources/realworkfromanywhere.py:5
src/sources/remoteok.py:4
src/sources/remotive.py:4
src/sources/themuse.py:5
src/sources/uni_jobs.py:5
src/sources/weworkremotely.py:5
src/sources/workanywhere.py:6
src/sources/yc_companies.py:4
```

**Safe to fix:** `ruff check --fix --select F401 src/sources/`

### 1.2 Other Unused Imports in `src/`

| File:Line                               | Symbol                  |
| --------------------------------------- | ----------------------- |
| `src/api/routes/profile.py:8`           | `Depends`               |
| `src/api/routes/profile.py:10`          | `get_db`                |
| `src/cli.py:121`                        | `profile_exists`        |
| `src/dashboard.py:39`                   | `EXPORTS_DIR`           |
| `src/dashboard.py:432`                  | `asyncio`               |
| `src/main.py:7`                         | `Path`                  |
| `src/notifications/report_generator.py:5` | `format_relative_time`  |
| `src/utils/logger.py:5`                 | `Path`                  |
| `src/utils/time_buckets.py:3`           | `re`                    |
| `src/utils/time_buckets.py:4`           | `timedelta`             |

### 1.3 Unused Imports in `tests/` (20 total)

| File:Line                           | Symbol(s)                                                        |
| ----------------------------------- | ---------------------------------------------------------------- |
| `tests/test_cli.py:1`               | `patch`, `MagicMock`                                             |
| `tests/test_cli.py:101`             | `CVData`                                                         |
| `tests/test_cron.py:1`              | `os`                                                             |
| `tests/test_dashboard.py:3`         | `PropertyMock`                                                   |
| `tests/test_linkedin_github.py:7`   | `fields` (from dataclasses)                                      |
| `tests/test_linkedin_github.py:8`   | `Path`                                                           |
| `tests/test_linkedin_github.py:13`  | `SearchConfig`                                                   |
| `tests/test_linkedin_github.py:18`  | `_find_csv_in_zip`                                               |
| `tests/test_linkedin_github.py:29`  | `LANGUAGE_TO_SKILL`                                              |
| `tests/test_linkedin_github.py:30`  | `TOPIC_TO_SKILL`                                                 |
| `tests/test_llm_provider.py:3`      | `MagicMock`                                                      |
| `tests/test_models.py:1`            | `datetime`, `timezone`                                           |
| `tests/test_profile.py:3-8`         | `json`, `os`, `tempfile`, `Path`, `AsyncMock` (5 imports)        |
| `tests/test_rate_limiter.py:4`      | `pytest`                                                         |

### 1.4 Genuinely Unused Functions/Classes (Vulture-verified)

- **`src/utils/logger.py:37` — `JSONFormatter` class** — defined but never instantiated or imported anywhere. The module only exports `setup_logging`.
- **`src/utils/logger.py:51` — `get_logger()`** — defined but never called. All modules use `logging.getLogger("job360.xxx")` directly.

### 1.5 Unused Local Variables (ruff F841)

- **`src/sources/careerjet.py:65`** — `salary = item.get("salary", "")` — assigned, never read. Only `salary_min`/`salary_max` are used further down.
- **`tests/test_main.py:239`** — `stats` — assigned but never asserted.

### 1.6 Unused Test Fixtures in `tests/conftest.py`

Four fixtures defined but zero test files reference them as parameters:

- `sample_unrelated_job` (line 28)
- `sample_duplicate_jobs` (line 41)
- `sample_non_uk_job` (line 69)
- `sample_empty_description_job` (line 82)

Note: `CLAUDE.md` lists these as "provides..." in conftest, but grep confirms zero test-file references them as fixture arguments.

### 1.7 Unused Module-Level Test Constants

- `tests/test_sources.py:1170` — `HN_JOBS_ITEM_2` — defined but never referenced
- `tests/test_sources.py:1747` — `AIJOBS_GLOBAL_HTML` — defined but never referenced

---

## 2. Dead Configuration Constants

### 2.1 Truly Dead (1 finding)

- **`src/config/settings.py:45` — `MAX_DAYS_OLD = 7`**
  Zero references anywhere in `src/` or `tests/`. Only echoed in `ARCHITECTURE.md:553`. Auto-purge uses a hard-coded `days=30` literal in `src/storage/database.py::purge_old_jobs()` instead.

### 2.2 Ghost Constants — Already Removed, Only Docs Lag

Both were removed in commit `3ba1342` ("chore: remove KNOWN_SKILLS (300+) and KNOWN_TITLE_PATTERNS (68) — replaced by LLM"):

- **`src/config/keywords.py::KNOWN_SKILLS`** — fully removed. Lines 183-187 contain only a removal comment. Zero Python references remain.
- **`src/config/keywords.py::KNOWN_TITLE_PATTERNS`** — same: removed, zero Python references.

The code is clean; only the docs in `CLAUDE.md`, `README.md`, `STATUS.md`, and `ARCHITECTURE.md` still mention them. See Section 4.

### 2.3 Dead-in-Spirit — Live Only on a Path Nothing Exercises

These constants exist and are imported, but the only code that reaches them is the legacy `score_job()` fallback, which runs only when `profile.is_complete` is False. And `SearchConfig.from_defaults()` at `src/profile/models.py:74-94` now returns **empty** skill/title lists, so the "no profile" branch was effectively neutralized.

| Constant                    | Entries | Only consumer(s)                                             |
| --------------------------- | ------- | ------------------------------------------------------------ |
| `JOB_TITLES`                | 25      | `skill_matcher._title_score`, `base._DEFAULT_JOB_TITLES`    |
| `PRIMARY_SKILLS`            | 15      | `skill_matcher._skill_score`, `time_buckets`                |
| `SECONDARY_SKILLS`          | 17      | `skill_matcher._skill_score`, `time_buckets`                |
| `TERTIARY_SKILLS`           | 11      | `skill_matcher._skill_score`, `time_buckets`                |
| `NEGATIVE_TITLE_KEYWORDS`   | 60      | `skill_matcher._negative_penalty` (legacy path only)        |
| `RELEVANCE_KEYWORDS`        | ~34     | `base._DEFAULT_RELEVANCE_KEYWORDS` (no-profile fallback)    |

**Interpretation:** Commit `3ba1342` deleted the CV-parsing lists (`KNOWN_SKILLS`, `KNOWN_TITLE_PATTERNS`) but left the *scoring* lists behind. If the intent was a full LLM migration, the legacy `score_job()`, `_title_score`, `_skill_score`, `_negative_penalty`, and the `_DEFAULT_*` fallbacks in `sources/base.py` are all candidates for removal — at which point these six constants become deletable.

**This is a design decision, not a mechanical cleanup.** Either:
- **Option A:** Fully commit to the LLM path — delete `score_job()`, `_title_score`, `_skill_score`, `_negative_penalty`, `_DEFAULT_*` fallbacks, plus these 6 constants and their tests.
- **Option B:** Restore meaningful defaults — repopulate `SearchConfig.from_defaults()` with generic keywords so the no-profile fallback is actually functional again.

Right now the code is in limbo between the two.

### 2.4 All ATS Company Slug Lists — Confirmed LIVE

Every one of the 9 company lists in `src/config/companies.py` is consumed by its matching source file. Nothing to prune:

| Constant                 | Entries | Consumer                          |
| ------------------------ | ------- | --------------------------------- |
| `GREENHOUSE_COMPANIES`   | 25      | `src/sources/greenhouse.py`       |
| `LEVER_COMPANIES`        | 12      | `src/sources/lever.py`            |
| `WORKABLE_COMPANIES`     | 8       | `src/sources/workable.py`         |
| `ASHBY_COMPANIES`        | 9       | `src/sources/ashby.py`            |
| `SMARTRECRUITERS_COMPANIES` | 6    | `src/sources/smartrecruiters.py`  |
| `PINPOINT_COMPANIES`     | 8       | `src/sources/pinpoint.py`         |
| `RECRUITEE_COMPANIES`    | 8       | `src/sources/recruitee.py`        |
| `WORKDAY_COMPANIES`      | 15      | `src/sources/workday.py`          |
| `PERSONIO_COMPANIES`     | 10      | `src/sources/personio.py`         |
| `SUCCESSFACTORS_COMPANIES`| 3      | `src/sources/successfactors.py`   |
| `COMPANY_NAME_OVERRIDES` | —       | All 9 slug-based ATS sources      |

**Total:** 104 company slugs.

### 2.5 All `settings.py` Env Vars — Confirmed LIVE

All 13 env-var constants are read by at least one source/notification/filter module: `REED_API_KEY`, `ADZUNA_APP_ID`, `ADZUNA_APP_KEY`, `JSEARCH_API_KEY`, `JOOBLE_API_KEY`, `SERPAPI_KEY`, `CAREERJET_AFFID`, `FINDWORK_API_KEY`, `GITHUB_TOKEN`, `GEMINI_API_KEY`, `GROQ_API_KEY`, `SMTP_*`, `NOTIFY_EMAIL`, `SLACK_WEBHOOK_URL`, `DISCORD_WEBHOOK_URL`, `TARGET_SALARY_MIN/MAX`.

---

## 3. Orphan Files / Modules — NONE FOUND

Good news: **every Python file in `src/` is reachable** from an entry point or registry.

- All 48 source files properly registered in `SOURCE_REGISTRY` (`src/main.py:78-128`)
- All API route modules properly included as routers in `src/api/main.py`
- All CLI commands properly decorated with `@click.command`
- All notification channels discovered via `get_configured_channels()` in `src/notifications/base.py`
- **No orphan test files** — no tests exist for modules that were deleted
- **No stub files** — all `__init__.py` files are intentionally empty
- **No unused env vars** — every `.env.example` entry is read by `src/config/settings.py`

### 3.1 Dynamic-Dispatch False Positives (NOT orphans — keep)

The following were flagged by vulture but are actually loaded via decorators/registries — do NOT treat as dead:

**Pydantic model fields** (used by FastAPI serialization at runtime):
- `src/api/models.py:10` — `HealthResponse.version`
- `src/api/models.py:27` — `StatusResponse.sources_total`
- `src/api/models.py:32-55` — `JobResponse.id`, `job_type`, `role`, `seniority`, `experience`, `credentials`, `location_score`, `recency`, `semantic`, `matched_skills`, `missing_required`, `transferable_skills`
- `src/api/models.py:72` — `LinkedInResponse.ok` / `GitHubResponse.ok` (lines 110, 115)
- `src/api/models.py:84-87` — `ProfileSummary.skills_count`, `cv_length`, `has_linkedin`, `has_github`
- `src/api/models.py:99` — `CVDetail.summary_text`
- `src/api/models.py:127` — `PipelineProgress.progress`
- `src/api/models.py:135` — `PipelineItem.updated_at`
- `src/api/models.py:142` — `applications`
- `src/api/models.py:150` — `reminders`

**Click CLI commands** (discovered via `@cli.command()` decorator):
- `src/cli.py:78` — `view()`
- `src/cli.py:91` — `api()`
- `src/cli.py:101` — `list_sources()` (registered as `"sources"`)
- `src/cli.py:109` — `setup_profile()` (registered as `"setup-profile"`)

**FastAPI route handlers** (registered via `@router.get/post` decorators):
- `src/api/routes/actions.py:13` — `set_action`
- `src/api/routes/actions.py:37` — `list_actions`
- `src/api/routes/actions.py:47` — `action_counts`
- `src/api/routes/health.py:15` — `health_check`
- `src/api/routes/jobs.py:67` — `export_jobs`
- `src/api/routes/jobs.py:107` — `list_jobs`
- `src/api/routes/jobs.py:178` — `get_job`
- `src/api/routes/pipeline.py:34` — `list_pipeline`
- `src/api/routes/pipeline.py:46` — `pipeline_counts`
- `src/api/routes/pipeline.py:55` — `pipeline_reminders`
- `src/api/routes/profile.py:50` — `get_profile`
- `src/api/routes/profile.py:59` — `upsert_profile`
- `src/api/routes/profile.py:116` — `upload_linkedin`
- `src/api/routes/profile.py:134` — `upload_github`
- `src/api/routes/search.py:18` — `start_search`
- `src/api/routes/search.py:36` — `search_status`

**`row_factory` attribute assignments** — load-bearing (rows consumed as dict-like downstream):
- `src/cli.py:62`
- `src/cli_view.py:32`
- `src/dashboard.py:230`
- `src/storage/database.py:20`

**Standard `__exit__` / `__aexit__` parameters** — conventional, cannot be removed:
- `src/utils/rate_limiter.py:24` — `exc_type`, `exc_val`, `exc_tb`

---

## 4. Stale Documentation (Dead Information)

The docs are the biggest source of "dead" information. Grouped by file.

### 4.1 `CLAUDE.md` — 13 stale claims

| Line | Stale claim                                                         | Reality                                                     |
| ---- | ------------------------------------------------------------------- | ----------------------------------------------------------- |
| 7    | "Without a profile, it defaults to the original AI/ML keywords"     | `from_defaults()` returns empty lists after commit `8c4ed82` |
| 65   | "Run all 397 tests"                                                 | Actual: **407**                                             |
| 91   | `KNOWN_SKILLS (391) + KNOWN_TITLE_PATTERNS (107)`                   | Both removed in `3ba1342`                                   |
| 95   | `cv_parser.py — PDF/DOCX … section detection, skill/title extraction` | LLM-only now (`804725c`)                                   |
| 101  | "47 source files"                                                   | Actual: **48** source files                                 |
| 120  | "409 tests across 20 files"                                         | Actual: **407 across 21**                                   |
| 129  | "12 packages" in requirements.txt                                   | Actual: **18**                                              |
| 147  | `KNOWN_SKILLS (391-entry set)`                                      | Removed                                                     |
| 150  | `SearchConfig.from_defaults() returns the hard-coded AI/ML keywords` | Returns empty lists                                        |
| 151  | `cv_parser.py … extraction using KNOWN_SKILLS and KNOWN_TITLE_PATTERNS` | LLM-only                                                 |
| 191  | "397 tests across 19 test files"                                    | Actual: **407 across 21**                                   |
| 210  | "12 packages"                                                       | Actual: **18**                                              |
| 246  | "all 47 source files"                                               | Actual: **48**                                              |

**Missing documentation:**
- Folder-structure block (lines 77-134) does **not** mention `src/llm/`, `src/pipeline/`, `src/validation/`, or `src/profile/llm_provider.py` — these exist but are undocumented.
- Env-var table (lines 216-229) is missing `GEMINI_API_KEY` and `GROQ_API_KEY` rows.

### 4.2 `README.md` — 9 stale claims

| Line     | Stale claim                     | Reality                                                  |
| -------- | ------------------------------- | -------------------------------------------------------- |
| 164      | "387 tests"                     | 407                                                      |
| 345      | "391 known skills for CV parsing" | Removed                                                |
| 383      | "47 source files"               | 48                                                       |
| 400      | "376 tests across 17 files"     | 407 across 21                                            |
| 404      | "12 packages"                   | 18                                                       |
| 414      | "Run all 387 tests"             | 407                                                      |
| 424      | "All 387 tests pass"            | 407                                                      |
| 173-186  | Test table                      | Missing `test_api.py` and `test_llm_provider.py` rows   |

### 4.3 `STATUS.md` — 11 stale claims

| Line     | Stale claim                                      | Reality                                             |
| -------- | ------------------------------------------------ | --------------------------------------------------- |
| 5        | "Last updated: 2026-03-17"                       | ~3 weeks stale (today is 2026-04-10)                |
| 6        | "387 tests across 18 test modules"               | 407 across 21                                       |
| 7        | "18 test modules"                                | 21                                                  |
| 21       | `cv_parser.py … KNOWN_SKILLS matching`           | LLM-based, no `KNOWN_SKILLS`                        |
| 27       | "47 source file refactor"                        | 48                                                  |
| 88       | "all 46 source files"                            | 48                                                  |
| 117      | "387 tests pass"                                 | 407                                                 |
| 132      | "CV parser \| Medium \| Regex-based section detection" | LLM-based                                      |
| 142      | "CV parser section detection \| Low \| Regex-based" | LLM-based                                        |
| 152-172  | Test coverage table                              | Uses 387 total, missing `test_api.py` / `test_llm_provider.py` |
| 172      | "Total \| \| 387"                                | 407                                                 |

### 4.4 `ARCHITECTURE.md` — 8+ stale claims

| Line     | Stale claim                                               | Reality                                                      |
| -------- | --------------------------------------------------------- | ------------------------------------------------------------ |
| 38       | `KNOWN_SKILLS (391), KNOWN_TITLE_PATTERNS (107)`          | Removed                                                      |
| 50       | "47 source files"                                         | 48                                                           |
| 69       | "18 test files, 387 tests"                                | 21 files, 407 tests                                          |
| 78       | "12 packages"                                             | 18                                                           |
| 444      | "Uses KNOWN_SKILLS (391 entries) for matching"            | Removed                                                      |
| 446      | "Uses KNOWN_TITLE_PATTERNS for matching"                  | Removed                                                      |
| 437-447  | Entire "CV Parser Pipeline" section                       | Describes regex helpers (`_find_sections`, `_extract_skills_from_text`, `_extract_titles_from_experience`, `_extract_tech_names`) that no longer exist |
| 530-543  | Env-var table                                             | Missing `GEMINI_API_KEY`, `GROQ_API_KEY`                    |
| 592-607  | Dependencies table                                        | Lists 12 packages — missing fastapi, uvicorn, python-multipart, httpx, google-generativeai, groq (6 new; should be 18) |

### 4.5 `.env.example` — Clean

Every variable is read by `src/config/settings.py`. The docs are out of sync, not the file itself.

---

## 5. Ground-Truth Counts (verified 2026-04-10)

| Claim                          | Actual            |
| ------------------------------ | ----------------- |
| `SOURCE_REGISTRY` entries      | **48**            |
| `RATE_LIMITS` entries          | **48**            |
| Source `.py` files in `src/sources/` | **48** (excl. `__init__.py`) |
| Total ATS company slugs        | **104**           |
| Tests collected                | **407**           |
| Test files (`test_*.py`)       | **21**            |
| `requirements.txt` packages    | **18**            |
| `JOB_TITLES`                   | 25                |
| `PRIMARY_SKILLS`               | 15                |
| `SECONDARY_SKILLS`             | 17                |
| `TERTIARY_SKILLS`              | 11                |
| `LOCATIONS`                    | 26                |
| `NEGATIVE_TITLE_KEYWORDS`      | ~60               |
| CLI commands                   | `run`, `dashboard`, `status`, `view`, `api`, `sources`, `setup-profile` (7) |
| API route modules              | 6 (health, jobs, actions, profile, search, pipeline) |

---

## 6. Recommended Action Priority

Cheapest to most expensive. **All recommendations — no action taken yet.**

### Priority 1 — Trivial (1 command)

```bash
ruff check --fix --select F401,F841 src/ tests/
```

Eliminates 56 unused imports + 2 unused local variables in one shot.

### Priority 2 — Small mechanical deletes (~5 min)

Delete:
- `JSONFormatter` class and `get_logger` function from `src/utils/logger.py`
- `MAX_DAYS_OLD` from `src/config/settings.py:45`
- Four unused fixtures from `tests/conftest.py` (`sample_unrelated_job`, `sample_duplicate_jobs`, `sample_non_uk_job`, `sample_empty_description_job`)
- Two unused constants in `tests/test_sources.py` (`HN_JOBS_ITEM_2`, `AIJOBS_GLOBAL_HTML`)
- Unused `salary` local in `src/sources/careerjet.py:65`
- Unused `stats` local in `tests/test_main.py:239`

### Priority 3 — Medium doc sync (~30 min)

Sync `CLAUDE.md`, `README.md`, `STATUS.md`, `ARCHITECTURE.md` to reality:
- Replace all `387 / 397 / 409 / 376` test counts with **407**
- Replace all `17 / 18 / 19 / 20` test-file counts with **21**
- Replace all `47 / 46` source-file counts with **48**
- Replace all `12 packages` with **18 packages**
- Remove `KNOWN_SKILLS` and `KNOWN_TITLE_PATTERNS` references
- Update CV parser description to "LLM-based, multi-provider"
- Add `GEMINI_API_KEY` / `GROQ_API_KEY` to env-var tables
- Document `src/llm/`, `src/pipeline/`, `src/validation/` packages in CLAUDE.md folder tree
- Update STATUS.md "Last updated" timestamp

The `sync` skill or `claude-md-management:revise-claude-md` would be the right tool for this pass.

### Priority 4 — Design decision (not mechanical)

Decide fate of the legacy `score_job()` path:
- **Option A (full LLM commit):** Delete `score_job()`, `_title_score`, `_skill_score`, `_negative_penalty` in `src/filters/skill_matcher.py`, `_DEFAULT_*` fallbacks in `src/sources/base.py`, the skill iteration in `src/utils/time_buckets.py`, and the 6 dead-in-spirit constants. Update `test_scorer.py` accordingly.
- **Option B (restore defaults):** Repopulate `SearchConfig.from_defaults()` with generic keywords so the "no profile" fallback works meaningfully again.

The current state is neither — it's vestigial code that technically runs but produces empty matches.

---

## Appendix A — What I Searched

- **Tools:**
  - `vulture 2.16` at `--min-confidence 80` and `--min-confidence 60`
  - `ruff check --select F401,F811,F841,F501 src/ tests/`
  - `grep` / `Glob` for cross-reference verification
- **Excluded from scope:**
  - `.claude/worktrees/` (git worktree copies — would produce duplicate noise)
  - `__pycache__/`
  - `frontend/` (Next.js, not Python)
- **Included in scope:**
  - `src/` (48 source files + supporting modules)
  - `tests/` (21 test files)
  - Top-level docs (`CLAUDE.md`, `README.md`, `STATUS.md`, `ARCHITECTURE.md`)
  - `.env.example`
  - `requirements.txt` / `requirements-dev.txt`

## Appendix B — Commit Signals That Guided the Analysis

Recent commits that explain the state of the code:

```
3ba1342 chore: remove KNOWN_SKILLS (300+) and KNOWN_TITLE_PATTERNS (68) — replaced by LLM
7bcd8e9 feat(profile): wire LLM parser into API, dashboard, and CLI with error handling
8c4ed82 refactor(models): remove hardcoded AI/ML defaults from SearchConfig
40aa8a1 test(profile): replace regex parser tests with LLM mock tests
804725c feat(parser): replace all regex extraction with LLM-only CV analysis
0eac7f9 feat(llm): add multi-provider LLM pool — Gemini and Groq free tiers
```

The pattern: a 5-commit sequence migrated CV parsing from regex + `KNOWN_SKILLS` → LLM. But the scoring path (`score_job()`, tiered skill constants) was not migrated in the same sweep, leaving the codebase with one foot in each world.
