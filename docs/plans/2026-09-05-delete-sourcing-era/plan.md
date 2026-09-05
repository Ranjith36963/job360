<!-- doc: PLAN | status: ACTIVE | pr: — -->
# Plan: delete the sourcing era (#483)
Branch `feat/delete-sourcing-era` from `origin/main` `1fba085`. Worktree
`.claude/worktrees/delete-sourcing`. SDLC playbook: frozen tests red → build → two Opus
reviews → fix batch with pins → verifier boot → gate → draft PR → owner merges.

## Baseline (measured 2026-09-05, before any change)
- `pytest --collect-only -q`: 3,739 tests / 244 files
- `grep -rn SOURCE_REGISTRY` (no `.git`, `node_modules`): 112 hits / 42 files
- `backend/src`: sources 7,196 lines · `main.py` 1,604 · workers 2,414 · D1 services ≈9,000
- `backend/scripts`: 10,951 lines, ≈10,300 sourcing-only
- frontend delete set ≈4,300 lines · workflows 30 (7 paused sourcing)

## Step 0 — frozen tests (manager)
`backend/tests/test_sourcing_era_deleted.py` — every R-row of the spec, red on this commit.
Token scan helper reads the repo from the test's own path, skips `.git`, `node_modules`,
`docs/plans`, `docs/_archive`, `docs/harness/IMPLEMENTATION_LOG.md`, `VISION.md`, and the
test file itself. Frontend Playwright `applications-detail-tailor.spec.ts` is written by the
frontend worker (red first, in its own run).

## Step 1 — three workers, disjoint files, same worktree
| Worker | Model / effort | Owns | Must not touch |
|---|---|---|---|
| **B** backend | Opus / high | spec D1, D2 (scripts under `backend/scripts` that tests import), D3, T1–T9, migration 0039, `.env.example`, `docker-compose.prod.yml` | `frontend/`, `.github/`, `scripts/`, `docs/`, `*.md` |
| **F** frontend | Sonnet / medium | spec D4, T10 (not the types regen) | everything outside `frontend/` |
| **H** harness + docs | Sonnet / medium | spec D5, D2 (the rest of `backend/scripts`), T11, T12 except CLAUDE.md, `docs/_archive/sourcing-era/`, `docs/README.md`, `STATUS.md`, pillars README, `backend/README.md`, `frontend/README.md` | `backend/src`, `backend/tests`, `frontend/`, `CLAUDE.md` |

Order inside B (each step leaves `python -c "import src.api.main"` working):
1. Move `detect_seniority` / `strip_seniority` / `_USER_EXPERIENCE_RANK` into
   `profile/seniority.py` + tests.
2. `bring.py` (T2) + `models.py` (T3) + `profile.py` (T4) + `health.py` (T5) + `main.py` (T6).
3. `git rm` D1; fix `database.py` (T7), `cli.py`, `dependencies.py`, `telemetry.py`,
   `shelf_audit.py`, `skill_normalizer.py` until import is clean.
4. `settings.py` (T1), `.env.example`, `pyproject.toml` (T9), `docker-compose.prod.yml`.
5. Migration `0039_drop_sourcing_tables.{up,down}.sql`.
6. Tests: D3 deletes, then trims; `conftest.py` loses the search-flag / rescore / enrichment
   fixtures. Run `pytest -q -p no:randomly tests/test_sourcing_era_deleted.py tests/test_bring*.py
   tests/test_profile.py tests/test_applications*.py tests/test_receipts*.py tests/test_tailor*.py
   tests/test_mcp*.py tests/test_oauth*.py tests/test_api.py tests/test_health*.py tests/test_cli.py
   tests/test_e2e_lifecycle.py tests/test_migrations*.py` green; then the full suite once.
7. `ruff check src tests`, `python scripts/mypy_ratchet.py` (or the project's mypy command)
   at zero. Report: files deleted (count + lines), tests collected, every KEEP module it had
   to edit and why.

F: delete D4 → move `TailorSection` onto `ApplicationClient` → bring redirect → middleware /
Navbar / sitemap / `api.ts` → write `applications-detail-tailor.spec.ts` → `npm run lint`,
`type-check`, `test:unit`, targeted Playwright. Reports the list of `api.ts` exports removed.

H: archive (git mv + FROZEN header) → workflows + scripts + drill rows → `gen_doc_blocks.py`,
`doc_sync_check.py` → docs. Runs `python scripts/drill_registry.py check`,
`python scripts/gen_doc_blocks.py --check` (will show the block stale until B lands — say so),
`python scripts/doc_sync_check.py`. Reports every rule it removed from `doc_sync_check.py`.

## Step 2 — integration (manager)
Regenerate `frontend/openapi.json` + `api-types.ts` (`npm run gen:types --prefix frontend`),
`python scripts/gen_doc_blocks.py --write`, fix ARCHITECTURE hand counts, run
`test_sourcing_era_deleted.py` + parity + MCP tests, `doc_sync_check.py`, `drill_registry.py`.

## Step 3 — two Opus reviews
- `reviewer-bugs`: the trims (T2, T4, T7, T11) and migration 0039 — anything a deletion
  silently broke; S1/S2/S6 checks by hand.
- `reviewer-conventions`: hard rules, guards edited-not-bypassed (S7), env-row parity, docs.
Findings → fix batch, each pinned in `tests/test_slice5_fixes.py` or the frozen file.

## Step 4 — verifier boot (real Postgres, throwaway DB, port 8012)
Fresh DB → boot (`init_db` applies 0039) → mint token → `POST /jobs/bring` (no score fields;
`application_id` present) → `GET /applications/{id}` → `POST /tailor/{job_id}/generate` still
answers (mocked LLM or a 4xx that proves the route exists) → `GET /profile` (no
`search_titles`) → `POST /profile/preferences` (no rescore log line) → `GET /api/health` (no
`sources_total`) → MCP `tools/list` count equals `EXPECTED_TOOLS` → `GET /api/jobs/1` is 404
→ down migration 0039 → up. Windows notes from slice 4 apply (selector loop policy, PYTHONPATH).

## Step 5 — gate, commit, PR
`git add -A && bash scripts/agent-gate.sh` under Monitor (`run_gate_delete.sh` targets this
worktree). Commits: (1) the deletion, (2) docs/roadmap/STATUS, (3) `CLAUDE.md` alone.
Draft PR body: measured before/after table, removed response fields, migration paragraph
(S8), Railway variable NAMES to delete, infra file touched (`docker-compose.prod.yml`),
follow-up issues (spec "Out of scope"), the merge condition (slice 2 live for a release).

## Conflict note
`#496` (URL fetch) and `#498` (contacts/stats) are open and touch `bring.py`,
`bring/page.tsx`, `profile.py`, `mcp_server.py`, `STATUS.md`, `ARCHITECTURE.md`,
`docs/README.md`. This branch is cut from `main` without them. When the owner merges them
first, rebase this branch and re-run Step 2 + the gate; the union-merge rule applies
(never `--ours`/`--theirs` wholesale).
