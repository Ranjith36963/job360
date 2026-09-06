# Spec: personal tokens + MCP server
<!-- doc: PLAN -->
> **PLAN — shipped.** A design record, not live truth; the code and `docs/product/VISION.md` win. <!-- banner: auto -->
Skills applied: `hard-rules` (#10 shared catalog, #12/#25 auth on every route,
#16 lazy imports, #26 current-password for account changes). Status: shipped. (The
`intent.md` this spec read from is deleted scaffolding, 2026-09-05 — git history holds it.)

## Requirements
R1. **Personal API tokens.** `POST /api/tokens {name}` → 201 `{id, name, prefix, created_at, token}`.
    `token` = `j360_` + 43 url-safe chars (32 random bytes). Returned ONCE; only `sha256(token)` is stored.
    `GET /api/tokens` → active tokens (`id, name, prefix, created_at, last_used_at`), never the secret.
    `DELETE /api/tokens/{id}` → 204, sets `revoked_at`. Max `API_TOKENS_PER_USER` (default 10) active.
R2. **Bearer path.** `Authorization: Bearer j360_…` authenticates `require_user` / `optional_user` /
    `require_verified_user` exactly like the cookie. A bad/revoked bearer → 401 (it does NOT fall back to
    the cookie). `CurrentUser.auth_via` is `"session"` or `"token"`.
R3. **Session-only routes.** The three token routes use `require_session_user` → 403
    `session_required` when called with a token. A leaked token cannot mint another.
R4. **MCP endpoint** at `POST /api/mcp` (streamable HTTP, stateless, JSON responses). No bearer → 401
    with `WWW-Authenticate: Bearer`. Eight tools, each calling the existing route function in-process
    with the token's user and a per-request DB connection:

    | tool | calls | returns |
    |---|---|---|
    | `get_profile()` | `profile.get_profile` | summary: is_complete, job_titles, skills_count, experience_level, has_linkedin, has_github |
    | `bring_job(title, company, description, location="", apply_url="")` | `bring.bring_job` | job_id, title, company, match_score, bucket, existing, scored, matched_skills, missing_required, url |
    | `get_job(job_id)` | `jobs.get_job` | same fields + description, dims, apply_url, action |
    | `tailor_documents(job_id)` | `tailor.generate` | doc_kind, status, text (polished ?? ai_draft), flagged_terms, quota |
    | `get_tailored_documents(job_id)` | `tailor.get_tailored` | same |
    | `record_application(job_id, channel="", note="")` | `receipts.create_receipt` | receipt id, sent_at, has_cv, has_cover_letter, url |
    | `list_receipts(job_id=None, limit=20)` | `receipts.list_receipts` | summaries + total |
    | `get_receipt(receipt_id)` | `receipts.get_receipt` | full receipt incl. cv_text |

    Route `HTTPException`s become tool errors with the same status + detail (`"404: Job not found"`).
R5. **Frontend** `/settings/connect`: name → Create → token shown once with Copy; list with Revoke;
    the `claude mcp add --transport http job360 <origin>/api/mcp --header "Authorization: Bearer <token>"`
    snippet filled with the fresh token. Tab added to `_tabs.tsx`.
R6. `api_tokens` is registered in `_PER_USER_TABLES`, `_EXPORT_TABLES` (export: no hashes — name,
    prefix, dates only) and `observe.py PER_USER_TABLES`.

## Design
- **Table** `api_tokens(id, user_id → users ON DELETE CASCADE, name, token_hash UNIQUE, prefix,
  created_at, last_used_at, revoked_at)`. Lookup is by hash (indexed via UNIQUE) — no timing channel on
  a 256-bit random value. `last_used_at` is written at most once per 5 minutes per token.
- **Resolution order** in `auth_deps`: header present → token path only; else cookie path. Explicit
  credential beats ambient one; a wrong explicit credential is an error, never a silent downgrade.
- **Failed-token rate limit**: `auth_rate_limit.check_and_record(f"api_token_fail:{ip}", max=
  API_TOKEN_FAIL_MAX_PER_MIN (30), window=60)` on each failed bearer; over the limit → 429.
- **MCP mount**: `src/api/mcp_server.py` builds `MCPServer("job360")` + tools. The sub-app is created
  inside an `mcp_runtime()` async context (used by the app lifespan AND by tests, because the
  auth fixture replaces the lifespan with a no-op and the SDK's session manager cannot be re-run).
  `main.py` mounts a tiny ASGI shim at `/api/mcp` that (1) checks the bearer, (2) sets a contextvar
  with the `CurrentUser`, (3) forwards to the current sub-app. Tools read the contextvar. No SDK
  `AuthSettings` — that would publish OAuth discovery metadata for a server that does not exist.
- `transport_security`: DNS-rebinding protection OFF unless `MCP_ALLOWED_HOSTS` is set (the backend
  sits behind the Next rewrite; the Host header is Railway's, not job360.uk). The bearer is the guard.
- `stateless_http=True, json_response=True`: no sticky session, no SSE, works through the rewrite.
- Heavy imports (mcp SDK, routes) are lazy inside `mcp_runtime()` (rule #16) so CLI runs and test
  collection do not pay for them.
- Audit log events: `api_token_create`, `api_token_revoke`, `mcp_tool_call` (tool name, user, status).

## Security guardrails (mandatory section)
- Secret never stored, never logged, never in the list response, never in the audit log.
- Token cannot: create/list/revoke tokens (R3); change password/email/delete account (rule #26 already
  requires the password); log out sessions.
- Brute force: 256-bit random + hash lookup + failed-attempt limiter per IP.
- Revocation is immediate (checked on every request; no cache).
- Account deletion cascades tokens (FK) and `_PER_USER_TABLES` erases them (belt and braces).
- Tool inputs pass through the same pydantic models as the web (`BringJobRequest` bounds: 40k chars,
  http(s) `apply_url`); tool outputs never include another user's data (all routes are owner-scoped).
- MCP request body capped by the SDK default (`max_request_body_size`).

## Flagged concerns
C1. **Claude.ai / ChatGPT connectors need OAuth.** Not this slice. Bearer covers Claude Code, Cursor,
    scripts, WhatsApp adapter.
C2. **Stateless mode drops server→client notifications** (progress, sampling). None of the eight tools
    need them.
C3. **`tailor_documents` costs an LLM call** and the monthly cap applies; the tool surfaces the 402
    detail verbatim so the agent can tell the user.
C4. **Next rewrite timeouts**: `tailor_documents` can take ~20–40 s. The rewrite has no explicit
    timeout today (the web app calls the same route through it). Watch in prod.
