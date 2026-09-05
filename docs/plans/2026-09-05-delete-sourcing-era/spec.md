<!-- doc: PLAN | status: ACTIVE | pr: — -->
# Spec: delete the sourcing era (#483)
Companion to `intent.md`. Measured on `origin/main` `1fba085`. Every path is relative to
the repo root unless it starts with `backend/` or `frontend/`.

Words: **DELETE** = `git rm`. **TRIM** = edit a file that mixes keep + legacy. **MOVE** =
`git mv` into `docs/_archive/sourcing-era/` with the FROZEN header. **KEEP** = do not touch.

## R — Requirements

| # | Requirement | Pinned by |
|---|---|---|
| R1 | No module under `backend/src` imports `src.sources`, `src.main`, `src.workers`, or any module in D1–D3. Those modules do not exist. | `test_sourcing_era_deleted.py::test_modules_gone`, `::test_no_forbidden_tokens` |
| R2 | The FastAPI app exposes no route under `/api/search`, `/api/runs`, `/api/sources`, and none under `/api/jobs` except `POST /api/jobs/bring`. | `::test_routes_gone` |
| R3 | `POST /jobs/bring` inserts the `jobs` row, births the Application, and returns `{job, existing, application_id, status}`. `job` carries no `match_score`, no score dim, no `enrichment_applied`; the response has no `scored`. No `user_feed` write, no `fill_shelves`, no `SCORER_VERSION`. | `::test_bring_stores_without_scoring` |
| R4 | A profile save (`POST /profile/cv`, `/linkedin`, `/github`, `/preferences`, `PATCH /profile`) triggers no re-score: `src/services/rescore.py` and `src/workers/` do not exist; `profile.py` has no `_maybe_trigger_rescore`. `GET /profile` has no `search_titles`. | `::test_profile_has_no_search_titles`, `::test_modules_gone` |
| R5 | `src/core/settings.py` has none of the names in T1. `.env.example` has none of the rows in T1. | `::test_settings_gone`, `::test_env_example_gone` |
| R6 | Migration `0039_drop_sourcing_tables` drops `run_log`, `job_enrichment`, `job_embeddings` (and any index/trigger on them). `down` recreates them empty with the columns of their creating migration. up → down → up is clean on a fresh DB. | `::test_migration_0039_up_down_up` |
| R7 | `backend/pyproject.toml` declares neither `arq` nor `python-jobspy`; the `indeed` extra is gone. | `::test_deps_gone` |
| R8 | The MCP tool set is unchanged: `EXPECTED_TOOLS` in `tests/test_mcp_server.py` is the same list before and after; the parity table has no row for a deleted route. | existing `test_mcp_server.py`, `test_mcp_gate_parity.py` |
| R9 | Frontend: `frontend/src/app/{dashboard,jobs,admin/sources}` and `frontend/src/components/jobs/` do not exist; `middleware.ts` and `Navbar.tsx` read no `NEXT_PUBLIC_SEARCH_UI_ENABLED`; `/applications/[id]` renders `TailorSection`; the bring page routes to `/applications/{application_id}`; `sitemap.ts` lists static pages only. | `::test_no_forbidden_tokens` (frontend paths), Playwright `applications-detail-tailor.spec.ts`, vitest on `ApplicationClient` |
| R10 | Workflows in W1 do not exist; `scripts/drill_registry.py check` passes; `checker-scorecard.yml` has no shelf x-ray step; `external-health.yml` / `scripts/provider_probe.py` probe no job board. | `::test_no_forbidden_tokens` (`.github`, `scripts`), `python scripts/drill_registry.py check` in CI |
| R11 | `scripts/gen_doc_blocks.py --check` and `scripts/doc_sync_check.py` pass with `SOURCE_REGISTRY` gone; neither parses `backend/src/main.py`. | doc-sync.yml (CI), `::test_no_forbidden_tokens` (`scripts/`) |
| R12 | `docs/_archive/sourcing-era/` holds pillars 02/03, `CATALOG_STATE.md`, `SHELF_FILL_MEASURED.md`, `UNIVERSAL_SHELF.md`, `skills/add-source/`, each with the FROZEN header pointing at `VISION.md`. `docs/product/pillars/README.md` says what moved. | `::test_archive_populated` |
| R13 | Live docs carry no `SOURCE_REGISTRY`, `run_search`, `JobScorer`, `fill_shelves`, `SEARCH_UI_ENABLED`: `ARCHITECTURE.md`, `STATUS.md`, `docs/README.md`, `backend/README.md`, `frontend/README.md`, `.claude/skills/hard-rules/SKILL.md`, `docs/product/pillars/*.md` (those that remain), `CLAUDE.md`, `backend/CLAUDE.md`. Excluded on purpose: `docs/plans/**`, `docs/_archive/**`, `docs/harness/IMPLEMENTATION_LOG.md`, `docs/product/VISION.md` (history). | `::test_live_docs_clean` |
| R14 | `python -m pytest --collect-only -q` collects fewer tests than 3,739 and the canonical run is green; ruff 0; mypy ratchet 0. | CI |

## D — DELETE (whole files)

**D1 backend/src** (`git rm`):
`src/sources/` (all), `src/main.py`, `src/cli_view.py`, `src/workers/`,
`src/api/routes/search.py`, `src/api/routes/jobs.py`, `src/api/routes/runs.py`,
`src/repositories/csv_export.py`,
`src/services/{skill_matcher,deduplicator,rescore,scheduler,feed,job_enrichment,job_enrichment_schema,shelf_enrichment,shelf_gate,prefilter,retrieval,embeddings,vector_index,pg_vector_index,llm_matcher,scoring_dimensions,uk_gate,domain_classifier,coverage,ghost_detection,description_backfill,circuit_breaker,metrics_exporter,audit_trail,conditional_cache,job_signals,salary,visa_signal,skill_gap,query_text}.py`,
`src/services/profile/keyword_generator.py`.
Before deleting `job_signals.py` / `scoring_dimensions.py`: **MOVE** `detect_seniority`,
`strip_seniority` and `_USER_EXPERIENCE_RANK` (with their unit tests) into
`src/services/profile/seniority.py`. Before deleting `feed.py`: confirm
`decision_card.py` reads `user_feed` through `database.py`, not `FeedService` — if it uses
`FeedService`, keep the *read* path only, in `database.py`.
Any module in this list that a KEEP module still imports after the trims is a build
failure to fix by moving the needed function, never by keeping the module.

**D2 backend/scripts** (sourcing-only; the inventory in `plan.md` §C names each):
catalog/embeddings/gazetteer, shelf/enrichment probes, source health, ranking/scoring eval,
judge/panel/fake-profile harness, `uk_sweep.py`, `repair_devitjobs_visa.py`,
`reextract_stale_profiles.py`, `cleanup_preference_pollution.py`,
`reset_polluted_additional_skills.py`, the two `top20_*.txt` snapshots.
KEEP: `bootstrap_dev.py`, `backup_db.py`, `dump_db.py`, `check_env_example.py`,
`check_logs.py`, `log_rotation_check.py`, `mypy_ratchet.py`, `align_railway_env.py`,
`verify_*.py`, `observe.py` (TRIM: drop the FEED and isolation sections). `check_worker.py`
goes with the worker.

**D3 backend/tests** — every file whose subject is a D1/D2 module. The inventory names ≈60;
the worker measures with `grep -l` over the D1 module names and deletes those whose
*every* test targets a deleted module. Files that mix (`test_cli.py`, `test_api.py`,
`test_api_security.py`, `test_api_idor.py`, `test_profile.py`,
`test_bring_review_findings.py`, `conftest.py`) are TRIMMED: the legacy tests and fixtures go,
the rest stays untouched. `test_seniority_inference.py` follows the moved functions.
`test_search_flag.py`, `test_worker_settings.py` go with their flags.

**D4 frontend** (`git rm`): `src/app/dashboard/`, `src/app/jobs/`, `src/app/admin/sources/`,
`src/components/jobs/`, `src/lib/scoring.ts`, `src/lib/catalog.ts`,
`tests/e2e/{dashboard-sort,feed-visibility,job-render,two-account-isolation}.spec.ts`,
`src/app/__tests__/landing-sources-count.test.tsx` only if it references deleted symbols
(it guards the landing copy — prefer KEEP).

**D5 harness**: `.github/workflows/{accuracy-audit,journey,live-e2e,product-health,user-journey,data-invariants,absence}.yml` and their scripts
`scripts/{journey_probe,product_assertions,user_journey_audit,data_invariants,absence_check}.py`,
`backend/scripts/{eval_ranking,shelf_xray}.py`; their tests under `scripts/tests` or
`backend/tests` if any. `live-e2e.yml`: delete only if its every job is search-flow —
if it also drives login/profile/applications, TRIM to those.

## T — TRIM (edit)

**T1 `backend/src/core/settings.py`** — remove: `REED_API_KEY`, `ADZUNA_APP_ID`,
`ADZUNA_APP_KEY`, `JSEARCH_API_KEY`, `JOOBLE_API_KEY`, `SERPAPI_KEY`, `CAREERJET_AFFID`,
`FINDWORK_API_KEY`, `DFE_APPRENTICESHIPS_API_KEY`, `MIN_MATCH_SCORE`, `FEED_CANDIDATE_CAP`,
`MAX_RESULTS_PER_SOURCE`, `MAX_DAYS_OLD`, `ENRICHMENT_*`, `MAX_CONCURRENT_SEARCHES_PER_USER`,
`MIN_TITLE_GATE`, `MIN_SKILL_GATE`, `SALARY_WEIGHT`, `SENIORITY_WEIGHT`, `VISA_WEIGHT`,
`WORKPLACE_WEIGHT`, `SEMANTIC_ENABLED`, `EMBED_BACKFILL_PER_RUN`,
`DESCRIPTION_BACKFILL_PER_TICK`, `SHELF_ENRICHMENT_*`, `LLM_OUTPUT_TOKENS_PER_JOB`,
`TARGET_SALARY_MIN/MAX`, `SOURCE_FETCH_TIMEOUT*`, `MAX_RETRIES`, `RETRY_BACKOFF`,
`SEARCH_UI_ENABLED`, `CATALOG_CRONS_ENABLED`, `ENGINE*_ENABLED`, `MATCHER_*`.
KEEP anything a remaining importer reads (`grep -rn` each name before removing; `REQUEST_TIMEOUT`
and `USER_AGENT` stay if `url_fetch`/tailoring read them). Same rows leave root `.env.example`
and `docker-compose.prod.yml` (`worker` service + job-board keys) — the compose edit is
listed in the PR body as an infra change for the owner.

**T2 `backend/src/api/routes/bring.py`** — drop imports from `jobs.py`, `feed`, `shelf_gate`,
`skill_matcher`; drop `_personalize_dims`, `FeedService.upsert_feed_row`, `scored`;
build the response with a local `_job_row_to_response(row)` (or a `JobResponse.from_row`
classmethod in `models.py`). `db.get_job_by_id_with_enrichment` → a plain `get_job_by_id`.
Keep `update_last_seen` only if `jobs.staleness_state` still gates any remaining read —
otherwise drop both.

**T3 `backend/src/api/models.py`** — `JobResponse` keeps `id, title, company, location,
salary, source, date_found, apply_url, visa_flag, visa_status, job_type, experience_level,
description, posted_at, date_confidence` (measure what `bring`/spine still fill); drop every
score dim, `match_score`, `enrichment_applied`, dedup fields, `JobListResponse`, action
models, run/source models. `SourceInfo`/`HealthResponse.sources_total` go (T5).

**T4 `backend/src/api/routes/profile.py`** — delete `_rescore_bg_tasks`,
`_rescore_finished`, `_run_rescore_in_process`, `_maybe_trigger_rescore` and every call;
delete the `search_titles` block and the field on `ProfileResponse`; keep everything else
byte-for-byte (slice 4 lands here too — minimal diff).

**T5 `backend/src/api/routes/health.py`** — drop the `src.main` import, `sources_total`,
`source_list`, the `/sources` endpoint; also any Redis/ARQ liveness field that reports a
worker that no longer exists.

**T6 `backend/src/api/main.py`** — remove `include_router` for `jobs`, `search`, `runs`;
remove ARQ/Redis lifespan hooks. `pipeline` stays.

**T7 `backend/src/repositories/database.py`** — remove the top-level imports of D1 modules and
every method that only they called (`run_log`, source health, feed *writes*, enrichment,
embeddings, dedup groups, `purge_old_jobs` and `_PURGE_CASCADE_TABLES`, scheduler state,
ghost sweep). Keep `insert_job`, `get_job_by_id`, `get_job_id_by_key`, the `jobs` DDL, every
spine/profile/receipt/tailor/notification method, and the `user_feed` **read** that
`decision_card.py` uses. `src/models.py` `Job`: keep the dataclass and `normalized_key()`
(hard rule 1); drop score/dim fields no remaining code sets.

**T8 `backend/src/cli.py`** — keep `api` and `setup-profile`; delete `run`, `status`, `view`,
`sources`, `rescore-backfill`. `backend/src/api/dependencies.py`, `src/utils/telemetry.py`,
`src/services/profile/{shelf_audit,skill_normalizer}.py`: remove references, keep behaviour.

**T9 `backend/pyproject.toml`** — drop `arq`, `python-jobspy`, the `indeed` extra; drop
`redis` only if `grep -rn "import redis\|from redis"` under `backend/src` finds nothing
after T5/T6 (`services/auth/rate_limit.py` is a known user — check it).

**T10 frontend** — `middleware.ts`: delete the flag block (`SEARCH_UI_*`); `Navbar.tsx`:
delete the flag and the Dashboard link; `bring/page.tsx`: copy says "We keep it, tailor
your CV, and keep a receipt…", `router.push(`/applications/${res.application_id}`)`;
`applications/[id]/ApplicationClient.tsx`: replace the `/jobs/{id}` anchor with
`<TailorSection jobId={detail.job_id} />` and an `apply_url` link; `sitemap.ts`: static
routes only; `lib/api.ts`: drop `listJobs`, `getJob`, `exportJobs`, action helpers and
their types; `.env.local.example`: drop the flag row; Playwright specs that only *skip* on
the flag: drop the skip. Regenerate `openapi.json` + `api-types.ts` after the backend lands.

**T11 harness** — `scripts/drill_registry.py`: delete the D5 rows; `checker-scorecard.yml`:
delete the shelf x-ray step; `external-health.yml` + `scripts/provider_probe.py`: drop
`REED`/`ADZUNA` probes; `scripts/gen_doc_blocks.py`: delete the source-count facts (keep
route/endpoint/test-file counts); `scripts/doc_sync_check.py`: delete `_extract_registry`
and every rule keyed on the source count, drop the archived docs from `LIVING_DOCS`, keep
`landing-source-count`.

**T12 docs** — `ARCHITECTURE.md`: rewrite §System Overview, delete §Pipeline Run,
§Scoring Algorithm, §Source Architecture, §SearchConfig Generation, the flag rows; regenerate
the code-facts block. `.claude/skills/hard-rules/SKILL.md`: delete the "Legacy — sourcing
era" section except rule 24 (notifications) and rule 1 (`normalized_key`, still used by
`bring`); renumber nothing. `docs/README.md`: pillar rows → archive rows + this plan's row.
`STATUS.md`: slice-5 line in the head. `docs/product/pillars/README.md`: what moved and
why. `backend/README.md`, `frontend/README.md`: drop search/dashboard sections.
`docs/plans/2026-09-03-mission-roadmap.md` row 5: `#483 — draft PR #NNN`.
`CLAUDE.md` lines 44, 72, 75 and the "search pipeline below is legacy" sentence: **last,
separate commit** (owner-approval-only file).

## S — Security and guardrails
| # | Guardrail |
|---|---|
| S1 | Deleting code must not widen access. Every remaining route keeps its `require_user` /
bearer gate; the parity test still covers every MCP tool. A route that was public
(`GET /api/jobs/{id}`, `/api/jobs`) is removed, not re-scoped — a brought ad is one user's data. |
| S2 | Migration `0039` touches only the three named tables. No `DROP` on `jobs`, `user_feed`,
`user_actions` or any spine/profile/auth table. Reviewer greps the up file for `DROP` and
counts three. |
| S3 | No secret leaves the repo by accident: removed env names are removed from `.env.example`
and `docker-compose.prod.yml`; Railway variables are the owner's (listed by NAME in the PR
body for him to delete). |
| S4 | The bring path no longer calls any code that fetches (no shelf gate, no enrichment LLM,
no embeddings). Request bounds on `BringJobRequest` stay as they are. |
| S5 | Removing SSRF-capable scrapers and 8 job-board API keys shrinks the attack surface; the
URL-fetch SSRF guard (#496) is untouched by this slice. |
| S6 | `sitemap.ts` must not enumerate any per-user data after the change (static pages only). |
| S7 | Guards are edited, never bypassed (intent constraint 5). The PR diff of
`doc_sync_check.py`, `drill_registry.py`, `gen_doc_blocks.py` is reviewed line by line. |
| S8 | Prod data risk is stated in the PR body in one paragraph: what is dropped, that the down
migration recreates empty tables, that the daily backup exists. |

## Out of scope (own issues, opened from the PR)
- Push notifications (`services/notifications`, `services/delivery`, channels routes and
  pages, `user_feed`, `notification_rules`, digests) — decision 11.
- Folding `applications.stage` + pipeline UI into `status` — slice 2 open question.
- Purging the 19k scraped `jobs` rows.
- Deleting `user_actions` (job saved/dismissed) once nothing writes it.
