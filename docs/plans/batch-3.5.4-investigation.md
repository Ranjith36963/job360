# Batch 3.5.4 — Test Cleanup Investigation

**Run:** 2026-04-19
**Baseline run:** `/tmp/pytest_baseline_3_5_4.log` — 24 failed / 578 passed / 3 skipped
**Random-order run** (`--randomly-seed=12345`): `/tmp/pytest_random_3_5_4.log` — **same 24 failures**, zero additional exposed flakes

## Buckets

All 24 failures are **test-only fixes** — no production code touched, no test-order flake surfaced in random order, no external-environment dependency. Three Bucket-A subcategories emerged:

| Bucket | Count | Subcategory | Root cause | Fix pattern |
|---|---:|---|---|---|
| **A1** | 6 | authenticated_client | Route requires auth (Batch 3.5.1 / 3.5.2) but test sends no session cookie → 401 | Add `authenticated_async_client` fixture in `conftest.py` |
| **A2** | 8 | PROJECT_ROOT path stale | Phase-1 refactor moved `tests/` into `backend/`. Tests compute `PROJECT_ROOT = parent.parent` (= `backend/`) but the scripts live at the repo root (`cron_setup.sh`, `setup.sh`, `requirements.txt`, `requirements-dev.txt`) | Update `PROJECT_ROOT = parent.parent.parent` |
| **A3** | 10 | Default keywords/skills emptied | `core/keywords.py` lists (PRIMARY/SECONDARY/TERTIARY skills, JOB_TITLES) were emptied in commit `a01c1b3` when storage moved to per-user SearchConfig. Tests that call sources / `extract_matched_skills` without injecting a SearchConfig get empty lists → 0 matches | Inject a minimal `SearchConfig` (or pass skill lists explicitly) in the test |

### Bucket B — test-order flakes: 0

The random-order run (seed 12345) produced the **identical failure set** to the in-order baseline. No hidden predecessors to bisect. The earlier flake reported in Batch 3.5.3 (`test_auth_sessions.py::test_cookie_tampering_rejected`) did not reproduce in either run — confirmed one-off.

### Bucket C — production bugs: 0

Each A3 test would pass if the caller passed the right SearchConfig — production call sites do this (via `generate_search_config(profile)` in `main.py`/`tasks.py`). The tests are exercising defaults that were deliberately emptied when per-user profiles landed. Not a production bug; a test-data gap.

### Bucket D — environmental/external: 0

No test requires real network, real Redis, real DB access outside the runner's fixture hooks. The `test_cron.py` / `test_setup.py` failures aren't environmental — the target files DO exist; the tests just look at the wrong path.

## Per-test classification

### A1 — authenticated_client needed (6)

| Test | Current status | Root cause |
|---|---|---|
| `test_api.py::test_jobs_list_empty` | `assert 401 == 200` | `GET /api/jobs` gated by `require_user` (Batch 3.5) |
| `test_api.py::test_actions_counts_empty` | `assert 401 == 200` | `GET /api/actions/counts` gated |
| `test_api.py::test_pipeline_counts_empty` | `assert 401 == 200` | `GET /api/pipeline/counts` gated |
| `test_api.py::test_pipeline_list_empty` | `assert 401 == 200` | `GET /api/pipeline` gated |
| `test_api.py::test_full_api_workflow` | `assert 401 == 200` | Every per-user endpoint along the path |
| `test_api.py::test_profile_404_when_none` | `assert 401 == 404` | `GET /api/profile` gated (Batch 3.5.1); auth fires before the `load_profile` mock returns None |

### A2 — PROJECT_ROOT path stale (8)

| Test | Path looked for | Actual location |
|---|---|---|
| `test_cron.py::test_cron_contains_uk_timezone` | `backend/cron_setup.sh` | `cron_setup.sh` (repo root) |
| `test_cron.py::test_cron_schedule_4am_4pm` | same | same |
| `test_cron.py::test_cron_uses_module_invocation` | same | same |
| `test_setup.py::test_setup_checks_python_version` | `backend/setup.sh` | `setup.sh` (repo root) |
| `test_setup.py::test_setup_creates_data_dirs` | same | same |
| `test_setup.py::test_setup_validates_env_example` | same | same |
| `test_setup.py::test_requirements_prod_no_test_deps` | `backend/requirements.txt` | `requirements.txt` (repo root) |
| `test_setup.py::test_requirements_dev_includes_prod` | `backend/requirements-dev.txt` | `requirements-dev.txt` (repo root) |

### A3 — Default keywords/skills emptied (10)

All these tests instantiate a source (or `extract_matched_skills`) without providing a `SearchConfig`. The BaseJobSource properties (`self.job_titles`, `self.search_queries`) and `time_buckets.extract_matched_skills` fall back to `core/keywords.py` lists which are empty since commit `a01c1b3`. Source fetch loops never iterate; `extract_matched_skills` never matches.

| Test | Empty collection |
|---|---|
| `test_sources.py::test_reed_parses_response` | `self.job_titles[:12]` |
| `test_sources.py::test_adzuna_parses_response` | `self.job_titles` |
| `test_sources.py::test_jooble_parses_response` | `self.job_titles[:8]` |
| `test_sources.py::test_google_jobs_parses_response` | `self.job_titles[:8]` |
| `test_sources.py::test_workday_parses_response` | `self.job_titles[:8]` |
| `test_sources.py::test_careerjet_parses_response` | `self.job_titles[:6]` |
| `test_sources.py::test_jobspy_parses_dataframe` | `self.job_titles[:8]` (indeed/JobSpySource) |
| `test_time_buckets.py::test_extract_matched_skills_primary` | default `PRIMARY_SKILLS` |
| `test_time_buckets.py::test_extract_matched_skills_secondary` | default `SECONDARY_SKILLS` |
| `test_time_buckets.py::test_extract_matched_skills_tertiary` | default `TERTIARY_SKILLS` |

## Implication for fix design

- **A1** — one new fixture in `conftest.py` + 6 test bodies updated to use it.
- **A2** — one-line fix per file: `PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent`. No other changes.
- **A3** — for test_sources tests, construct a `SearchConfig(job_titles=[...], relevance_keywords=[...])` and pass `search_config=sc`. For test_time_buckets, pass explicit `primary=`, `secondary=`, `tertiary=` lists. No production changes.

Total expected diff: ~100 lines of test code edits + 1 new conftest fixture + 0 lines of production code.

_Investigation complete 2026-04-19._
