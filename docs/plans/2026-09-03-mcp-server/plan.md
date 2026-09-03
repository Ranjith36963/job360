# Plan: personal tokens + MCP server
Reads: `intent.md`, `spec.md`. Branch `feat/mcp-server` off `main@97e6fbc`.

## Files that change
Backend
- `backend/migrations/0035_api_tokens.{up,down}.sql` (new)
- `backend/pyproject.toml` — `mcp>=2.1,<3` (Python ≥3.10; prod image is 3.12)
- `backend/src/services/auth/api_tokens.py` (new) — mint / hash / resolve / list / revoke
- `backend/src/api/auth_deps.py` — bearer path, `auth_via`, `require_session_user`
- `backend/src/api/routes/tokens.py` (new) — R1, R3
- `backend/src/api/mcp_server.py` (new) — R4: server, tools, `mcp_runtime()`, ASGI shim
- `backend/src/api/main.py` — include tokens router; mount `/api/mcp`; lifespan enters `mcp_runtime()`
- `backend/src/core/settings.py` — `API_TOKENS_PER_USER`, `API_TOKEN_FAIL_MAX_PER_MIN`, `MCP_ALLOWED_HOSTS`
- `backend/src/repositories/database.py` — `_PER_USER_TABLES`, `_EXPORT_TABLES`
- `backend/scripts/observe.py` — `PER_USER_TABLES`
- `backend/tests/test_api_tokens.py`, `backend/tests/test_mcp_server.py` (new, frozen first)

Frontend
- `frontend/src/lib/api.ts` — `createToken`, `listTokens`, `revokeToken`
- `frontend/src/app/settings/connect/page.tsx` (new); `frontend/src/app/settings/_tabs.tsx` — tab
- `frontend/tests/e2e/connect-agent.spec.ts` (new)
- `openapi.json`, `src/lib/api-types.ts` — regenerated

Docs
- `ARCHITECTURE.md` generated blocks (migration head 0035, route table)

## Order of work
1. Tests written and red: tokens (mint once/hashed/list/revoke/cap/session-only), bearer auth
   (works on `/api/auth/me`, wrong token 401 even with a valid cookie, revoked 401, rate limit 429),
   MCP (no bearer 401; `tools/list` = 8 names; `bring_job` → `get_job` → `record_application` →
   `list_receipts` round-trip over HTTP JSON-RPC; another user's receipt → error; token cannot call
   `/api/tokens`).
2. Backend green on real Postgres. Migration up/down/up.
3. Frontend tab + spec. `type-check`, `lint`, api-types regen.
4. Gate (`git add -A && bash scripts/agent-gate.sh`).
5. Verifier walks `/settings/connect` in the real browser; then a real `claude mcp add` against a
   local backend and one `bring_job` call from Claude Code (the dogfood the owner will repeat on prod).
6. Review passes per `REVIEW.md` (bugs + conventions). Fix Important only.
7. Commit, push, draft PR. Owner merges. Prod check: migration head 0035, `POST /api/mcp` → 401,
   owner mints a token on job360.uk and runs `claude mcp add`.

## Risks
- `mcp` 2.x API differs from 1.x (`MCPServer`, transport args moved to `streamable_http_app()`).
  Checked against the v2 docs 2026-09-03; pin `<3`.
- The SDK session manager needs a running task group; ASGITransport in tests runs no lifespan →
  `mcp_runtime()` is entered by the test. If the shim is called with no runtime → 503, never a crash.
- Calling route functions directly bypasses FastAPI's `Query()` defaults — tools pass every argument
  explicitly (`limit`, `offset`, `job_id`).
- Windows full suite flakes; targeted gate locally, Linux CI is the verdict.

## Proof
- `test_api_tokens.py` — 8 tests; `test_mcp_server.py` — 6 tests. All new; nothing existing touched.
- Playwright `connect-agent.spec.ts`: create → token shown once → snippet contains it → revoke.
- Verifier report + screenshots on the PR; a real Claude Code `bring_job` call in the PR body.

## Diff vs plan

What changed between the plan and what shipped, and why:

- **`/api/mcp` is a `Route`, not a `Mount`.** A Mount answers the slash-less
  path with a 307 that MCP clients do not follow. A callable *instance* on
  `add_route` gets the raw ASGI triple the SDK transport needs.
- **Data export is flattened** (`{"exported_at", **data}`), no `.data` nesting —
  nothing consumed the nested shape.
- **Review pass (bugs lens) found two Important bugs, both fixed + pinned**
  (`tests/test_mcp_gate_parity.py`): (1) tools call route *functions*, so a
  route's `Depends(require_verified_user)` never ran on the agent surface —
  the two tailor tools now go through `_verified_user()`, and a parity test
  reads each route's gate off its signature; (2) the failed-bearer throttle
  was checked before the lookup and keyed by IP only — behind the Next rewrite
  every agent shares one IP, so one junk client could 429 every valid token.
  Now: resolve first, throttle failures only. A Minor read-then-write race on
  the per-user token cap (self-inflicted, session-only) is left as is.
- **Conventions pass:** `tokens` added to the route-auth guard, three env vars
  documented in ARCHITECTURE.md, README says Python 3.10+ (`mcp` needs it).
  The two "Python 3.9+" lines in root/backend CLAUDE.md are the owner's.
- **The mypy ratchet was dead in CI** — since at least 2026-08-14 the step took
  1s: mypy exited 2 (numpy's stubs use PEP 695 `type` under a 3.10 target),
  zero errors parsed, "OK". `check_mypy_ran` now refuses a crashed run,
  `python_version` is 3.12 (what CI/prod run), and the 96 errors that
  accrued while nobody was looking are banked in `mypy_baseline.txt` — all in
  parked source/profile modules, none in this slice. Tracked as a follow-up
  issue; the ratchet bites again from this commit on.
- **Local dev Postgres `public` schema is stale** (pre-multi-tenant
  `applications`, no migration table). The verifier boots with
  `pg.TEST_MODE = True` to get a fresh, fully-migrated schema.
- **Playwright reuses whatever owns :3000** — the e2e run must set `E2E_PORT`.
