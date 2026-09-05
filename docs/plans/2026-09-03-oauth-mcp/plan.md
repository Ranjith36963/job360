<!-- doc: PLAN | status: ACTIVE | pr: — -->
# Plan: OAuth 2.1 authorization server for MCP clients
Reads: `intent.md`, `spec.md`. Branch `feat/oauth-mcp` off `main` 7c6f433. Process (issue #479):
Opus attacks the spec's security section → Sonnet writes frozen tests red → Sonnet builds →
Fable reviews (bugs + conventions + spec compliance) → verifier walks the browser → draft PR →
owner merges.

## Files that change
Backend
- `backend/migrations/0036_oauth.up.sql` / `.down.sql` — five tables (spec §Design).
- `backend/src/core/settings.py` — `OAUTH_REDIRECT_ALLOWLIST`, `OAUTH_ALLOW_LOOPBACK_REDIRECTS`,
  `OAUTH_ACCESS_TOKEN_TTL_SECONDS`, `OAUTH_REFRESH_TOKEN_TTL_SECONDS`, `OAUTH_CODE_TTL_SECONDS`,
  `OAUTH_AUTHORIZE_TTL_SECONDS`, `OAUTH_REUSE_GRACE_SECONDS`, `OAUTH_REGISTER_MAX_PER_HOUR`,
  `OAUTH_REGISTER_MAX_PER_HOUR_GLOBAL`, `OAUTH_AUTHORIZE_MAX_PER_MIN`,
  `OAUTH_TOKEN_FAIL_MAX_PER_MIN`, `OAUTH_BEARER_FAIL_MAX_PER_MIN`, `OAUTH_CLIENT_PRUNE_DAYS`,
  `OAUTH_MAX_CLIENTS`, `OAUTH_PRUNE_SAMPLE`.
- `backend/src/services/auth/magic_link.py` + `backend/src/api/routes/auth.py` — optional
  `next` on the magic-link request, carried in the emailed link (spec R9).
- `backend/src/services/auth/oauth_clients.py` (new) — register / load / allow-list / prune.
- `backend/src/services/auth/oauth_flow.py` (new) — requests, decision, codes, PKCE, tokens,
  refresh, revoke, `resolve_access_token`.
- `backend/src/api/routes/oauth.py` (new) — `/api/oauth/*`.
- `backend/src/api/routes/well_known.py` (new) — the two discovery documents.
- `backend/src/api/main.py` — include both routers (well-known mounted at root, not `/api`).
- `backend/src/api/auth_deps.py` — `auth_via` literal + prefix dispatch.
- `backend/src/api/middleware.py` — Origin-check exemption for `/api/oauth/{register,token,revoke}`.
- `backend/src/api/mcp_server.py` — 401 challenge header with `resource_metadata`.
- `backend/src/repositories/database.py` — `oauth_grants` in `_PER_USER_TABLES` + `_EXPORT_TABLES`.
- `backend/scripts/observe.py` — `oauth_grants` in `PER_USER_TABLES`.
- `backend/tests/test_oauth_server.py` (new, frozen), `backend/tests/test_migrations.py` (0036
  up/down/up if the existing pattern is per-migration).
Frontend
- `frontend/next.config.ts` — two `/.well-known/*` rewrites.
- `frontend/src/middleware.ts` — `/oauth` in `PROTECTED_PATHS`.
- `frontend/src/app/oauth/consent/[rid]/page.tsx` + `ConsentClient.tsx` (new).
- `frontend/src/app/settings/connect/page.tsx` — "Connected apps" section.
- `frontend/src/app/(auth)/login/page.tsx` + `frontend/src/app/auth/magic/page.tsx` — pass
  and honour `next` on the magic-link path (spec R9).
- `frontend/src/lib/api.ts` — four helpers; `frontend/src/lib/api-types.ts` regenerated.
- `frontend/e2e/oauth-consent.spec.ts` (new, frozen).
Docs
- `ARCHITECTURE.md` generated blocks (migration head, routes, env table) via the existing
  generator; `docs/product/pillars/01-user-pillar.md` auth section: one paragraph + env rows.

## Order of work
1. Opus adversarial review of `spec.md` §Security guardrails (effort high). Fix Important
   findings in the spec before any code.
2. Sonnet A: frozen backend tests red (`test_oauth_server.py`) + migration files.
   Sonnet B (parallel): frozen Playwright spec + frontend helpers/types stubs.
3. Sonnet A: backend services + routes + auth_deps + middleware + mcp 401 + data tables, until
   the frozen tests pass. Sonnet B: consent page + connected apps + rewrites + middleware.
4. Regenerate api-types; mypy ratchet; ruff; targeted pytest; `npm run type-check`,
   `lint`, `test:unit`.
5. Fable: reviewer-bugs + reviewer-conventions on the diff; spec-compliance table; fix
   Important findings (code, never frozen tests).
6. Verifier agent walks: register → authorize → login bounce → consent → code → token → MCP
   `tools/list` → revoke → 401. Table goes in the PR.
7. `git add -A && bash scripts/agent-gate.sh`; commit; push; draft PR; memory note.

## Risks
- **Claude.ai/ChatGPT can only be tested in prod** (they need a public HTTPS URL). Local
  proof = the verifier's scripted client + the spec table of what they need. "Done when"
  is closed by the owner connecting after merge; the PR says so.
- `resource` and issuer string mismatches are silent on the client side (spec C2/C3).
- Windows full-suite flake (C4) — targeted gate, Linux CI verdict.
- The Next middleware login bounce: `next=` is a pathname only, which is why `rid` lives in
  the path; a query-string design would lose it.

## Proof
- Frozen tests listed in spec §Frozen tests all green; gate-parity test untouched and green.
- Verifier table (browser walk) in the PR body.
- `curl -s https://job360.uk/.well-known/oauth-authorization-server` after merge shows
  `issuer: https://job360.uk` (owner runs; CI cannot).

## Diff vs plan
Built as planned, plus these deviations — each one is a review finding, not a design change:

- **Grants show the client's first registered redirect URI** (`oauth_flow.list_grants_for_user`).
  A grant has no redirect column and the code row that carried one is pruned after a day, so
  the client's `redirect_uris[0]` is the only durable source. Spec R8 amended to say so.
- **Bugs review (P1): open redirect through the URL parser.** `safe_next` (backend) and
  `safeNext` (frontend) accepted `/\evil.com`, `/\t/evil.com` etc., which the WHATWG parser
  resolves to `https://evil.com/`. Both now refuse backslash, tab, CR, LF and non-printable
  chars; tests on both sides. Nits fixed with it: `\Z` regex anchors for PKCE strings, refresh
  sentinel (`replaced_by = 0`) cleared on every post-claim failure, `/revoke` with no
  `client_id` is a no-match (never a revoke), `/register` refuses on `Content-Length` before
  buffering, `prune()` deletes are bounded (`ctid IN (… LIMIT 100)`), loopback host check no
  longer strips a trailing dot, consent page ignores a focus refetch while the decision POST
  is in flight.
- **Conventions review: the three browser-facing routes carry `response_model`**
  (`ConsentRequestResponse`, `ConsentDecisionResponse`, `GrantListResponse`) so
  `api-types.ts` sees real shapes and `api.ts` uses the generated types instead of local
  interfaces. The RFC endpoints (`/register`, `/authorize`, `/token`, `/revoke`) stay bare
  `Response` — their bodies vary by error branch. `list_grants` now sends `no-store`.
  `SITE_BASE_URL` "not set" warning moved from import time (`well_known.py`) to
  `api.main.lifespan` via a new `settings.SITE_BASE_URL_IS_EXPLICIT`. `safeNext` re-export
  shim dropped from the login page; the test imports `@/lib/safe-next`.
- **Not in the file list:** `backend/tests/test_route_auth_coverage.py` learned root-mounted
  routers (`ROOT_ROUTE_MODULES`, prefix map) and 7 `PUBLIC_ROUTES` entries;
  `scripts/gen_doc_blocks.py` reads each router's mount prefix from `main.py` so the
  ARCHITECTURE route table prints `/.well-known/*` at the root; `.env.example`,
  `ARCHITECTURE.md` (env rows + regenerated blocks) and `docs/product/pillars/01-user-pillar.md`
  (§2.5 + env rows) updated. `docs/README.md` index entry added.
- **Untouched on purpose:** the 60 pre-existing `doc_sync_check` drifts on main (incl. the
  unstamped `docs/plans/2026-09-03-mcp-server/*`) — out of scope for this slice.
