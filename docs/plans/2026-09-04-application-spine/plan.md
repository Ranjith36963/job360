<!-- doc: PLAN | status: ACTIVE | pr: — -->
# Plan: the application spine
Reads: `intent.md`, `spec.md`. Branch `feat/application-spine` off `main` 90ab21b. Process
(issue #480): Opus attacks the spec's security + fold sections → Sonnet writes the frozen
tests red → Sonnet builds → Fable reviews (bugs + conventions + spec compliance) → verifier
walks the real browser and a real MCP client → draft PR → **owner merges**.

## Files that change

Backend — migration
- `backend/migrations/0037_application_spine.up.sql` / `.down.sql` — two new tables, 12
  columns on `applications`, 7 on `application_receipts`, the eight fold steps, the down
  header stating exactly what a rollback costs (spec §Migration fold).

Backend — settings and services
- `backend/src/core/settings.py` — `SEARCH_UI_ENABLED`, `CATALOG_CRONS_ENABLED`,
  `APPLICATION_STATUS_EVENT_TYPES`, `APPLICATION_NOTE_EVENT_TYPES`,
  `APPLICATION_EXTRA_EVENT_TYPES`, `APPLICATION_EVENT_DETAIL_MAX_CHARS`,
  `APPLICATION_EVENT_PAYLOAD_MAX_BYTES`, `APPLICATION_EVENT_MAX_FUTURE_SECONDS`,
  `APPLICATION_ARTIFACT_MAX_CHARS`, `APPLICATION_ARTIFACT_MAX_VERSIONS`,
  `APPLICATION_ARTIFACT_KINDS`, `APPLICATION_RECEIPT_ANSWERS_MAX`,
  `APPLICATION_RECEIPT_FIELDS_MAX_BYTES`, `APPLICATION_FIT_REASONING_MAX_CHARS`,
  `WHATS_NEW_DEFAULT_WINDOW_DAYS`, `WHATS_NEW_MAX_EVENTS`,
  `EXPORT_HISTORY_MAX_APPLICATIONS`, `EXPORT_HISTORY_MAX_BYTES`,
  `EXPORT_HISTORY_MAX_PER_HOUR`. All via the existing `_env_flag` (`settings.py:418`) /
  `int(os.getenv(...))` style, plus one small `_env_list`.
- `backend/src/services/applications/__init__.py` (new).
- `backend/src/services/applications/status.py` (new) — event→status map, the legacy
  `stage` projection dict (spec R4), `replay_status(events)` used by both the writer and
  the frozen rebuildability test.
- `backend/src/services/applications/authorship.py` (new) — `actor_for(user) -> str`
  (`web` / `token:<name>` / `agent:<client>`), the single place `recorded_by` and `made_by`
  are produced (spec S3).
- `backend/src/services/applications/spine.py` (new) — birth-at-bring, `record_event`,
  `save_artifact` (version allocation), `save_fit`, `record_receipt`, `whats_new`,
  `export_history`. Pure validation helpers (`validate_event_type`, `parse_occurred_at`,
  `payload_bytes`) are module-level so tests hit them with no DB.

Backend — routes and the agent surface
- `backend/src/api/routes/applications.py` (new) — the eight tools' REST side plus
  `GET /applications/{id}/artifacts/{artifact_id}`; `/applications/export` declared **before**
  `/applications/{application_id}` (spec §Tool contracts).
- `backend/src/api/main.py` — `include_router(applications.router, prefix="/api")`.
- `backend/src/api/routes/bring.py` — create/find the application, snapshot the ad, append
  `brought`, return `application_id` + `status` (spec R1/R2).
- `backend/src/api/routes/receipts.py` — `create_receipt` writes through to the spine
  (spec R8); route shape unchanged.
- `backend/src/api/routes/tailor.py` — `generate` and `save_edit` also write an artifact
  version (spec R15).
- `backend/src/api/routes/search.py` — 404 both routes when `SEARCH_UI_ENABLED` is off
  (spec R12).
- `backend/src/api/mcp_server.py` — seven new tools, `record_application` enriched,
  `bring_job` returns `application_id`; each tool re-applies its route's gate by hand (M5).
- `backend/src/repositories/database.py` — spine query helpers; `application_events` +
  `application_artifacts` into `_PER_USER_TABLES` (`:1779`) and `_EXPORT_TABLES` (`:1798`);
  `purge_old_jobs` (`:775`) excludes `settings.USER_BROUGHT_SOURCE`.
- `backend/src/workers/settings.py:232,239` — `refresh_catalog` and `enrichment_sweep`
  appended only when `CATALOG_CRONS_ENABLED`.
- `backend/scripts/observe.py:43` — both new tables in `PER_USER_TABLES`.

Backend — tests
- `backend/tests/test_application_spine.py` (new, frozen — spec items 1–29).
- `backend/tests/test_migration_0037.py` (new, frozen — items 30–35).
- `backend/tests/test_search_flag.py` (new, frozen — items 36–37).
- `backend/tests/test_mcp_gate_parity.py` — seven `TOOL_ROUTES` rows (`:33-42`).
- `backend/tests/conftest.py` — `monkeypatch.setenv("SEARCH_UI_ENABLED", "1")` beside the
  existing `LOOP_WATCHDOG_ENABLED` line (`:148`) so the legacy search suite still runs.

Frontend
- `frontend/src/app/page.tsx` — applications home when the session cookie is present, the
  existing landing when not; landing copy loses "41 Sources" (spec R14).
- `frontend/src/app/applications/page.tsx` (new) — the list.
- `frontend/src/app/applications/[id]/page.tsx` + `ApplicationClient.tsx` (new) — server
  page **awaits `params`** (Next 16, hard rule #22) with a client child for interactivity,
  same shape as `oauth/consent/[rid]`.
- `frontend/src/components/applications/{ApplicationList,Timeline,ArtifactVersions,FitPanel}.tsx`
  (new).
- `frontend/src/components/layout/Navbar.tsx:22-29` — `NAV_LINKS` gains Applications, drops
  Dashboard when the flag is off.
- `frontend/src/middleware.ts` — `/applications` into `PROTECTED_PATHS:7-18`; `/dashboard`
  and `/jobs` 404 when `NEXT_PUBLIC_SEARCH_UI_ENABLED` is off. `/` stays public.
- `frontend/src/lib/api.ts` — eight helpers + `getArtifact`;
  `frontend/src/lib/api-types.ts` + `frontend/openapi.json` regenerated by
  `npm run gen:types` (never hand-edited; `check:types-drift` blocks the commit otherwise).
- `frontend/tests/e2e/applications-home.spec.ts` (new, frozen — item 38).

Docs and config
- `.env.example` + `frontend/.env.example` — `SEARCH_UI_ENABLED`,
  `NEXT_PUBLIC_SEARCH_UI_ENABLED`, `CATALOG_CRONS_ENABLED` and the caps.
- `ARCHITECTURE.md` — regenerated blocks (migration head, route table, env table) via
  `scripts/gen_doc_blocks.py`; DB-schema section gains the two tables.
- `docs/README.md` — index row for `plans/2026-09-04-application-spine/`.
- `.claude/skills/hard-rules/SKILL.md` — rule #3 records that slice 2 exempted
  `user_brought` from `purge_old_jobs`; M3 gains the two new append-only tables and their
  guard test names.
- `STATUS.md` — head moves to slice 2.

## Order of work
1. **Opus adversarial pass** on `spec.md` §Security guardrails and §Migration fold (effort
   high). Fold findings into the spec **before** any code — especially the fold's
   transaction boundaries and the `recorded_by` derivation.
2. **Sonnet A**: migration `0037` up/down + `test_migration_0037.py` red.
   **Sonnet B** (parallel): `test_application_spine.py` + `test_search_flag.py` red, and the
   `TOOL_ROUTES` rows.
   **Sonnet C** (parallel): the Playwright spec red + `api.ts` helper stubs.
3. **Sonnet A**: `services/applications/*` + `routes/applications.py` + `database.py`
   helpers until the spine tests pass.
   **Sonnet B**: write-through in `bring.py` / `receipts.py` / `tailor.py`, the search flag,
   the cron switch, `mcp_server.py` tools + gate parity.
   **Sonnet C**: the applications pages, the home split, nav, middleware.
4. Regenerate `api-types`; `python -m mypy` ratchet at zero; `ruff`; targeted pytest;
   `npm run type-check`, `lint`, `test:unit`, `check:types-drift`.
5. **Fable review**: `reviewer-bugs` + `reviewer-conventions` on the diff, plus a
   spec-compliance table (every R and S numbered against a file:line). Fix Important
   findings **in the code, never in the frozen tests**.
6. **Verifier** (`verify-job360`): a real MCP client walks the done-when — bring → save
   `cv` v1 → save `cv` v2 → `record_application` naming v2 → `record_event replied` →
   `record_event interview_requested` → `whats_new`; then the browser opens `/` and the
   application page and reads both CV versions. The table goes in the PR body.
7. `git add -A && bash scripts/agent-gate.sh` (run from `D:\dev\job360` — the gate hashes
   the session cwd and is worktree-blind); commit; push; **draft** PR; memory note.

Model economy: Opus for the migration fold, the authorship/ownership code and the review;
Sonnet for the routes, the MCP tool wrappers, the pages and the test bodies. Nobody
self-certifies — worker output comes back through the manager.

## Risks
- **The fold is the only irreversible-feeling step.** Mitigated by design: it copies and
  never deletes, so the down migration leaves every pre-migration row untouched (spec
  §Migration fold). The residual risk is post-migration events being dropped on a rollback;
  the down file says so in its header, and the owner should take a `db-backup` run before
  merging.
- **Two vocabularies (`status` vs `stage`) until slice 5** — a wrong mapping dict silently
  changes what the Kanban board shows. One dict, one frozen test over all six legacy stages.
- **Route-order collision** on `/applications/export` vs `/applications/{id}` — declared in
  the right order, and a test hits `/applications/export` to prove it.
- **`test_route_auth_coverage.py` and `test_mcp_gate_parity.py` will both go red** on the
  first new route/tool. That is the guard working; the fix is a row, never a skip.
- **`conftest` turning `SEARCH_UI_ENABLED` on for the suite** hides the off-state everywhere
  except `test_search_flag.py`. Accepted deliberately: the alternative is touching hundreds
  of legacy search tests that slice 5 deletes anyway.
- **Windows full-suite flake** (psycopg exit 139 on a second back-to-back run) — targeted
  gate locally, Linux CI is the verdict.
- **`NEXT_PUBLIC_SEARCH_UI_ENABLED` is build-time** — a redeploy, not a restart (spec C2).

## Proof
- Every frozen test in `spec.md` §Frozen tests green, plus `test_mcp_gate_parity.py` and
  `test_receipts.py::test_receipts_are_append_only` still green and untouched.
- Migration proof measured, not quoted: row counts for the four legacy tables before and
  after `up`, then after `down`, then after `up` again — the table in spec §Migration fold,
  printed in the PR body.
- The verifier's done-when walk (step 6) as a table in the PR: each step, the tool called,
  the id returned, what `whats_new` showed.
- After merge, on production: `railway run -s Postgres` re-runs the same count query
  (baseline recorded 2026-09-04: applications 3 · stage_history 0 · receipts 0 ·
  tailored_documents 8) and the owner brings one real job through Claude Code. Prod is the
  only place the owner's daily-use measure can be closed.
