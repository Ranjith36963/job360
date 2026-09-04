---
name: hard-rules
description: >-
  Job360's numbered hard rules — the load-bearing invariants that break
  production or corrupt data when violated. Consult BEFORE editing schema,
  the application spine, MCP tools, auth routes, notifications, profile
  extraction — or any legacy sourcing/scoring code that still runs.
---
<!-- doc: LIVING | last-verified: 2026-09-03 by the mission sweep -->

# Hard Rules

Moved out of `CLAUDE.md` 2026-08-25. They were 42% of a file that loads
before every session, and per Anthropic's guidance a bloated CLAUDE.md is
the reason rules get ignored — not the reason they get followed.

Nothing here is weaker for being a skill. It loads when the work touches it.

## Mission rules (2026-09-03 — `docs/product/VISION.md` wins over everything below)

- **M1. The user brings the job; we never source, rank or recommend** (product rule 4). Never add a source, a scorer weight, a feed, or a "recommended for you". `SOURCE_REGISTRY` and `JobScorer` are legacy code waiting for slice 5 — obey their rules while they exist, never extend them.
- **M2. The agent thinks, Job360 remembers** (product rule 5). Before adding a feature ask: *could Claude Code / ChatGPT do this with its own tools?* If yes, expose a **store** tool, not a **do** tool. No Gmail readers, no fit judges, no outreach writers of our own.
- **M3. One door for history: `record_event`** — fixed event types (VISION.md), free text, `recorded_by`. Every artifact version is kept forever with `made_by` + `profile_version`. A receipt is append-only. Nothing rewrites history. **Built in slice 2 (application spine):** `application_events` + `application_artifacts` (migration `0037_application_spine`) — no runtime code in `backend/src/` may `UPDATE`/`DELETE` either table (guard: `tests/test_application_spine.py::test_events_are_append_only`, a grep over `backend/src/`); `applications.status`/`stage` are the one cache SLOT that IS updated (S7 — a slot is not history). `application_receipts.application_id` is set at INSERT time, never backfilled by an UPDATE (guard: `tests/test_receipts.py::test_receipts_are_append_only`).
- **M4. Free, pull, consent-first** (product rule 6). No credits, no paywall, no push. Agents connect over MCP with OAuth 2.1; personal `j360_…` tokens stay as the fallback.
- **M5. Every MCP tool = the same per-user route the web uses** (#12/#25 apply) and every new route gate must be re-applied in `backend/src/api/mcp_server.py` — parity is by hand, there is no shared gate.

The numbered rules below are split by whether they survive the pivot. **Legacy** rules guard code that is still on `main` and still runs; break them and production still breaks. They die with the code in slice 5 (#483).

## Still binding

An index, one line each. Where a test guards a rule, the test is named — that is the real enforcement.

### Schema + data integrity
10. **Never INSERT into `jobs` with `user_id`/`tenant_id`** — `jobs` is the shared catalog (a brought job is a catalog row too). Per-user state lives in `user_feed`, `user_actions`, `applications`, `application_receipts`.

### Auth + multi-tenant routes
12. **Every per-user FastAPI route MUST `Depends(require_user)`** and scope queries by `user.id`. Never accept `user_id` from URL/body — trivial IDOR.
25. **Per-user mutating routes MUST scope by `user.id`** (extends #12) — derive from the session cookie / bearer / OAuth subject, never a parameter. Step 3 review caught 3 real IDOR violations.
26. **Account-mgmt routes (password/email/delete) MUST verify the current password BEFORE the mutation, then `response.delete_cookie("job360_session")`** (forces re-login).

### Heavy imports
11. **Never import `apprise` at module top level** (~30 MB) — lazy-import inside the function (see `dispatcher._get_apprise_cls`).
16. **Same for `sentence_transformers`, `chromadb`, `rapidfuzz`, `sklearn`** — top-level costs 150 ms – 2 s per pytest collection. Not even "just for typing".

### Extraction must be data-driven
28. **STRICT — ZERO hardcoded skill/keyword lists in profile extraction (`src/services/profile/`).** *(Owner rule, non-negotiable.)* **Banned:** any `*_SKILL_TERMS` / `*_TO_SKILL` / skill-keyword dict or denylist — hand-typed maps overfit one CV; prose→skill mapping belongs to the LLM. **FACT (verified 2026-08-11): no ontology is consulted.** Extraction is LLM + structural passes (CV headings, dependency manifests, GitHub language/topic stats). **ESCO is inert scaffolding, never built or shipped** — never cite it as running; reviving it means shipping artefacts, not flipping `SEMANTIC_ENABLED`. Absence chain + both call sites: `docs/product/PILLAR1_EXTRACTION_AUDIT.md`. **Carve-out: `core/skill_synonyms.py` is RETAINED** — scoring/search vocabulary, reads no CV input.
29. **"Filled shelves work harder; empty shelves stay SILENT."** An empty preference means "don't care" — never a penalty, never a guess, never a default we invent. Survives the pivot for the profile the agent reads: an unset field is absent, not zero. (The dim-scorer half of this rule is legacy, below.)

### Notifications
23. **ONE `notification_rules` row per user** (`UNIQUE(user_id)`) governing ALL their channels. Dispatch converts UTC `now` to `users.timezone` via stdlib `zoneinfo` (**not `pytz`**) before comparing quiet hours — skipping it leaks notifications across BST/DST.

### Process + verification
4. **Always mock HTTP in tests** with `aioresponses`. Never live HTTP.
5. **Always run the relevant test suite** after a change.
6. **Read a file fully before editing** — logic, imports, dependents.
7. **Check if something exists before creating it.**
21. **Value-presence > schema-presence for new fields.** `assert "field" in body` passes against a `= 0` default and a serializer that never reads the column. Run a real input end-to-end and assert non-default. Pattern: `backend/tests/test_database.py::test_dim_columns_round_trip`.
22. **Next.js App Router work MUST consult Context7 docs first.** Training data for 14–15 is unreliable for 16: `params` is a `Promise` and must be `await`ed; `"use client"` on `page.tsx` silently disables `generateMetadata`. Also read `frontend/node_modules/next/dist/docs/`.

## Legacy — sourcing era (code still runs; obey until slice 5 deletes it; never extend)

### Schema + data integrity
1. **`normalized_key()` in `models.py`** — never change without re-verifying the deduplicator AND the DB UNIQUE constraint. Wrong normalization = duplicate rows or missed dedup. (Still bites `bring_job`: two users pasting the same ad share one row.)
3. **`purge_old_jobs()` in `database.py`** — never change without explicit confirmation. Wrong threshold = data loss. **Slice 2 (application spine) did this: `source <> USER_BROUGHT_SOURCE` on both the direct DELETE and the cascade-child subquery — a brought job (and its application snapshot) survives the catalog purge. Guards: `tests/test_application_spine.py::test_purge_spares_a_brought_job` / `::test_the_job_snapshot_survives_a_purge`.**
17. **`job_enrichment` + `job_embeddings` must NOT gain `user_id`** (same reason as #10). Per-user scoring happens at read time via `JobScorer(..., user_preferences=…, enrichment_lookup=…)`.

### Catalog scope + filters (owner product rules — full text: `docs/product/product_design_rules.md`)
30. **UK-only gate applies to the SEARCH pipeline only — a brought job is global (Berlin, Tokyo accepted).** Never hand-enumerate an UNBOUNDED set. ONE chokepoint refuses foreign jobs (`services/uk_gate.check_uk`, called from `backend/src/main.py`) — never per-source, never a scorer penalty. Foreign cities are unbounded so never typed; UK places are finite, so the gate matches DATA (`backend/src/data/uk_gazetteer/`). Countries stay enumerated only because that set is CLOSED; the country override runs BEFORE gazetteer matching. **Dry-run any location rule over the live catalog first** — the naive version blocked 48%. No scorer penalty, no city list (both deleted 2026-08-12); `base._is_uk_or_remote` is a fetch SKIP asking `uk_gate.names_foreign_place`. Guards: `backend/tests/test_uk_gate.py`, `backend/tests/test_scorer.py`. **Gap:** the dual-site escape admits "London, Ontario" (needs DATA).
31. **Visa is a SPOTLIGHT, not a wall.** Visa ON never shrinks the catalog: every job still shows, sponsors ranked up + badged. Three states via `services/visa_signal.detect_visa_status` (sponsors / no_sponsorship / unknown) because `jobs.visa_flag` conflates "says no" with "never mentioned". Refusal is tested BEFORE offer. Guard: `backend/tests/test_visa_signal.py`.

### Sources (removal recipe: `.claude/skills/add-source/SKILL.md` — NEVER add one, M1)
2. **Never change `BaseJobSource`** (constructor, properties, retry, `_get_json`/`_post_json`/`_get_text`) without checking every source file that inherits it.
8 + 13. **Adding/removing a source = FIVE surfaces:** `SOURCE_REGISTRY`, `_build_sources()`, `RATE_LIMITS`, `backend/tests/test_cli.py`, `backend/tests/test_api.py`. Guards (hardcoded counts that must move together): `backend/tests/test_cli.py:55`, `backend/tests/test_api.py:43,58,160,165`.
14. **Conditional fetch is opt-in** — only call `_get_json_conditional()` when the upstream really honours ETag/Last-Modified.
15. **Sources MUST set `.category`** (`ats`/`rss`/`keyed_api`/`free_json`/`scrapers`/`other`) or a `NAME_TIER` override in `scheduler.py`; untagged falls to the 60-min tier. Folder ≠ tier (`teaching_vacancies` is in `apis_free/` but is `rss`).

### Scoring + enrichment (scorer/judge/enrichment are OFF for the product path — decision 17; the tailor stays as the web fallback)
29-legacy. **Dim scorers return a CONSTANT for an unset preference; prefilters pass everything; the judge prompt omits unset prefs.** Guard: `backend/tests/test_design_rules.py` — **covers only the dim scorers + prefilter; the judge prompt and frontend are UNGUARDED**, check by hand.
9. **Scoring changes require running `backend/tests/test_scorer.py` AND `backend/tests/test_profile.py`.**
18. **Pillar 2 engines default off — but the gate is `ENGINEx_ENABLED OR <legacy flag>`** (`backend/src/core/settings.py`, `ENGINE2_ENABLED` / `ENGINE3_ENABLED`), so `ENGINE2_ENABLED=true` runs Engine 2 with `ENRICHMENT_ENABLED` false. E1 on; E2/E3/E4 off. With all off, behaviour must *exactly* match pre-Pillar-2 — no semantic queries, no LLM calls. Test BOTH names.
19. **`JobScorer` default = 4 components MINUS 1 penalty** (Title 40 / Skill 40 / Location 10 / Recency 10, then **−30 negative title**; the −15 foreign penalty died 2026-08-12, #30). `SCORER_VERSION` = **8** (`backend/src/services/skill_matcher.SCORER_VERSION`) — bump it whenever a score moves. The **4 extra dims** (8 total, not 7) activate on #20's ONE condition (`:587`); the `engine1` kwarg gates the KEYWORD half only (`:480-483`/`:560`), never the dims. Don't flip defaults silently.
20. **Multi-dim scoring is gated on `user_preferences` ALONE** — a missing `enrichment_lookup` gives each dim its documented NEUTRAL half, never zeros (#29). Guard: `backend/tests/test_scorer.py::test_dims_neutral_not_zero_when_enrichment_missing`.
27. **Multi-dim weights add 30 on top of the legacy 100 (raw max 130); the clamp to `[0, 100]` is load-bearing** — never remove it.

### Notifications — digest path (the code is on `main`, but NOTHING RUNS IT since the worker + Redis services were deleted 2026-09-02)
24. **`notify_mode` = `instant` | `daily` | `every_n_hours`.** `instant` sends inline from the API process; the others queue into `user_notification_digests` for a `notification_tick` ARQ cron (`src/workers/tasks.py`) that no longer has a worker to run on — **in prod, `daily`/`every_n_hours` never deliver.** Push is gone by decision 11 (pull via `whats_new`); slice 5 deletes `src/workers/`. Do not build on this path.
