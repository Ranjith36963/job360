# Job360 Project Status — sourcing-era history
<!-- doc: FROZEN -->

> **FROZEN — closed or superseded.** Kept as evidence of the sourcing era (deleted 2026-09-05, #483). Live truth: docs/product/VISION.md. <!-- banner: auto -->

> Archived 2026-09-05 out of `STATUS.md`, verbatim, when slice 5 deleted the
> sourcing-era pipeline (job search, scoring, dedup, enrichment, embeddings,
> the search dashboard, the 40 job-source classes). This is the phase-by-phase
> build narrative for that product, kept as a historical record — not a guide
> for what to build next. `STATUS.md` itself now carries only the current
> state and a pointer here.

## Sourcing era (history) — Funnel batch LIVE (2026-08-05) — retrieve→enrich→rank→judge in prod

> **Freshest history:** `docs/harness/IMPLEMENTATION_LOG.md` § "2026-08-05 — The funnel batch"
> (PRs #224/#226/#227/#228/#229): bounded per-user candidate set (top-800), revived
> title ranking (Spearman −0.04→+0.29 vs LLM oracle), job text for ~65% of the
> catalog, user-feedback loop + learned preferences v1/v2, self-healing enrichment
> cron, embedding convergence. Open: prod semantic-search env flip (owner),
> linkedin scraper text.

> **⚠️ THIS FILE HAS ROTTED — read `CLAUDE.md` first.** Verified 2026-08-03: it pointed at
> `backend/src/services/profile/` and `backend/src/services/`, neither of which has existed since the
> clean-architecture restructure (they are `src/services/profile/` and
> `src/services/skill_matcher.py`); it named a merged head ~334 commits behind; it listed a
> sentinel file that does not exist; and it claimed an autonomous maintenance loop was
> running when that loop was disabled on 2026-06-21. An agent following it would edit paths
> that are not there. Treat every path and commit below as UNVERIFIED unless you have just
> checked it.

> **SHIPPED** (verified 2026-08-24: `backend/src/services/profile/two_pass.py:572`
> `run_two_pass_extraction`). The branch `feat/two-pass-profile-extraction` no
> longer exists — it merged. This block described it as "in flight" for two
> months after the work landed. Two-pass
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
> a periodic cron (every 5 min) + a bundling send drained the digest queue
> (fixes the P0 where digests queued but never sent); legacy global `.env`-webhook
> path removed (one path: worker/tick → dispatcher → Apprise → ledger); API is one
> `GET`/`PUT /settings/notification-rule`; frontend is one rulebook form. **Nav IA:**
> Channels is now a top-level page (`/channels`); the Settings gear holds only
> Notifications + Account. Off-state made unmistakable; notification-history filters
> fixed to server-side; Account/Channels error messages cleaned. Verified end-to-end
> in a real browser (Playwright manual-tester pass — every button/feature/state).
> Backend 1392 passed / 3 skipped; frontend type-check+lint clean, 107 unit tests.
> Full detail in `docs/harness/IMPLEMENTATION_LOG.md`. Only unverified corner: real external
> delivery to a live inbox. (Historical note: Slack/Telegram/Discord were deleted outright
> on 2026-08-24 — never configured in production, zero users ever connected one. See
> `docs/plans/2026-08-24-email-webhook-only-delivery.md`.)

**Last updated:** 2026-08-24 (doc truth check; the phase narratives below are older — see `docs/harness/IMPLEMENTATION_LOG.md` for the current history)
**Total tests:** measure it, never quote it — `cd backend && python -m pytest --collect-only -q -p no:randomly | tail -1` (2 `live` tests deselected offline; 3 skip on Windows)
**Source & test file counts:** measure, never quote — `git ls-files 'backend/src/sources/**/*.py' | grep -vE '__init__\.py|/base\.py' | wc -l` and `git ls-files 'backend/tests/test_*.py' | wc -l`. The authoritative figures lived in `ARCHITECTURE.md`'s counts table, which was guarded against the code.
**Job sources:** 41 entries in the job registry; 40 live instances since `indeed` + `glassdoor` shared one class; gov_apprenticeships restored 2026-06-16 on DfE Display Advert API v2 (M6 2026-06 dropped jobtensor, comeet, aijobs_global; the 2026-08-10 rotation dropped 6 more dead upstreams — aijobs, rippling, biospace, jobs_ac_uk, workanywhere, nhs_jobs_xml). CLAUDE.md's five-load-bearing-surfaces rule this once cited was retired with the registry.
**Latest merged head:** measure it, never quote it — `git rev-parse origin/main`. This line has been stale twice (it sat on a June commit for two months). **This branch's own work — the 2026-08-24 email/webhook-only channel deletion described above — is NOT on `main` yet**: `feat/delivery-email-webhook-only` has not merged, so `main` does not reflect the Slack/Discord/Telegram removal.
**Sentinel:** removed 2026-08-24. `.claude/step-3-verified.txt` does not exist and no code reads it — `docs/harness/step_3_plan.md:120` still describes a halt-on-sentinel flow that has nothing to halt on. A pointer to a file that was never written is worse than no pointer: it reads as proof that a check ran.

---

## Phase 1: Dynamic User Profile System -- COMPLETE

**Goal:** Replace hard-coded AI/ML keywords with user-provided profile data so Job360 works for any profession (sales, law, engineering, hospitality, etc.).

### What was built

| Component | File(s) | Status |
|-----------|---------|--------|
| Profile dataclasses | `backend/src/services/profile/models.py` | Done -- CVData, UserPreferences, UserProfile, SearchConfig |
| CV parser (PDF/DOCX) | `backend/src/services/profile/cv_parser.py` | Done -- pdfplumber + python-docx text extraction, LLM-only skill/title extraction via `llm_provider.py` (KNOWN_SKILLS regex removed in commit 804725c) |
| Preferences validator | `backend/src/services/profile/preferences.py` | Done -- form validation, CV+prefs merge |
| Profile storage | `backend/src/services/profile/storage.py` | Done -- `user_profiles` table, one row per user |
| Keyword generator | `backend/src/services/profile/keyword_generator.py` | Done -- UserProfile -> SearchConfig conversion (module deleted, slice 5) |
| Job scorer class | `backend/src/services/skill_matcher.py` | Done -- dynamic scoring using SearchConfig (module deleted, slice 5) |
| Job-source base properties | `backend/src/sources/base.py` | Done -- `self.relevance_keywords`, `self.job_titles`, `self.search_queries` (module deleted, slice 5) |
| Source file refactor (every source) | `backend/src/sources/*.py` | Done -- **zero** direct `src.core.keywords` imports remain; the 12 sources that need keywords read them via the inherited `self.*` properties (the other 28 never reference them) (folder deleted, slice 5) |
| Orchestrator wiring | `backend/src/main.py` | Done -- loads profile, creates scorer, passes config (module deleted, slice 5) |
| CLI setup-profile | `backend/src/cli.py` | Done -- interactive profile wizard |
| Profile tests | `backend/tests/test_profile.py` | Done -- 56 tests covering all profile modules |
| Dependencies | `backend/pyproject.toml` | Done -- added pdfplumber, python-docx |

### Backward compatibility

- `keywords.py` is NOT modified -- remains the default keyword source
- All existing function signatures preserved (`score_job()`, `check_visa_flag()`, etc.) — all deleted, slice 5
- The job-registry-size test assertion still lived in `tests/test_cli.py` (current N = 41; 40 live instances — `indeed`+`glassdoor` aliased one class) — deleted, slice 5
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
| DB error logging | `backend/src/cli_view.py` | Done -- `except Exception` blocks now log errors before returning empty (module deleted, slice 5) |
| Magic number elimination | `backend/src/main.py`, `backend/tests/test_main.py` | Done -- an instance-count constant replaces hard-coded 47 (both deleted, slice 5) |
| Schema migration | `backend/src/repositories/database.py` | Done -- `_migrate()` method uses PRAGMA table_info + ALTER TABLE for future columns |
| Source health tracking | `backend/src/main.py`, `backend/src/repositories/database.py` | Done -- detects sources returning 0 that previously had jobs, warns in logs |
| Rate limiter tests | `backend/tests/test_rate_limiter.py` | Done -- 5 tests: acquire/release, context manager, concurrency limit, delay, multi-concurrent |
| Source category metadata | `backend/src/sources/base.py`, every source file (40 classes / 41 registry entries at the time) | Done -- `category` class attribute (keyed_api/free_json/ats/rss/scrapers/other) (folder deleted, slice 5) |
| Integration tests | `backend/tests/test_main.py`, `backend/tests/test_database.py` | Done -- instance-count validation, failed source tracking, migration, source history |

---

## Phase 3+ (Future, as of the sourcing era)

- Skill inference from job titles (e.g., "Data Scientist" implies Python, SQL, statistics)
- AI-powered CV summarization for better keyword extraction
- Multi-profile support (different job searches simultaneously)
- Job recommendation engine based on profile match patterns — superseded by product rule 4 (never rank/recommend)
- Interview tracking and application pipeline — became the kept pipeline/kanban feature

---

## Matching Engines (deleted, slice 5)

Four engines were stacked **keyword → dimensions → hybrid → LLM judge**. All default OFF except the keyword engine:

| Engine | Service | Flag | Default |
|--------|---------|------|---------|
| #1 Keyword | `services/skill_matcher.py` (4-component 0–100) | dedicated flag — no legacy alias; gated the keyword half so it CAN be switched off | **true** |
| #2 Dimensions | `services/scoring_dimensions.py` — +30 seniority/salary/visa/workplace. **The scorer entered the dim path on `user_preferences` alone**, no flag involved; the flag gated only whether the **enrichment** LLM step had produced data for those dims to read. Without it they scored their neutral halves | dedicated flag **or** the legacy enrichment flag (enrichment data only) | false |
| #3 Hybrid | `services/embeddings.py` + `pg_vector_index.py` + `retrieval.py` (RRF fuse + cross-encoder rerank). Vectors lived in `job_embeddings.embedding`; a legacy Chroma wrapper had no production caller | reads: dedicated flag **or** the legacy semantic flag; embedding writes: the legacy flag alone | false |
| #4 LLM judge | `services/llm_matcher.py` | dedicated flag **or** the legacy matcher flag | false |

Engine #4 ran after the per-user feed write. Results stored on `user_feed` (migration 0017). Feed reads ranked by `COALESCE(llm_fit_score, score) DESC`. Measured: 18/18 judged in 89.8 s at concurrency 3; judge spread 20–92 vs keyword 30–43; 10/10 fit accuracy on labeled sample.

**Profile-version re-score (migration 0018, automatic, no new flags):** `user_feed` carried a nullable `profile_version` stamping which `user_profile_versions.id` produced the row's score — migration 0018 only ADDed the column, so rows written before it stayed `NULL` until something re-visited them. When a user saved a new profile (CV / LinkedIn / GitHub / preferences), the system detected whether the content actually changed and re-scored the catalog in the background against the new profile. Old LLM verdicts were cleared **only when the judge was on**; with it off, the default, no verdict was touched. Ordinary searches did **not** skip existing rows: every authenticated search also ran a catalog backfill, which re-scored the shared catalog for that user. Their scores held steady because the upsert froze the score only when the incoming `profile_version` **and** scorer-version both matched the stored ones, and overwrote it when either differed. A scorer-version bump therefore re-scored only the rows a run actually reached — the write set was bounded by a store-score floor and a top-N feed-candidate cap, not every `user_feed` row. A job's score changed only when the profile or the scorer changed, never just because time passed.

---

## What's Next (Step 4 — Ops hardening / Batch 4 — Launch readiness), as of the sourcing era

**Step 0..3 status:** all green and merged on origin/main.
- Step 0 (pre-flight hardening) closed 2026-04-24 at `e31cac7` with 1,018 tests + Makefile + bootstrap_dev + migration 0010 + check_env_example + pytest-xdist.
- Step 1 (engine→API seam) closed with multi-dim scoring + hybrid retrieval + per-dim score columns wired to the legacy jobs endpoint (migration 0011).
- Step 1.5 (post-Step-1 stabilisation) shipped reviewer fixes + dataclass round-trips + lazy-import startup safety.
- Step 1.6 locked the generator/reviewer worktree contract (`.claude/generator-commit.md` + `.claude/reviewer-verdict.md` + `make verify-batch`).
- Step 2 (API→UI seam) closed at `5cf60ea` with all 5 cohorts (foundations + components + page surfaces + SEO + TanStack Query + run-surface + E2E smokes) + reviewer R-1..R-4 fixes + sentinel.
- **Step 3 (new endpoints + Settings UI) closed at origin/main `7194d0e` PR #9 merge** — 8 new backend endpoints, migrations 0012/0013/0014, dispatcher rule consultation with timezone-aware quiet hours, periodic digest + ghost-sweep tasks, 5 new frontend pages, KanbanBoard polish, Cohort D toasts/a11y/loading skeletons, reviewer R-1..R-7 closed, sentinel `337fbda`.
- **Matcher batch (funnel→judge) merged post-Step-3** on `fix/per-user-search-and-scoring-gate` — the LLM judge module, migration 0017 (`user_feed` llm_* columns), the matcher pipeline stage, API llm_* fields, dashboard AI-verdict badge + COALESCE sort, three feature flags (all default off). Measured: 18/18 in 89.8 s, judge spread 20–92, 10/10 fit accuracy.
- ~~**Autonomous maintenance loop** running~~ — **DORMANT since 2026-06-21.** The live automation is the GitHub Actions harness in `.github/workflows/`, not these agents. MISSIONS.md has not moved since 2026-06-17.

**Step 4 — ops hardening (next, as of the sourcing era):** GitHub Actions CI matrix, Dockerfile + docker-compose, deploy platform config, secret manager integration, security headers middleware, `/livez` + `/readyz` split, worker timeouts, pip-audit + npm audit + gitleaks + bandit in CI, FastAPI request timeout middleware, LLM call timeouts, per-query DB deadlines, DB backup script + restore drill, an admin UI consuming the legacy source-health endpoint (Step-3 backend already shipped this).

**Step 5 / Batch 4 — launch readiness:** scope-down to top 10-15 sources, freemium metering, ICO £40 registration, privacy notice + LIA, ASA-compliant marketing copy, ~~Amazon SES wiring~~ (**DONE differently**: magic-link email ships via **Resend**'s HTTP API — `services/auth/email_sender.py`, live on the verified `job360.uk` domain since 2026-07-10. SES was never used), full password-reset (forgot-password) flow, friend dogfood, prod-Redis smoke.

**Step 3 carry-overs — ALL SHIPPED (closed):**
- V-01..V-03 form-validation library (RHF + zod) — DONE; forms now use RHF + zod validation.
- V-04 CV upload size cap + MIME allowlist — DONE; 413/415 responses wired, MIME allowlist enforced.
- V-05 OpenAPI → TS codegen — DONE; codegen pipeline in place.
- C-07 `@dnd-kit/core` + `@dnd-kit/sortable` keyboard a11y on KanbanBoard — DONE; `@dnd-kit/*` installed and keyboard a11y active.

---

## What Was Working (as of the sourcing era)

- Full 41-source pipeline (40 live instances) ran end-to-end (async fetch, score, dedup, store, notify) with a tiered scheduler wired into the orchestrator (Batch 3 / 3.5; M6 rotation removed jobtensor/comeet/aijobs_global; gov_apprenticeships restored 2026-06-16; 2026-08-10 rotation removed 6 dead upstreams)
- Profile system: CV + LinkedIn + GitHub enrichment → dynamic keywords → personalised search (LLM-only CV parser via multi-provider fallback, in order: **OpenAI `gpt-4o-mini` (PRIMARY)** / Gemini / Groq / Cerebras)
- Multi-user delivery layer (Batch 2): auth + per-tenant isolation + an ARQ worker + Apprise dispatcher + a feed-service single source of truth
- Multi-user profile storage (Batch 3.5.2): migration `0006_user_profiles` + per-user search-config resolution
- Conditional-cache machinery (Batch 3.5.3) existed and was tested, but **no source used it** — an `nhs_jobs_xml` pilot went with that source in the 2026-08-10 rotation
- All 8 keyed APIs skipped gracefully when keys were empty
- All ATS boards iterated over **297** company slugs actually polled — the ATS catalog held **302** across 11 platform lists, but one platform's slugs had no source class since the 2026-08-10 rotation, so 10 ATS sources polled the other 297
- All RSS/XML feeds parsed correctly with mocked data
- All HTML scrapers extracted job data with regex
- Multi-dim scoring activated as soon as the scorer was wired with user preferences — an enrichment lookup was optional, and without it each dim scored its NEUTRAL half, not zero (8-dim: title/skill/location/recency + seniority/salary/visa/workplace); legacy 4-component path unchanged by default
- Opt-in features behind flags (OFF by default): LLM enrichment, semantic search (sentence-transformers + the pgvector store)
- Postgres database with auto-purge (30 days); shared `jobs` catalog + per-user `user_feed` / `user_actions` / `applications`
- Email (the supported product surface) and webhook (an unsupported raw-JSON escape hatch) — both via the Apprise dispatcher, per-user channels (Batch 2). Slack/Discord/Telegram were removed 2026-08-24 (never configured in production, zero users ever connected one). The old built-in channel classes were REMOVED.
- CLI commands (7): run, status, view, api, sources, setup-profile, rescore-backfill — all but `api` and `setup-profile` deleted, slice 5
- Next.js frontend + FastAPI backend delivered the interactive UI

---

## What Was Fragile or Risky

| Source/Component | Risk | Notes |
|------------------|------|-------|
| **HTML scrapers** (5) | High | LinkedIn, Climatebase, 80000Hours, BCS Jobs, AIJobs AI all used regex parsing on HTML. Any layout change broke them silently (returns 0 jobs, no error). |
| **python-jobspy** (Indeed/Glassdoor) | Medium | If Indeed/Glassdoor changed their site, python-jobspy broke. |
| **Workday ATS** | Medium | Complex dict-format config (tenant/wd/site). 20 companies = 20 potential breakpoints. |
| **SuccessFactors** | Medium | Parsed sitemap.xml files. Only 3 companies. |
| **Personio** | Medium | Used an XML job feed API. 26 companies. |
| **LinkedIn guest API** | High | Unofficial, could break or get rate-limited at any time. |
| **HackerNews sources** | Low | Algolia API was stable, but the "Who is Hiring" thread format could change. |
| **CV parser** | Medium | Regex-based section detection, later replaced by LLM extraction. Works for ~80% of CVs. |

---

## Known Issues (as of the sourcing era)

| Issue | Severity | Notes |
|-------|----------|-------|
| Layer-4 embedding repost dedup not activated | Medium | Scaffolded but gated behind the semantic flag; needed an embedding lookup handed in and was never wired into the default pipeline path. |
| No skill inference beyond what the LLM extracts | Medium | Implicit skill expansion from titles ("Data Scientist" → Python/SQL) was never implemented. Partially mitigated by `skill_synonyms.py` canonicalisation (Batch 2.3, kept). |
| python-jobspy not a core dep | Low | Intentionally optional (heavy dependency). Deleted entirely with the Indeed/Glassdoor source, slice 5. |
| GITHUB_TOKEN optional | Low | Still true post-slice-5: without a token, GitHub API rate limit is 60 req/hr; profile enrichment may fail for users with many repos. |

---

## Quick Verification (as of the sourcing era)

```bash
# Profile setup works (all enrichment sources)
python -m src.cli setup-profile --cv path/to/cv.pdf --github username

# Pipeline with profile (command deleted, slice 5)
python -m src.cli run --dry-run --log-level DEBUG
# Log: "Using dynamic keywords from user profile"

# Check job-source count (module deleted, slice 5)
python -c "from src.main import SOURCE_REGISTRY; print(len(SOURCE_REGISTRY))"
# Output: 41 (40 live instances; the 2026-08-10 rotation dropped 6 dead upstreams)
```
