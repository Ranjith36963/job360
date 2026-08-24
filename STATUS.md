# Job360 Project Status
<!-- doc: LIVING | last-verified: 2026-08-21 by /sync -->

## Current State: Funnel batch LIVE (2026-08-05) — retrieve→enrich→rank→judge in prod

> **Freshest history:** `docs/harness/IMPLEMENTATION_LOG.md` § "2026-08-05 — The funnel batch"
> (PRs #224/#226/#227/#228/#229): bounded per-user candidate set (top-800), revived
> title ranking (Spearman −0.04→+0.29 vs LLM oracle), job text for ~65% of the
> catalog, user-feedback loop + learned preferences v1/v2, self-healing enrichment
> cron, embedding convergence. Open: prod `SEMANTIC_ENABLED` env flip (owner),
> linkedin scraper text.

> **⚠️ THIS FILE HAS ROTTED — read `CLAUDE.md` first.** Verified 2026-08-03: it pointed at
> `backend/src/services/profile/` and `backend/src/services/`, neither of which has existed since the
> clean-architecture restructure (they are `src/services/profile/` and
> `src/services/skill_matcher.py`); it named a merged head ~334 commits behind; it listed a
> sentinel file that does not exist; and it claimed an autonomous maintenance loop was
> running when that loop was disabled on 2026-06-21. An agent following it would edit paths
> that are not there. Treat every path and commit below as UNVERIFIED unless you have just
> checked it.

> **In flight (branch `feat/two-pass-profile-extraction`, 2026-06-17):** two-pass
> profile extraction. Every input (CV / LinkedIn / GitHub / preferences) now gets a
> deterministic pass **and** an LLM enhance pass, merged into one `CVData`. New
> `CVData` fields (`linkedin_raw_text`, `github_repos_brief`, `github_llm_skills`,
> `about_me_inferred_skills`) store raw inputs so both passes re-run from storage on
> any profile change (`two_pass.reextract_and_rescore`) → new profile version → feed
> re-score. No DB migration (JSON-blob storage). M2 / LLM judge untouched. See
> `docs/harness/IMPLEMENTATION_LOG.md` for the full entry.

> **✅ MERGED to main (channels & notifications overhaul, 2026-06-20):** fully
> shipped and in sync on `origin/main`. Notifications collapsed to ONE rule per user
> (migration 0020) with three timing modes (`instant`/`daily`/`every_n_hours`);
> `notification_tick` ARQ cron (every 5 min) + `send_bundle` drain the digest queue
> (fixes the P0 where digests queued but never sent); legacy global `.env`-webhook
> path removed (one path: worker/tick → dispatcher → Apprise → ledger); API is one
> `GET`/`PUT /settings/notification-rule`; frontend is one rulebook form. **Nav IA:**
> Channels is now a top-level page (`/channels`); the Settings gear holds only
> Notifications + Account. Off-state made unmistakable; notification-history filters
> fixed to server-side; Account/Channels error messages cleaned. Verified end-to-end
> in a real browser (Playwright manual-tester pass — every button/feature/state).
> Backend 1392 passed / 3 skipped; frontend type-check+lint clean, 107 unit tests.
> Full detail in `docs/harness/IMPLEMENTATION_LOG.md`. Only unverified corner: real external
> delivery to live Slack/Telegram/Gmail (needs provider credentials).

**Last updated:** 2026-08-24 (doc truth check; the phase narratives below are older — see `docs/harness/IMPLEMENTATION_LOG.md` for the current history)
**Total tests:** defer to the runtime collected count (3,297 collected / 3,295 selected, 2 live deselected; 0 failing, 3 skipped on Windows)
**Source files:** 40 source files in `backend/src/sources/` (excluding `__init__.py` and `base.py`) split into 6 category subfolders | **Test files:** 217 `test_*.py` modules
**Job sources:** 41 entries in `SOURCE_REGISTRY`; 40 live instances since `indeed` + `glassdoor` share `JobSpySource`; gov_apprenticeships restored 2026-06-16 on DfE Display Advert API v2 (M6 2026-06 dropped jobtensor, comeet, aijobs_global; the 2026-08-10 rotation dropped 6 more dead upstreams — aijobs, rippling, biospace, jobs_ac_uk, workanywhere, nhs_jobs_xml). See CLAUDE.md rule #13 for the five load-bearing surfaces that move together on a registry change.
**Latest merged head:** `225040e` on `origin/main` — docs audit + cleanup (2026-06-21); all worktree/feature branches merged and deleted.
**Sentinel:** `.claude/step-3-verified.txt` → `337fbda19b5ae30d55dba061bc6658a49bcd208d` (post-reviewer-fix SHA).

---

## Phase 1: Dynamic User Profile System -- COMPLETE

**Goal:** Replace hard-coded AI/ML keywords with user-provided profile data so Job360 works for any profession (sales, law, engineering, hospitality, etc.).

### What was built

| Component | File(s) | Status |
|-----------|---------|--------|
| Profile dataclasses | `backend/src/services/profile/models.py` | Done -- CVData, UserPreferences, UserProfile, SearchConfig |
| CV parser (PDF/DOCX) | `backend/src/services/profile/cv_parser.py` | Done -- pdfplumber + python-docx text extraction, LLM-only skill/title extraction via `llm_provider.py` (KNOWN_SKILLS regex removed in commit 804725c) |
| Preferences validator | `backend/src/services/profile/preferences.py` | Done -- form validation, CV+prefs merge |
| Profile storage | `backend/src/services/profile/storage.py` | Done -- JSON at `backend/data/user_profile.json` |
| Keyword generator | `backend/src/services/profile/keyword_generator.py` | Done -- UserProfile -> SearchConfig conversion |
| JobScorer class | `backend/src/services/skill_matcher.py` | Done -- dynamic scoring using SearchConfig |
| BaseJobSource properties | `backend/src/sources/base.py` | Done -- `self.relevance_keywords`, `self.job_titles`, `self.search_queries` |
| Source file refactor (every source) | `backend/src/sources/*.py` | Done -- **zero** direct `src.core.keywords` imports remain; the 12 sources that need keywords read them via the inherited `self.*` properties (the other 28 never reference them) |
| Orchestrator wiring | `backend/src/main.py` | Done -- loads profile, creates scorer, passes config |
| CLI setup-profile | `backend/src/cli.py` | Done -- interactive profile wizard |
| Profile tests | `backend/tests/test_profile.py` | Done -- 56 tests covering all profile modules |
| Dependencies | `backend/pyproject.toml` | Done -- added pdfplumber, python-docx |

### Backward compatibility

- `keywords.py` is NOT modified -- remains the default keyword source
- All existing function signatures preserved (`score_job()`, `check_visa_flag()`, etc.)
- When no `backend/data/user_profile.json` exists, behavior is **identical** to pre-Phase-1
- `len(SOURCE_REGISTRY) == N` test assertion still in `tests/test_cli.py` (current N = 41; 40 live instances — `indeed`+`glassdoor` alias `JobSpySource`)
- All original tests pass without modification

---

## Phase 2: LinkedIn + GitHub API -- COMPLETE (LinkedIn ingest later replaced with PDF)

**Goal:** Enrich user profiles with LinkedIn data and GitHub public repos.

**Superseded note:** the original phase-2 ingest was a LinkedIn Data Export ZIP of CSVs. This was later replaced with a LinkedIn "Save to PDF" parser (`parse_linkedin_pdf` in `backend/src/services/profile/linkedin_parser.py`) — same output schema, same `enrich_cv_from_linkedin()` merge logic. The rest of this section describes the historical ZIP flow.

### What was built

| Component | File(s) | Status |
|-----------|---------|--------|
| LinkedIn ZIP parser | `backend/src/services/profile/linkedin_parser.py` | Done -- parses positions.csv, skills.csv, education.csv from ZIP |
| LinkedIn CVData enrichment | `backend/src/services/profile/linkedin_parser.py:enrich_cv_from_linkedin()` | Done -- merges LinkedIn data into CVData |
| GitHub API enricher | `backend/src/services/profile/github_enricher.py` | Done -- fetches repos, languages, topics; infers skills |
| GitHub CVData enrichment | `backend/src/services/profile/github_enricher.py:enrich_cv_from_github()` | Done -- merges GitHub data into CVData |
| CVData model fields | `backend/src/services/profile/models.py` | Done -- linkedin_positions, linkedin_skills, linkedin_industry, github_languages, github_topics, github_skills_inferred |
| UserPreferences field | `backend/src/services/profile/models.py` | Done -- github_username field |
| CLI --linkedin option | `backend/src/cli.py:setup-profile` | Done -- accepts LinkedIn ZIP path |
| CLI --github option | `backend/src/cli.py:setup-profile` | Done -- accepts GitHub username |
| GITHUB_TOKEN env var | `backend/src/core/settings.py`, `.env.example` | Done -- optional, for higher API rate limits |
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
| DB error logging | `backend/src/cli_view.py` | Done -- `except Exception` blocks now log errors before returning empty |
| Magic number elimination | `backend/src/main.py`, `backend/tests/test_main.py` | Done -- `SOURCE_INSTANCE_COUNT` constant replaces hard-coded 47 |
| Schema migration | `backend/src/repositories/database.py` | Done -- `_migrate()` method uses PRAGMA table_info + ALTER TABLE for future columns |
| Source health tracking | `backend/src/main.py`, `backend/src/repositories/database.py` | Done -- detects sources returning 0 that previously had jobs, warns in logs |
| Rate limiter tests | `backend/tests/test_rate_limiter.py` | Done -- 5 tests: acquire/release, context manager, concurrency limit, delay, multi-concurrent |
| Source category metadata | `backend/src/sources/base.py`, every source file (40 classes / 41 registry entries today) | Done -- `category` class attribute (keyed_api/free_json/ats/rss/scrapers/other) |
| Integration tests | `backend/tests/test_main.py`, `backend/tests/test_database.py` | Done -- SOURCE_INSTANCE_COUNT validation, failed source tracking, migration, source history |

---

## Phase 3+ (Future)

- Skill inference from job titles (e.g., "Data Scientist" implies Python, SQL, statistics)
- AI-powered CV summarization for better keyword extraction
- Multi-profile support (different job searches simultaneously)
- Job recommendation engine based on profile match patterns
- Interview tracking and application pipeline

---

## Matching Engines

Four engines are available, stacked **keyword → dimensions → hybrid → LLM judge**. All default OFF except the keyword engine:

| Engine | Service | Flag | Default |
|--------|---------|------|---------|
| #1 Keyword | `services/skill_matcher.py` (`JobScorer`, 4-component 0–100) | always on | ON |
| #2 Dimensions | `services/scoring_dimensions.py` — +30 seniority/salary/visa/workplace (`skill_matcher.py:582-617`). **The scorer enters the dim path on `user_preferences` alone** (`skill_matcher.py:587`), no flag involved; the flag gates only whether the **enrichment** LLM step (`services/job_enrichment.py`) has produced data for those dims to read (`main.py:853`, `main.py:1137`). Without it they score their neutral halves | `ENGINE2_ENABLED` **or** `ENRICHMENT_ENABLED` (enrichment data only) | false |
| #3 Hybrid | `services/embeddings.py` + `vector_index.py` + `retrieval.py` (RRF fuse + cross-encoder rerank) | `SEMANTIC_ENABLED` | false |
| #4 LLM judge | `services/llm_matcher.py` (`MatchVerdict`) | `MATCHER_ENABLED` | false |

Engine #4 runs after per-user feed write (`_run_matcher_stage`). Results stored on `user_feed` (migration 0017). Feed reads rank by `COALESCE(llm_fit_score, score) DESC`. Measured: 18/18 judged in 89.8 s at concurrency 3; judge spread 20–92 vs keyword 30–43; 10/10 fit accuracy on labeled sample.

**Profile-version re-score (migration 0018, automatic, no new flags):** every `user_feed` row is now stamped with the profile version that produced its score. When a user saves a new profile (CV / LinkedIn / GitHub / preferences), the system detects whether the content actually changed, clears old LLM verdicts, and re-scores the full 30-day catalog in the background against the new profile. Ordinary searches only score newly-fetched jobs — existing rows keep their scores. A job's score changes only when the profile changes, never just because time passed. Service: `src/services/rescore.py`; trigger: `src/api/routes/profile.py`.

---

## What's Next (Step 4 — Ops hardening / Batch 4 — Launch readiness)

**Step 0..3 status:** all green and merged on origin/main.
- Step 0 (pre-flight hardening) closed 2026-04-24 at `e31cac7` with 1,018 tests + Makefile + bootstrap_dev + migration 0010 + check_env_example + pytest-xdist.
- Step 1 (engine→API seam) closed with multi-dim scoring + hybrid retrieval + per-dim score columns wired to `/api/jobs` (migration 0011).
- Step 1.5 (post-Step-1 stabilisation) shipped reviewer fixes + dataclass round-trips + lazy-import startup safety.
- Step 1.6 locked the generator/reviewer worktree contract (`.claude/generator-commit.md` + `.claude/reviewer-verdict.md` + `make verify-batch`).
- Step 2 (API→UI seam) closed at `5cf60ea` with all 5 cohorts (foundations + components + page surfaces + SEO + TanStack Query + run-surface + E2E smokes) + reviewer R-1..R-4 fixes + sentinel.
- **Step 3 (new endpoints + Settings UI) closed at origin/main `7194d0e` PR #9 merge** — 8 new backend endpoints, migrations 0012/0013/0014, dispatcher rule consultation with timezone-aware quiet hours, ARQ digest + ghost-sweep periodic tasks, 5 new frontend pages, KanbanBoard polish, Cohort D toasts/a11y/loading skeletons, reviewer R-1..R-7 closed, sentinel `337fbda`.
- **Matcher batch (funnel→judge) merged post-Step-3** on `fix/per-user-search-and-scoring-gate` — `services/llm_matcher.py`, migration 0017 (`user_feed` llm_* columns), `_run_matcher_stage` in pipeline, API llm_* fields, dashboard AI-verdict badge + COALESCE sort, MATCHER_ENABLED/THRESHOLD/MAX_JOBS flags (all default off). Measured: 18/18 in 89.8 s, judge spread 20–92, 10/10 fit accuracy.
- ~~**Autonomous maintenance loop** running~~ — **DORMANT since 2026-06-21.** The live automation is the GitHub Actions harness in `.github/workflows/`, not these agents. MISSIONS.md has not moved since 2026-06-17.

**Step 4 — ops hardening (next):** GitHub Actions CI matrix, Dockerfile + docker-compose, deploy platform config, secret manager integration, security headers middleware, `/livez` + `/readyz` split, worker timeouts, pip-audit + npm audit + gitleaks + bandit in CI, FastAPI request timeout middleware, LLM call timeouts, per-query DB deadlines, DB backup script + restore drill, `/admin/runs` UI consuming `GET /api/runs/recent` (Step-3 backend already shipped this).

**Step 5 / Batch 4 — launch readiness:** scope-down to top 10-15 sources, freemium metering, ICO £40 registration, privacy notice + LIA, ASA-compliant marketing copy, ~~Amazon SES wiring~~ (**DONE differently**: magic-link email ships via **Resend**'s HTTP API — `services/auth/email_sender.py`, live on the verified `job360.uk` domain since 2026-07-10. SES was never used), full password-reset (forgot-password) flow, friend dogfood, prod-Redis smoke.

**Step 3 carry-overs — ALL SHIPPED (closed):**
- V-01..V-03 form-validation library (RHF + zod) — DONE; forms now use RHF + zod validation.
- V-04 CV upload size cap + MIME allowlist — DONE; 413/415 responses wired, MIME allowlist enforced.
- V-05 OpenAPI → TS codegen — DONE; codegen pipeline in place.
- C-07 `@dnd-kit/core` + `@dnd-kit/sortable` keyboard a11y on KanbanBoard — DONE; `@dnd-kit/*` installed and keyboard a11y active.

---

## What Is Working Right Now

- Full 41-source pipeline (40 live instances) runs end-to-end (async fetch, score, dedup, store, notify) with `TieredScheduler` wired into `run_search` (Batch 3 / 3.5; M6 rotation removed jobtensor/comeet/aijobs_global; gov_apprenticeships restored 2026-06-16; 2026-08-10 rotation removed 6 dead upstreams)
- Profile system: CV + LinkedIn + GitHub enrichment → dynamic keywords → personalised search (LLM-only CV parser via multi-provider fallback, in order: **OpenAI `gpt-4o-mini` (PRIMARY)** / Gemini / Groq / Cerebras — `llm_provider.py:330-334`)
- Multi-user delivery layer (Batch 2): auth + per-tenant isolation + ARQ worker (`WorkerSettings` + `send_notification`) + Apprise dispatcher + `FeedService` SSOT
- Multi-user profile storage (Batch 3.5.2): migration `0006_user_profiles` + per-user `_search_config_for`
- Conditional-cache machinery (Batch 3.5.3) exists and is tested, but **no source uses it today** — the `nhs_jobs_xml` pilot went with that source in the 2026-08-10 rotation (only `nhs_jobs_xml` was retired — the separate `nhs_jobs` source is still registered, `main.py:142`). Only `backend/tests/test_conditional_fetch.py` calls the helpers; `backend/scripts/preflight_conditional_cache.py` remains for future candidates
- All 8 keyed APIs skip gracefully when keys are empty
- All ATS boards iterate over **297** company slugs actually polled — `core/companies.py` holds **302** across 11 platform lists, but `RIPPLING_COMPANIES` (5) has had no source class since the 2026-08-10 rotation, so 10 ATS sources poll the other 297 (comeet was removed in M6)
- All RSS/XML feeds parse correctly with mocked data
- All HTML scrapers extract job data with regex
- Pillar 2 multi-dim scoring activates as soon as `JobScorer(..., user_preferences=...)` is wired — `enrichment_lookup` is optional, and without it each dim scores its NEUTRAL half, not zero (8-dim: title/skill/location/recency + seniority/salary/visa/workplace); legacy 4-component path unchanged by default
- Pillar 2 opt-in features behind flags (OFF by default): `ENRICHMENT_ENABLED` (LLM enrichment pipeline), `SEMANTIC_ENABLED` (sentence-transformers + ChromaDB)
- Postgres database with auto-purge (30 days); shared `jobs` catalog + per-user `user_feed` / `user_actions` / `applications`
- Email, Slack, Discord, Telegram, webhook — all via the Apprise dispatcher, per-user channels (Batch 2). The old built-in channel classes are REMOVED.
- CLI commands: run, view, api, status, sources, setup-profile
- Next.js frontend (at `frontend/`) + FastAPI backend (at `backend/src/api/`) deliver the interactive UI
- Tests: defer to the runtime collected count (3,297 collected / 3,295 selected, 2 live deselected); 3 skip on Windows (bash-only `setup.sh` / `cron_setup.sh` tests), 0 failing

---

## What Is Fragile or Risky

| Source/Component | Risk | Notes |
|------------------|------|-------|
| **HTML scrapers** (5) | High | LinkedIn, Climatebase, 80000Hours, BCS Jobs, AIJobs AI all use regex parsing on HTML. Any layout change breaks them silently (returns 0 jobs, no error). |
| **python-jobspy** (Indeed/Glassdoor) | Medium | Not in backend/pyproject.toml. Optional dependency. If Indeed/Glassdoor change their site, python-jobspy breaks. |
| **Workday ATS** | Medium | Complex dict-format config (tenant/wd/site). Workday API endpoints change occasionally. 15 companies = 15 potential breakpoints. |
| **SuccessFactors** | Medium | Parses sitemap.xml files. Only 3 companies. MBDA already removed (DNS failure). |
| **Personio** | Medium | Uses XML job feed API. 10 companies. Personio may restrict access. |
| **LinkedIn guest API** | High | Unofficial, can break or get rate-limited at any time. |
| **HackerNews sources** | Low | Algolia API is stable, but "Who is Hiring" thread format could change. |
| **CV parser** | Medium | Regex-based section detection. Works for ~80% of CVs. Non-standard formats may miss skills. |

---

## Known Issues

| Issue | Severity | Notes |
|-------|----------|-------|
| 3 tests skip on Windows | Low | bash-only tests for `setup.sh` and `cron_setup.sh` — pass on Linux/Mac |
| ~~`test_main.py` still hits live Indeed~~ RESOLVED (M8 batch) | — | JobSpy stubbed via autouse fixture; `load_profile` patched. Runs offline in ~8 s (14 tests). Now part of canonical suite — `--ignore` removed from Makefile, agent-gate.sh, and CLAUDE.md. |
| Layer-4 embedding repost dedup not activated | Medium | Scaffolded in `backend/src/services/deduplicator.py` but gated behind `SEMANTIC_ENABLED`; ChromaDB-backed layer is opt-in and not yet wired into the default pipeline path. |
| ~~Batch 2.7 hybrid mode flag not wired to HTTP routes~~ RESOLVED 2026-06-10 | — | `GET /api/jobs?mode=hybrid` consults the retrieval stack and the dashboard requests it by default (a19db65); the LLM judge re-ranks on top via `COALESCE(llm_fit_score, score)`. |
| No skill inference beyond what the LLM extracts | Medium | Profile system relies on LLM-extracted skills + explicit user additions; implicit skill expansion from titles ("Data Scientist" → Python/SQL) not implemented. Partially mitigated by `skill_synonyms.py` canonicalisation (Batch 2.3). |
| python-jobspy not in backend/pyproject.toml core deps | Low | Intentionally optional (heavy dependencies). Indeed/Glassdoor source skips with warning if not installed. |
| GITHUB_TOKEN optional | Low | Without token, GitHub API rate limit is 60 req/hr. With token: 5000 req/hr. Profile enrichment may fail for users with many repos without a token. |
| pdfminer/cryptography conflict | Low | Environment-specific: pyo3 panic in cryptography lib breaks pdfplumber import in some environments |
| Heavy deps must stay lazy-imported | Low (guardrail) | `sentence_transformers`, `chromadb`, `rapidfuzz`, `sklearn`, `apprise` must be imported inside functions, never at module top level. Enforced by CLAUDE.md rules #11 + #16. A stray top-level import regresses pytest collection time by 150 ms – 2 s per process. |

---

## Test Coverage by Module

> **The per-file table lives in `README.md` ("Testing") and nowhere else.** It used to be
> duplicated here, and the copy rotted: it still listed `test_notifications.py` and
> `test_notification_base.py` (both deleted with the `NotificationChannel` ABC), named
> `cron_run.sh` for what `test_cron.py` actually exercises (`cron_setup.sh`), and carried
> per-file counts roughly a third of the real ones. Two copies of a number is one copy too
> many — README's is the measured one.

**Current green baseline:** 3,297 collected / 3,295 selected (2 `live` deselected), 0 failing,
3 skipped on Windows. Measure it, never quote it:
`cd backend && python -m pytest --collect-only -q -p no:randomly | tail -1`.

Broad coverage beyond the top-20 files: migrations, auth, feed, prefilter, channels, crypto,
dispatcher (rule consultation + timezone-aware quiet hours), scheduler, circuit_breaker,
conditional_cache, embeddings, retrieval, enrichment, dedup layers, Pillar-2 scoring dims,
score-dim columns, multi-dim scoring, hybrid retrieval, IDOR, account-mgmt, ghost-sweep,
application history, notification rules + tick + bundling, ledger filters, dim-score
round-trips, harness guards.

### Not covered or lightly covered

- `backend/src/utils/rate_limiter.py` — now has 5 dedicated tests in `test_rate_limiter.py`
- Live HTTP behavior — all tests use mocked responses, so real API format changes are not caught by tests
- Next.js frontend at `frontend/` — no automated UI tests yet (would need Playwright or similar)
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
# Output: 41 (40 live instances; the 2026-08-10 rotation dropped 6 dead upstreams)
```
