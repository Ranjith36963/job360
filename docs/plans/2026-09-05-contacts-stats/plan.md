<!-- doc: PLAN | status: ACTIVE | pr: — -->
# Plan: contacts, outreach, stats, profile edits (slice 4, #482)

Spec: `spec.md`. Branch `feat/contacts-stats` from `origin/main` `1fba085`. Manager
(Fable) writes design + frozen tests, Sonnet builds, Opus reviews twice, Fable verifies.

## Files that change

Backend (`backend/`):

| File | Change |
|---|---|
| `migrations/0038_contacts_and_profile_edits.up.sql` / `.down.sql` | new tables + indexes (spec §Data model) |
| `src/core/settings.py` | `CONTACT_*`, `CONTACTS_PER_APPLICATION_MAX`, `CONTACTS_MAX_PER_HOUR`, `STATS_MAX_GROUPS`, `STATS_MAX_PER_HOUR`, `PROFILE_EDITABLE_PATHS`, `PROFILE_EXTRA_EDITABLE_PATHS`, `PROFILE_EDIT_*` |
| `.env.example` | every new parameter, one line each |
| `src/services/applications/contacts.py` | new: `add_contact(db, *, user_id, application_id, actor, …)`, `list_contacts(db, user_id, application_id)` |
| `src/services/applications/stats.py` | new: `compute_stats(db, user_id, since)` — the two grouped queries |
| `src/services/applications/spine.py` | `get_application_detail` gains `contacts`; `export_history` gains `contacts` + `profile_edits` |
| `src/services/profile/models.py` | `CVData.links: list[str]` |
| `src/services/profile/edits.py` | new: `validate_edit(path, value)` (dataclass-driven), `current_overlay(conn, user_id)`, `apply_overlay(profile, overlay)`, `record_edits(conn, user_id, actor, edits)` |
| `src/services/profile/storage.py` | `load_profile` applies the overlay after building the dataclasses |
| `src/api/routes/applications.py` | `POST /applications/{id}/contacts`, `GET /applications/stats` (before `/{id}`) |
| `src/api/routes/profile.py` | `PATCH /profile`; `_build_profile_response` adds `agent_edits` |
| `src/api/mcp_server.py` | tools `add_contact`, `stats`, `update_profile`; `get_profile` adds `agent_edits` + `editable_paths` |
| `src/repositories/database.py` | `_PER_USER_TABLES`, `_EXPORT_TABLES` |
| `scripts/observe.py` | `PER_USER_TABLES` |
| `tests/test_mcp_gate_parity.py` | 3 `TOOL_ROUTES` rows |
| `tests/test_slice4_contacts.py`, `test_slice4_stats.py`, `test_slice4_profile_edits.py` | frozen (written by the manager, red first) |

Frontend (`frontend/`):

| File | Change |
|---|---|
| `src/lib/api.ts` | `getApplication` type picks up `contacts`; `getProfile` picks up `agent_edits` (from regenerated types) |
| `src/app/applications/[id]/ApplicationClient.tsx` | **People** section: contacts list (name, role, email as text, LinkedIn as a link only when `https://`), then outreach artifacts + `outreach_sent` events grouped under it |
| `src/app/profile/page.tsx` (+ its client component) | "Edited by <set_by> on <date>" mark next to each overlaid field; values already come merged from `GET /profile` |
| `src/lib/api-types.ts`, `openapi.json` | regenerated (`npm run gen:types`), committed |
| `tests/e2e/application-people.spec.ts`, `profile-agent-edits.spec.ts` | Playwright, mocked API |

Docs: `ARCHITECTURE.md` generated blocks (routes/tests counts), `docs/README.md` index
line for this plan, `docs/plans/2026-09-03-mission-roadmap.md` slice 4 row → PR link.

## Order of work

1. Manager: settings parameters + migration 0038 + the three frozen test files (red).
2. Sonnet worker A (backend): `contacts.py`, `stats.py`, spine additions, routes,
   MCP tools, registries, parity rows. Runs the three slice-4 files + spine + parity +
   receipts + `test_pg_translate.py` + `test_database.py` until green. Ruff + mypy 0.
3. Sonnet worker B (backend, parallel with A after step 1): `models.py` `links`,
   `edits.py`, `storage.py` overlay, `PATCH /profile`, `_build_profile_response`,
   MCP `get_profile` additions. Runs `test_slice4_profile_edits.py` + every
   `test_profile*` + `test_storage*` file.
   Workers A and B touch disjoint files except `mcp_server.py` and
   `test_mcp_gate_parity.py` — A owns both; B hands its `update_profile` tool body to
   the manager who splices it in.
4. Sonnet worker C (frontend) after A+B: regen types, People section, profile mark,
   two Playwright specs; `type-check`, `lint`, `test:unit`, `check:types-drift` green.
5. Opus review pass 1 (`reviewer-bugs`) on the full diff; pass 2 (`reviewer-conventions`).
   Fix batch by a Sonnet worker with every finding pinned by a test in
   `tests/test_slice4_fixes.py`.
6. Manager verifier walk: fresh throwaway DB (`job360_verify_s4`), boot backend with
   `DATABASE_URL`, mint a token, run the spec §Done-when walk with `curl`, screenshot
   `/applications/{id}` and `/profile` in the real browser.
7. `git add -A && bash scripts/agent-gate.sh` under Monitor; commit; push; draft PR
   with the spec's Security section summarised; owner merges.

## Risks

- **Partial unique index through the pg shim.** `translate()` may not have seen
  `CREATE UNIQUE INDEX … WHERE`. Worker A runs `test_pg_translate.py` first and adds
  a case if needed; fallback is a full unique index on `(application_id, email)` plus
  `email` stored as `NULL` instead of `''` — spec is written for the partial index and
  the worker reports which one landed.
- **`load_profile` is on the hot path of every profile read and the tailor.** The
  overlay adds one indexed query; no N+1. Worker B asserts a single extra statement
  with a query counter in the test.
- **Web preferences save copies the overlay into the base** (spec §Flagged concerns).
  Accepted; documented in the tool description and the PR body.
- **Stats SQL portability.** `COUNT(DISTINCT CASE WHEN … THEN application_id END)` is
  plain Postgres; the shim only rewrites placeholders and DDL. Worker A checks the
  generated SQL through `translate()` in the test.
- **Frontend `[id]` page fetches server-side** — the Playwright spec mocks the API
  route the same way `application-detail.spec.ts` does (check its pattern first).

## Proof

- `python -m pytest tests/test_slice4_*.py tests/test_application_spine.py
  tests/test_mcp_gate_parity.py tests/test_receipts.py tests/test_pg_translate.py
  tests/test_database.py tests/test_profile*.py -q -p no:randomly` green; counts
  measured, never quoted.
- `python -m ruff check .` 0; `python -m mypy` 0 (the ratchet).
- Migration 0038 up + down on the throwaway DB; `migrations.runner status` clean.
- Real-app walk transcript in the PR body: contact + outreach visible; stats equal to
  a hand count; profile edit visible with the mark; second user sees nothing.
- CI green on the draft PR (ci, ci-offline, codeql, drift checks).
