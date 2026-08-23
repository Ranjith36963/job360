# CLAUDE.md
<!-- doc: LIVING | last-verified: 2026-08-21 by /sync -->
<!-- SIZE BUDGET: <= 2,000 words. This file is auto-loaded before every session,
     so it is POINTERS + CRITICAL GOTCHAS ONLY. Long-form history belongs in
     docs/harness/IMPLEMENTATION_LOG.md, reference tables in ARCHITECTURE.md, recipes in
     .claude/skills/. If you are about to add a paragraph here, add it there and
     leave a one-line pointer. CI enforces this (doc_sync_check.py). -->

## How to talk to me (STRICT — always follow)

**Explain everything in simple, plain English.** This is a strict rule, never skip it.

- Use short sentences and easy words. Imagine explaining to a smart friend who is not a coding expert.
- Avoid jargon. If a technical word is needed, say what it means in plain words right after it (one short line).
- No long walls of text. Get to the point: what happened, what I did, what's next.
- When I ask for something, show me the result in plain words first, then the details if needed.

## 🔴 `main` IS PRODUCTION. MERGING SHIPS TO REAL USERS.

Railway is GitHub-linked to `Ranjith36963/job360`, branch `main`. **Every merge auto-deploys** — no manual step, no staging gate. Re-provable any time with `railway deployment list --service backend --json` (read `meta.commitHash`). `/api/health` is **useless** here — it returns a hardcoded `"version": "1.0.0"`, so the deploy API is the only trustworthy source.

- Never merge "to tidy up" — merging is a release.
- **When the owner reports a problem, he is describing LIVE PRODUCTION.** Diagnose against prod first, then read code.
- Check prod is on the commit you are reading; several sessions merge here and `main` moves under you.

**You can read production directly — do it, don't ask the owner to paste things.**

| Source | How |
|---|---|
| Errors | Sentry MCP — `organizationSlug: "job360"`, `regionUrl: "https://de.sentry.io"` |
| Logs (backend, frontend, worker, Postgres, Redis) | `railway logs --service <name>` |
| Database | `railway run -s Postgres python <script>` using **`DATABASE_PUBLIC_URL`** (plain `DATABASE_URL` only resolves inside Railway) |
| Product analytics | PostHog MCP (EU) |
| Deploys | `railway deployment list --service <svc> --json` |

**Two traps that cost real time:** `railway logs` *streams*, so piping to `tail` returns nothing and looks exactly like "no access" — always `| head -N` with a `timeout`. And **never print secret values** (`railway variables` once dumped a live API key into a transcript); filter to key NAMES only.

## Quick Orientation

- **Branch:** `main`. Multi-commit work demands a preflight: verify `git branch --show-current`, clean tree, and `git fetch origin <branch>` HEAD alignment. Halt and surface on divergence — never silent rebase.
- **Canonical pre-commit verification:** `cd backend && python -m pytest -q -p no:randomly`. **Never quote a test count from a doc — measure it** (`python -m pytest --collect-only -q | tail -1`); three docs once disagreed by 400–800 tests. Runs against a **real Postgres** (docker-compose.dev.yml, port 5433) via the `sqlite3`/`aiosqlite` shims in `tests/conftest.py`, schema-per-test. HTTP is mocked with `aioresponses`; the suite must run offline. `test_main.py` is in the canonical run — do **not** re-add `--ignore=tests/test_main.py`.
- **Two deployables:** `backend/` (Python 3.9+, FastAPI, **Postgres via psycopg3**) and `frontend/` (Next.js 16, React 19). Runtime data in `backend/data/`. **Live on Railway at job360.uk** since 2026-07-02; five services: `backend`, `frontend`, `worker`, `Postgres`, `Redis`.
- **What automation is actually running:** the **GitHub Actions harness** — 26 workflows in `.github/workflows/` (repair, triage, doc-sync, ci, ci-offline, codeql, security, uptime, live-e2e, journey, product-health, db-backup, pr-shepherd…). The old agent loop (`docs/harness/maintenance/MISSIONS.md`) is **DORMANT — disabled 2026-06-21**. **Do not wait on it.**
- **What surprises new sessions:** `SOURCE_REGISTRY` has 41 entries but 40 unique source classes (`indeed` + `glassdoor` both alias `JobSpySource`) — measure it, never quote it. Heavy deps must be lazy-imported (#11/#16). Next.js 16 made `params` async (#22). Adding a source touches **five** files (#8/#13). Migrations auto-apply on FastAPI boot via `lifespan` in `src/api/dependencies.py`.

## Hard Rules (load-bearing, numbered, do not violate)

An index, one line each. Where a test guards a rule, the test is named — that is the real enforcement.

### Schema + data integrity
1. **`normalized_key()` in `models.py`** — never change without re-verifying the deduplicator AND the DB UNIQUE constraint. Wrong normalization = duplicate rows or missed dedup.
3. **`purge_old_jobs()` in `database.py`** — never change without explicit confirmation. Wrong threshold = data loss.
10. **Never INSERT into `jobs` with `user_id`/`tenant_id`** — `jobs` is the shared catalog. Per-user state lives in `user_feed`, `user_actions`, `applications`.
17. **`job_enrichment` + `job_embeddings` must NOT gain `user_id`** (same reason as #10). Per-user scoring happens at read time via `JobScorer(..., user_preferences=…, enrichment_lookup=…)`.

### Catalog scope + filters (owner product rules — full text: `docs/product/product_design_rules.md`)
30. **UK-only is a DOOR, not a penalty; never hand-enumerate an UNBOUNDED set.** ONE chokepoint refuses foreign jobs (`services/uk_gate.check_uk`, `main.py:1040`) — never per-source, never a scorer penalty. Foreign cities are unbounded so never typed; UK places are finite, so the gate matches DATA (`src/data/uk_gazetteer/`). Countries stay enumerated only because that set is CLOSED; the country override runs BEFORE gazetteer matching. **Dry-run any location rule over the live catalog first** — the naive version blocked 48%. No scorer penalty, no city list (both deleted 2026-08-12); `base._is_uk_or_remote` is a fetch SKIP asking `uk_gate.names_foreign_place`. Guards: `tests/test_uk_gate.py`, `test_scorer.py`. **Gap:** the dual-site escape admits "London, Ontario" (needs DATA).
31. **Visa is a SPOTLIGHT, not a wall.** Visa ON never shrinks the catalog: every job still shows, sponsors ranked up + badged. Three states via `services/visa_signal.detect_visa_status` (sponsors / no_sponsorship / unknown) because `jobs.visa_flag` conflates "says no" with "never mentioned". Refusal is tested BEFORE offer. Guard: `tests/test_visa_signal.py`.

### Sources (recipes: `.claude/skills/add-source/SKILL.md`)
2. **Never change `BaseJobSource`** (constructor, properties, retry, `_get_json`/`_post_json`/`_get_text`) without checking every source file that inherits it.
8 + 13. **Adding/removing a source = FIVE surfaces:** `SOURCE_REGISTRY`, `_build_sources()`, `RATE_LIMITS`, `tests/test_cli.py`, `tests/test_api.py`. Guards (hardcoded counts that must move together): `tests/test_cli.py:52`, `tests/test_api.py:43,56,158,163`.
14. **Conditional fetch is opt-in** — only call `_get_json_conditional()` when the upstream really honours ETag/Last-Modified.
15. **New sources MUST set `.category`** (`ats`/`rss`/`keyed_api`/`free_json`/`scrapers`/`other`) or a `NAME_TIER` override in `scheduler.py`; untagged falls to the 60-min tier. Folder ≠ tier (`teaching_vacancies` is in `apis_free/` but is `rss`).

### Heavy imports
11. **Never import `apprise` at module top level** (~30 MB) — lazy-import inside the function (see `dispatcher._get_apprise_cls`).
16. **Same for `sentence_transformers`, `chromadb`, `rapidfuzz`, `sklearn`** — top-level costs 150 ms – 2 s per pytest collection. Not even "just for typing".

### Auth + multi-tenant routes
12. **Every per-user FastAPI route MUST `Depends(require_user)`** and scope queries by `user.id`. Never accept `user_id` from URL/body — trivial IDOR.
25. **Per-user mutating routes MUST scope by `user.id`** (extends #12) — derive from the session cookie, never a parameter. Step 3 review caught 3 real IDOR violations.
26. **Account-mgmt routes (password/email/delete) MUST verify the current password BEFORE the mutation, then `response.delete_cookie("job360_session")`** (forces re-login).

### Scoring + enrichment
29. **"Filled shelves work harder; empty shelves stay SILENT."** An empty preference (salary, locations, workplace, experience, about_me) means "don't care" — never a penalty, never a per-job zero, never a guess. Dim scorers return a CONSTANT; prefilters pass everything; the judge prompt omits unset prefs; the frontend never blocks on one. Guard: `tests/test_design_rules.py` — **covers only the dim scorers + prefilter; the judge prompt and frontend are UNGUARDED**, check by hand.
9. **Scoring changes require running `test_scorer.py` AND `test_profile.py`.**
18. **Pillar 2 engines default off — but the gate is `ENGINEx_ENABLED OR <legacy flag>`** (`core/settings.py:255-258`), so `ENGINE2_ENABLED=true` runs Engine 2 with `ENRICHMENT_ENABLED` false. E1 on; E2/E3/E4 off. With all off, behaviour must *exactly* match pre-Pillar-2 — no semantic queries, no LLM calls. Test BOTH names.
19. **`JobScorer` default = 4 components MINUS 1 penalty** (Title 40 / Skill 40 / Location 10 / Recency 10, then **−30 negative title**; the −15 foreign penalty died 2026-08-12, #30). `SCORER_VERSION` = **7** — bump it whenever a score moves. The **4 extra dims** (8 total, not 7) activate on #20's ONE condition (`:587`); the `engine1` kwarg gates the KEYWORD half only (`:480-483`/`:560`), never the dims. Don't flip defaults silently.
20. **Multi-dim scoring is gated on `user_preferences` ALONE** — a missing `enrichment_lookup` gives each dim its documented NEUTRAL half, never zeros (#29). Guard: `test_scorer.py::test_dims_neutral_not_zero_when_enrichment_missing`.
27. **Multi-dim weights add 30 on top of the legacy 100 (raw max 130); the clamp to `[0, 100]` is load-bearing** — never remove it.

### Extraction must be data-driven
28. **STRICT — ZERO hardcoded skill/keyword lists in profile extraction (`src/services/profile/`).** *(Owner rule, non-negotiable.)* **Banned:** any `*_SKILL_TERMS` / `*_TO_SKILL` / skill-keyword dict or denylist — hand-typed maps overfit one CV; prose→skill mapping belongs to the LLM. **FACT (verified 2026-08-11): no ontology is consulted.** Extraction is LLM + structural passes (CV headings, dependency manifests, GitHub language/topic stats). **ESCO is inert scaffolding, never built or shipped** — never cite it as running; reviving it means shipping artefacts, not flipping `SEMANTIC_ENABLED`. Absence chain + both call sites: `docs/product/PILLAR1_EXTRACTION_AUDIT.md`. **Carve-out: `core/skill_synonyms.py` is RETAINED** — scoring/search vocabulary, reads no CV input.

### Notifications
23. **ONE `notification_rules` row per user** (`UNIQUE(user_id)`) governing ALL their channels. Dispatch converts UTC `now` to `users.timezone` via stdlib `zoneinfo` (**not `pytz`**) before comparing quiet hours — skipping it leaks notifications across BST/DST.
24. **`notify_mode` = `instant` | `daily` | `every_n_hours`.** `instant` sends inline; the others (and anything caught in quiet hours) queue into `user_notification_digests`, drained by the `notification_tick` ARQ cron → `send_bundle` (`force=True`; marks `sent`/retries/`dlq` after 5). Only delivery path: worker/tick → `dispatcher.dispatch()` → Apprise → `notification_ledger`. New dispatch paths need tests for all three modes AND both quiet-hours states.

### Process + verification
4. **Always mock HTTP in tests** with `aioresponses`. Never live HTTP.
5. **Always run the relevant test suite** after a change.
6. **Read a file fully before editing** — logic, imports, dependents.
7. **Check if something exists before creating it.**
21. **Value-presence > schema-presence for new engine-side fields.** `assert "field" in body` passes against a `= 0` default and a serializer that never reads the column. Run a real input end-to-end and assert non-default. Pattern: `tests/test_database.py::test_dim_columns_round_trip`.
22. **Next.js App Router work MUST consult Context7 docs first.** Training data for 14–15 is unreliable for 16: `params` is a `Promise` and must be `await`ed; `"use client"` on `page.tsx` silently disables `generateMetadata`. Also read `frontend/node_modules/next/dist/docs/`.

## Commands

```bash
# Backend — run from backend/
python main.py                                       # FastAPI on :8000
python -m src.cli run                                # full pipeline
python -m src.cli run --source arbeitnow --dry-run   # one source, dry run
python -m src.cli setup-profile --cv cv.pdf --linkedin li.pdf --github user
python -m src.cli status | sources | view --hours 24 --min-score 50
python -m pytest -q -p no:randomly                   # canonical run (needs Postgres up)
python -m pytest tests/test_scorer.py::test_name -v  # single test
python -m migrations.runner up | status | down       # migrations

# Frontend — run from frontend/  (⚠️ Next.js 16, see rule #22)
npm run dev | build | lint | type-check | test:unit | test:e2e
```

## Architecture (one paragraph + pointers)

Pipeline: **CLI (Click)** → **orchestrator `src/main.py`** (`run_search()`, `SOURCE_REGISTRY`, `_build_sources()`) → **sources** (`asyncio.gather`, tiered by `services/scheduler.py`, guarded by `circuit_breaker`) → **`services/skill_matcher.JobScorer`** → **4-layer `services/deduplicator.py`** → **Postgres** → **notifications + reports + CSV**.

- **`src/repositories/pg.py` is the single DB door** — an `aiosqlite`-shaped async driver whose `translate()` rewrites legacy SQLite SQL to Postgres at runtime. It is **production-critical, not test-only** (guard: `tests/test_pg_translate.py`). Every module does `from src.repositories import pg as aiosqlite`.
- **`docs/pillars/`** — the *authoritative* code-verified architecture reference (User / Search & Match Engine / Job Providers). Cross-check it first for any specific claim.
- **`ARCHITECTURE.md`** — system overview, full directory tree, DB schema, scoring detail, source-category list, dependency table, **and the canonical environment-variable table**.
- **`docs/README.md`** — index of every plan/design/eval doc (batch-2 decisions, step plans, evaluation report, tailored-CV design).
- **`docs/harness/IMPLEMENTATION_LOG.md`** — append-only batch history **and the phase/batch summaries that used to live here** (read FIRST when picking up unfamiliar work).
- **`docs/product/product_design_rules.md`** — owner product rules #29/#30/#31 in full.
- **`.claude/skills/add-source/SKILL.md`** — adding/removing a source (five surfaces) + adding a notification channel.
- **`STATUS.md`** — current phase, carry-overs, fragile-source table. **`CONTRIBUTING.md`** — branch/commit/PR conventions. **`backend/README.md`** / **`frontend/README.md`** — install + run.
- **Second brain:** older project memory lives at `D:\second-brain\wiki\projects\job360\`.
