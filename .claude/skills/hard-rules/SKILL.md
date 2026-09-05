---
name: hard-rules
description: >-
  Job360's numbered hard rules — the load-bearing invariants that break
  production or corrupt data when violated. Consult BEFORE editing schema,
  the application spine, MCP tools, auth routes, notifications, or profile
  extraction.
---
<!-- doc: LIVING | last-verified: 2026-09-05 by slice 5 (delete the sourcing era) -->

# Hard Rules

Moved out of `CLAUDE.md` 2026-08-25. They were 42% of a file that loads
before every session, and per Anthropic's guidance a bloated CLAUDE.md is
the reason rules get ignored — not the reason they get followed.

Nothing here is weaker for being a skill. It loads when the work touches it.

## Mission rules (2026-09-03 — `docs/product/VISION.md` wins over everything below)

- **M1. The user brings the job; we never source, rank or recommend** (product rule 4). Never add a source, a scorer, a feed, or a "recommended for you". The old sourcing/scoring pipeline was deleted 2026-09-05 (slice 5, #483) — do not rebuild it.
- **M2. The agent thinks, Job360 remembers** (product rule 5). Before adding a feature ask: *could Claude Code / ChatGPT do this with its own tools?* If yes, expose a **store** tool, not a **do** tool. No Gmail readers, no fit judges, no outreach writers of our own.
- **M3. One door for history: `record_event`** — fixed event types (VISION.md), free text, `recorded_by`. Every artifact version is kept forever with `made_by` + `profile_version`. A receipt is append-only. Nothing rewrites history. **Built in slice 2 (application spine):** `application_events` + `application_artifacts` (migration `0037_application_spine`) — no runtime code in `backend/src/` may `UPDATE`/`DELETE` either table (guard: `tests/test_application_spine.py::test_events_are_append_only`, a grep over `backend/src/`); `applications.status`/`stage` are the one cache SLOT that IS updated (S7 — a slot is not history). `application_receipts.application_id` is set at INSERT time, never backfilled by an UPDATE (guard: `tests/test_receipts.py::test_receipts_are_append_only`).
- **M4. Free, pull, consent-first** (product rule 6). No credits, no paywall, no push. Agents connect over MCP with OAuth 2.1; personal `j360_…` tokens stay as the fallback.
- **M5. Every MCP tool = the same per-user route the web uses** (#12/#25 apply) and every new route gate must be re-applied in `backend/src/api/mcp_server.py` — parity is by hand, there is no shared gate.

The sourcing-era pipeline (search, scoring, dedup, enrichment, embeddings) was
deleted 2026-09-05 (slice 5, #483) along with the rules that only guarded it.
Two of those rules guarded facts that outlived the deletion and moved up into
the index below unchanged: dedup-key normalization (still used by a brought
job) and the digest notification path (dead code, kept only so nobody
resurrects it by accident).

## Still binding

An index of the 17 hard rules, one line each. Where a test guards a rule, the test is named — that is the real enforcement.

### Schema + data integrity
1. **`normalized_key()` in `models.py`** — never change without re-verifying the DB UNIQUE constraint. Wrong normalization = duplicate rows. Still bites `bring_job`: two users pasting the same ad share one row.
10. **Never INSERT into `jobs` with `user_id`/`tenant_id`** — `jobs` is the shared catalog (a brought job is a catalog row too). Per-user state lives in `user_feed`, `user_actions`, `applications`, `application_receipts`.

### Auth + multi-tenant routes
12. **Every per-user FastAPI route MUST `Depends(require_user)`** and scope queries by `user.id`. Never accept `user_id` from URL/body — trivial IDOR.
25. **Per-user mutating routes MUST scope by `user.id`** (extends #12) — derive from the session cookie / bearer / OAuth subject, never a parameter. Step 3 review caught 3 real IDOR violations.
26. **Account-mgmt routes (password/email/delete) MUST verify the current password BEFORE the mutation, then `response.delete_cookie("job360_session")`** (forces re-login).

### Heavy imports
11. **Never import `apprise` at module top level** (~30 MB) — lazy-import inside the function (see `dispatcher._get_apprise_cls`).
16. **Same for `sentence_transformers`, `chromadb`, `rapidfuzz`, `sklearn`** — top-level costs 150 ms – 2 s per pytest collection. Not even "just for typing".

### Extraction must be data-driven
28. **STRICT — ZERO hardcoded skill/keyword lists in profile extraction (`src/services/profile/`).** *(Owner rule, non-negotiable.)* **Banned:** any `*_SKILL_TERMS` / `*_TO_SKILL` / skill-keyword dict or denylist — hand-typed maps overfit one CV; prose→skill mapping belongs to the LLM. **FACT (verified 2026-08-11): no ontology is consulted.** Extraction is LLM + structural passes (CV headings, dependency manifests, GitHub language/topic stats). **ESCO is inert scaffolding, never built or shipped** — never cite it as running; reviving it means shipping artefacts, not flipping `ESCO_SKILL_NORMALISATION_ENABLED` (slice 5 renamed `SEMANTIC_ENABLED`; the old name is deliberately not honoured). Absence chain + both call sites: `docs/product/PILLAR1_EXTRACTION_AUDIT.md`. **Carve-out: `core/skill_synonyms.py` is RETAINED** — a vocabulary table, reads no CV input.
29. **"Filled shelves work harder; empty shelves stay SILENT."** An empty preference means "don't care" — never a penalty, never a guess, never a default we invent. Survives the pivot for the profile the agent reads: an unset field is absent, not zero. (The dim-scorer half of this rule went with the scorer in slice 5.)

### Notifications
23. **ONE `notification_rules` row per user** (`UNIQUE(user_id)`) governing ALL their channels. Dispatch converts UTC `now` to `users.timezone` via stdlib `zoneinfo` (**not `pytz`**) before comparing quiet hours — skipping it leaks notifications across BST/DST.
24. **`notify_mode` = `instant` | `daily` | `every_n_hours`.** `instant` sends inline from the API process; the others queue into `user_notification_digests` for a cron that has had no worker to run on since the `worker` + `Redis` services were deleted 2026-09-02 and `src/workers/` was deleted in slice 5 — **in prod, `daily`/`every_n_hours` never deliver.** Push is gone by decision 11 (pull via `whats_new`). Do not build on this path.

### Process + verification
4. **Always mock HTTP in tests** with `aioresponses`. Never live HTTP.
5. **Always run the relevant test suite** after a change.
6. **Read a file fully before editing** — logic, imports, dependents.
7. **Check if something exists before creating it.**
21. **Value-presence > schema-presence for new fields.** `assert "field" in body` passes against a `= 0` default and a serializer that never reads the column. Run a real input end-to-end and assert non-default. Pattern: `backend/tests/test_sourcing_era_deleted.py::test_bring_stores_without_scoring` (asserts the echoed description, not just the key).
22. **Next.js App Router work MUST consult Context7 docs first.** Training data for 14–15 is unreliable for 16: `params` is a `Promise` and must be `await`ed; `"use client"` on `page.tsx` silently disables `generateMetadata`. Also read `frontend/node_modules/next/dist/docs/`.
