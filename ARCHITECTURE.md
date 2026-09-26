# Job360 Architecture
<!-- doc: LIVING | last-verified: 2026-09-26 by the daily truth check -->

> **Mission (2026-09-03, [`docs/product/VISION.md`](docs/product/VISION.md)):** Job360 is the memory and context layer for the seeker's own AI agent. The agent finds the job, judges fit, writes the CV, reads Gmail, does outreach; Job360 stores the profile, every artifact version, every typed event and the receipt. **We never source, rank or recommend jobs.**
>
> This file describes the one path the app has: `api/routes/bring.py` (`POST /jobs/bring`, link or text) → `api/routes/receipts.py` (append-only `application_receipts`) → `api/routes/tailor.py` (reads, versions and renders the CV the agent wrote — no LLM of ours, decision 28) → `api/mcp_server.py` (the MCP tools at `/api/mcp` — count them with `grep -cF '@mcp.tool()' backend/src/api/mcp_server.py`; bearer `j360_…`, OAuth 2.1). The FastAPI app behind it is the route modules under `backend/src/api/routes/` (counts: [`docs/GENERATED.md`](docs/GENERATED.md)). Profile extraction (`services/profile/`) feeds it, and the application spine (`applications`, `application_events`, `application_artifacts`, `application_receipts`) records everything that happens to a brought job.
>
> **The sourcing-era pipeline was deleted 2026-09-05** (slice 5, #483): job search, keyword-driven scoring, four-layer dedup, LLM enrichment, embeddings, the search dashboard, and the 40 job-source classes that fed them. **The per-user notification-channel system (Apprise dispatcher, Slack/Discord/Telegram connect flows, digest queue) was deleted the same day.** None of that code exists in this repo any more, and nothing archives its history in-tree — git history is the record.
>
> Three Railway services: `backend`, `frontend`, `Postgres`. The `worker` and `Redis` services were deleted 2026-09-02, and `src/workers/` was deleted with the sourcing era — nothing runs in the background (no notifications, no crons).

## Repo facts

The countable facts — migration head, route and endpoint counts, test-file count, workflow count, hard-rule count — are machine-written into [`docs/GENERATED.md`](docs/GENERATED.md) by `scripts/gen_doc_blocks.py`. They are deliberately NOT repeated here: a generated block beside a prose copy is two copies again, and a block that rewrites itself on every merge is what made this file conflict with every open PR that touched its prose.

---

## Directory Structure

> **Post-Phase-4 layout** (commit `a814ae8`, 2026-03-XX): `config/` → `core/`, `filters/` + `notifications/` + `profile/` → `services/{...}`, `storage/` → `repositories/`. The old paths in earlier docs no longer exist.

```
job360/
├── backend/
│   ├── main.py                       # FastAPI uvicorn entry (thin; imports src/api/main.py)
│   ├── pyproject.toml                # Deps + dev extras, ruff/mypy/pytest config
│   ├── data/                         # Runtime (gitignored). NO jobs.db — the store is Postgres; `core.settings.DB_PATH` is a connection selector `repositories.pg.connect` maps to a schema, not a file
│   ├── migrations/                   # forward/reverse SQL migration pairs + runner.py (counts: repo facts above)
│   ├── src/
│   │   ├── cli.py                    # Click CLI: api, setup-profile
│   │   ├── models.py                 # Job dataclass + normalized_key() — DB UNIQUE constraint, still used by a brought ad
│   │   ├── api/                      # FastAPI: lifespan, CORS, dependencies, route modules (counts: repo facts above)
│   │   │   └── routes/               # health, applications, profile, auth, tailor, client_log, tokens, bring, receipts, oauth, well_known (root-mounted)
│   │   ├── core/                     # (post-Phase-4 rename from config/)
│   │   │   ├── settings.py           # Env vars, rate limits, the ESCO flag
│   │   │   ├── observability.py      # Sentry init
│   │   │   └── tenancy.py            # DEFAULT_TENANT_ID UUID for CLI/legacy rows
│   │   ├── services/                 # (post-Phase-4 merge of filters/ + notifications/ + profile/)
│   │   │   ├── auth/                 # passwords (argon2id), sessions (HMAC cookies), magic-link + system email (Resend/SMTP)
│   │   │   ├── applications/         # application-spine services — `ls` the folder for the module list
│   │   │   ├── fetch/                # the URL-fetch web fallback (extract, fetcher, ssrf guard.py, outcomes)
│   │   │   ├── tailoring/            # render + check a saved CV (no LLM since decision 28) — `ls` the folder for the module list
│   │   │   └── profile/              # deterministic extraction + storage — `ls` the folder for the module list
│   │   ├── repositories/             # (post-Phase-4 rename from storage/)
│   │   │   └── database.py           # Postgres via psycopg3 (`pg.py` aiosqlite-shaped shim) + forward-compat migration schema
│   │   └── utils/
│   │       ├── logger.py             # Rotating file + console logging
│   │       ├── audit_trail.py        # who-did-what rows for account changes
│   │       └── loop_guard.py         # refuses blocking work on the event loop
│   └── tests/                        # file count: `docs/GENERATED.md` (collected-test count: measure it, never quote it)
├── frontend/                         # Next.js 16 + React 19 + Tailwind 4
│   └── src/app/                      # App Router pages (server/client split; params is Promise<...> per Next.js 16)
├── docs/
│   └── product/                      # VISION.md (the mission), product_design_rules.md
├── .env.example
└── CLAUDE.md                         # Canonical AI agent instructions
```

---

## Job Model and the `jobs` Table

### Job Dataclass (`backend/src/models.py`)

`jobs` is now "the ad the user brought", nothing more — a shared catalog row keyed
by the same `(normalized_company, normalized_title)` pair a scraper used to
dedup against, because the uniqueness constraint on the table still needs it
(hard rule 1).

What the constructor still normalises is `models.Job.__post_init__`; how the key
is derived — every step of it, each one load-bearing and commented with the
incident that added it — is `models.Job.normalized_key`. Read them there; a
paraphrase here that drops a step is how the dedup bug comes back.

Dedup is the database's alone: the `UNIQUE(normalized_company, normalized_title)`
constraint, no dedup service, so two users pasting the same ad share one `jobs`
row. `grep -rn "normalized_key()" backend/src` for its call sites.

---

## Profile System

### Data Model

The fields are the dataclasses `services/profile/models.CVData` and
`services/profile/models.UserPreferences`. Read them there.

### Extraction pipelines

One entry point each — read the function and what it calls:
`services/profile/linkedin_parser.parse_linkedin_pdf`,
`services/profile/github_enricher.fetch_github_profile`,
`services/profile/cv_parser.extract_text`. All three are **deterministic**:
they pull TEXT out of a file or the GitHub API and read the structure they can
prove (a delimited Skills section, the Top-Skills sidebar, repo topics). None of
them calls a model — Job360 has none (decision 28, 2026-09-21; the provider
pool in `services/profile/llm_provider.py` and its four SDKs were deleted).

### Extraction (`services/profile/two_pass.py`)

Every input (CV, LinkedIn, GitHub, preferences) is read ONCE, deterministically,
into one shared `CVData`. Which function runs for which input is the body of
`services/profile/two_pass.run_two_pass_extraction` — read it there. The name
"two_pass" is historical: there was a second, LLM pass per input until decision
28 removed it.

**What fills the rest of the profile:** the user's own agent. It reads the
stored raw text through the MCP tool `get_profile` (key `raw`) and writes the
structured fields back with `update_profile`. Job360 never overwrites or clears
what the agent wrote — every merge in the extractor is fill-if-present.

Re-runs use only **stored** inputs (`raw_text`, `linkedin_raw_text`,
`github_repos_brief`, `about_me`) — no re-upload, no GitHub re-fetch. Each step
no-ops when its input is missing. Which of those outputs become skill
evidence, and under which source label, is
`services/profile/skill_tiering.collect_evidence_from_profile` — not every field
two-pass writes is read by it.

A profile save saves the profile — nothing else happens. There is no re-score
to trigger any more (the code that queued one, and the queue itself, were
deleted with the sourcing era).

### Seniority helpers (`services/profile/seniority.py`)

Profile extraction infers a candidate's seniority band from job titles
(`seniority.detect_seniority`), independent of any job search.

---

## Notification System

Job360 is **pull, not push** (VISION.md decision 11) — `services.applications.spine.whats_new`
is the whole of it. The Apprise dispatcher, the per-user channel CRUD, the digest queue and
`notification_rules` were all deleted 2026-09-05 along with the sourcing era — do not rebuild them.

---

## Database Schema

> **The schema is code, not prose.** The legacy baseline `init_db()` hands to
> `executescript()` is `repositories.database.JobDatabase.init_db`; every statement goes
> through `repositories.pg.translate` first, which rewrites SQLite spellings
> (`INTEGER PRIMARY KEY AUTOINCREMENT` → a Postgres identity column, `?` placeholders,
> `datetime('now')`, `INSERT OR IGNORE`) and strips FK clauses. The full schema is the
> baseline plus the forward migrations in `backend/migrations/` — read those two, in that
> order. `tests/test_pg_translate.py` pins the rewriting.

---

## API Routes

Every endpoint the app declares. Generated, so it cannot disagree with the
routers — a wrong endpoint reads like a contract and 404s whoever trusts it.

The full table — every method, path and router file, generated from the routers themselves — lives in [`docs/GENERATED.md`](docs/GENERATED.md).

## Configuration

### Environment Variables

There is no single list, and no grep finds them all — `core.settings._env_list`
and `validate_required_env` read under variable names. Most knobs are
`os.getenv` calls in `core/settings.py`; the rest sit where they are used —
`FRONTEND_ORIGIN` in `api.main`, `REQUIRE_EMAIL_VERIFICATION` in
`api.auth_deps.require_verified_user`, `SMTP_*` in `services.auth.email_sender`.

- `.env` lives in the repo root (see `.env.example`).
- Required in production: `core.settings._REQUIRED_PROD_VARS`, enforced at boot
  by `core.settings.validate_required_env`.

---

## Architectural Decisions

1. **Normalization for dedup on the one table that still needs it.** `jobs.normalized_key()` (company/title, suffix-stripped, lowercased) still backs the `UNIQUE(normalized_company, normalized_title)` constraint, because two users pasting the same job ad must land on one shared catalog row (hard rule 1). There is no dedup SERVICE any more — this is the DB constraint alone.

---

## Dependencies

### Production (backend/pyproject.toml)

`[project].dependencies` in `backend/pyproject.toml` is the list; every entry
carries the comment saying why it is there. A table here is a second copy that
goes stale without failing anything — this one still declared `scikit-learn`
long after #503 removed it.

The only fact worth repeating, because the manifest cannot say it:
**aiosqlite is NOT a dependency** — `repositories/pg.py` shims an
aiosqlite-shaped API over psycopg3, and heavy packages must stay lazy-imported
(rule #16, guarded by `backend/tests/test_heavy_imports_stay_lazy.py`).

### Dev (`pip install -e ".[dev]"` from `backend/`)

Read `[project.optional-dependencies].dev` in `backend/pyproject.toml` — each
entry carries the comment explaining why it is pinned or opt-in.

One thing the manifest does NOT tell you: **`pre-commit` is not in the extra**,
even though `CONTRIBUTING.md` makes `pre-commit run --all-files` a merge gate.
Install it separately.
