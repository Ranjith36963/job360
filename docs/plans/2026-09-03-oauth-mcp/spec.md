<!-- doc: PLAN | status: ACTIVE | pr: — -->
# Spec: OAuth 2.1 authorization server for MCP clients
Skills applied: `hard-rules` (M4 free/pull/OAuth 2.1 with personal-token
fallback, M5 gate parity in `mcp_server.py`, #12/#25 every per-user route scopes by `user.id`,
#16 lazy imports, #26 credential management is session-only). Status: shipped (PR #488), revised
after the Opus adversarial review (11 Important + 17 nits folded in; see §Review log). (The
`intent.md` this spec read from is deleted scaffolding, 2026-09-05 — git history holds it.)

## What the clients need (verified against live docs 2026-09-03)
Sources: MCP Authorization spec rev 2025-11-25; claude.com/docs/connectors/building/authentication;
developers.openai.com/apps-sdk/build/auth.

| Need | MCP spec | Claude.ai | ChatGPT |
|---|---|---|---|
| Flow | authorization code + PKCE **S256** | always sends S256 | S256 |
| Client type | — | public: `token_endpoint_auth_method: none` | `none` (or `private_key_jwt`, not needed) |
| Registration | DCR optional (CIMD preferred) | DCR supported (re-registers per fresh connection) | DCR via `registration_endpoint` |
| Redirect URI | exact match | `https://claude.ai/api/mcp/auth_callback` | `https://chatgpt.com/connector_platform_oauth_redirect`, `https://chatgpt.com/connector/oauth/{id}` |
| `resource` (RFC 8707) | client MUST send `https://job360.uk/api/mcp`; server MUST check audience | yes | yes |
| Protected-resource metadata (RFC 9728) | MUST, at `/.well-known/oauth-protected-resource/api/mcp` (root fallback) | reads `resource_metadata` from the 401 first | required |
| AS metadata (RFC 8414) | MUST at `/.well-known/oauth-authorization-server` | 10 s timeout | required |
| 401 | `WWW-Authenticate: Bearer resource_metadata="…"` (`scope` SHOULD) | **must be a real 401** — a 200 is ignored | same |
| `/token` body | `application/x-www-form-urlencoded` | 415 on JSON-only parsers is a known bug | same |
| Refresh | rotate for public clients | refreshes on 401 and ~5 min before expiry; expects `invalid_grant` when dead | not documented |

## Requirements

R1. **Discovery.** `GET /.well-known/oauth-authorization-server` → 200 JSON:
    `issuer=SITE_BASE_URL`, `authorization_endpoint={SITE}/api/oauth/authorize`,
    `token_endpoint={SITE}/api/oauth/token`, `registration_endpoint={SITE}/api/oauth/register`,
    `revocation_endpoint={SITE}/api/oauth/revoke`, `response_types_supported=["code"]`,
    `grant_types_supported=["authorization_code","refresh_token"]`,
    `code_challenge_methods_supported=["S256"]`, `token_endpoint_auth_methods_supported=["none"]`,
    `revocation_endpoint_auth_methods_supported=["none"]`, `scopes_supported=["job360"]`,
    `service_documentation={SITE}/settings/connect`.
    `GET /.well-known/oauth-protected-resource` and `…/oauth-protected-resource/api/mcp` → 200
    `{resource: "{SITE}/api/mcp", authorization_servers: ["{SITE}"], bearer_methods_supported:
    ["header"], scopes_supported: ["job360"], resource_documentation: "{SITE}/settings/connect"}`.
    All three: `Cache-Control: public, max-age=3600`, `Access-Control-Allow-Origin: *`, no
    auth, GET only. The frontend rewrites `/.well-known/oauth-authorization-server`,
    `/.well-known/oauth-protected-resource` (exact) **and**
    `/.well-known/oauth-protected-resource/:path*` to the backend (`next.config.ts`) — three
    rewrite entries, because `:path*` does not match the bare root path.
    The OAuth module logs one startup warning when `SITE_BASE_URL` is not set in the
    environment (the default is production's URL, wrong for every local instance).

R2. **Dynamic client registration** (RFC 7591). `POST /api/oauth/register` (JSON) with
    `redirect_uris` (1–10 absolute URLs), optional `client_name` (≤100 chars, default
    "Unnamed client"; Unicode bidi controls, format/control characters and non-printables
    are stripped at registration; the result is stored and later rendered as untrusted text),
    optional `token_endpoint_auth_method` (must be `none`), optional `grant_types` (subset of
    `authorization_code`, `refresh_token`), optional `response_types` (subset of `code`).
    Every redirect URI must pass the allow-list (S3). → 201 `{client_id, client_id_issued_at,
    client_name, redirect_uris, token_endpoint_auth_method: "none", grant_types,
    response_types}`. No `client_secret` ever. Errors are RFC 7591 §3.2.2: 400 `{error:
    "invalid_redirect_uri" | "invalid_client_metadata", error_description}`.
    `client_id` = `j360c_` + `secrets.token_urlsafe(24)` (32 chars). Registration is
    unauthenticated (the clients have no credentials yet) so it is rate-limited (S6) and the
    table is bounded (S7). Response carries `Access-Control-Allow-Origin: *` (no cookie).

R3. **Authorize.** `GET /api/oauth/authorize?response_type=code&client_id&redirect_uri&
    code_challenge&code_challenge_method=S256&state&scope&resource`.
    **Normative order: no error may be delivered by redirect until the client and the exact
    redirect URI have been verified.**
    - Step 1 — unknown `client_id`, or `redirect_uri` not exactly one of the client's registered
      URIs (string equality after the S3 normalisation; no wildcards), or `state` longer than
      512 chars → **400 JSON, never a redirect** (RFC 6749 §4.1.2.1).
    - Step 2 — any other problem (missing/other `response_type`, missing `code_challenge`,
      `code_challenge` not exactly 43 base64url chars, method ≠ S256, unknown scope, `resource`
      ≠ canonical MCP URL) → 302 to `redirect_uri` with `error` (`invalid_request` /
      `unsupported_response_type` / `invalid_scope` / `invalid_target`), `error_description`,
      and the `state` echoed. The redirect query is built with `urllib.parse.urlencode` —
      never string concatenation (`state` is attacker-supplied; CR/LF in `Location` is
      response splitting).
    - Valid → store an `oauth_authorization_requests` row (id `secrets.token_urlsafe(32)`,
      expires in `OAUTH_AUTHORIZE_TTL_SECONDS` = 1800 — long enough for the magic-link email
      round-trip) and 302 to `{SITE}/oauth/consent/{rid}`. Missing `scope` means `job360`.
      Missing `resource` **defaults to the canonical MCP URL** (never NULL) — the audience is
      always known (S13).
    - `Cache-Control: no-store` on every response.

R4. **Consent.** `GET /api/oauth/authorize/{rid}` (session cookie only, `require_session_user`)
    → 200 `{client_name, redirect_uri, scope, scope_description, user_email, expires_at}`; 404
    when the request is unknown, consumed or expired. `POST /api/oauth/authorize/{rid}/decision`
    (session only, JSON `{approve: bool}`) → 200 `{redirect_to}` where `redirect_to` is:
    - approve → `redirect_uri?code=…&state=…` — creates/reuses the user's active grant for that
      client (one active grant per `(user_id, client_id)`, enforced by a partial unique index
      `ON oauth_grants(user_id, client_id) WHERE revoked_at IS NULL`; a new approval after a
      revoke inserts a **new** row, never un-revokes), inserts an authorization code (hash only;
      `OAUTH_CODE_TTL_SECONDS` = 60; bound to grant, client, redirect_uri, code_challenge,
      resource, scope) and marks the request consumed;
    - deny → `redirect_uri?error=access_denied&state=…`, request consumed.
    A consumed or expired request → 404 on either call. The user's identity for the grant is the
    session's, never a body field (#25).

R5. **Token.** `POST /api/oauth/token`. The handler reads `Content-Type` itself: only
    `application/x-www-form-urlencoded` is parsed (`await request.form()`); anything else →
    400 `invalid_request` (not FastAPI's 422). Response JSON with `Cache-Control: no-store`,
    `Pragma: no-cache`, `Access-Control-Allow-Origin: *`.
    - `grant_type=authorization_code` + `code` + `client_id` + `redirect_uri` + `code_verifier`
      (+ optional `resource`): the code is **claimed atomically** —
      `UPDATE oauth_authorization_codes SET used_at = now WHERE code_hash = ? AND used_at IS NULL
      RETURNING …`; zero rows = already consumed or unknown. Then: unexpired, belongs to
      `client_id`, `redirect_uri` byte-equal, `resource` (if sent) equal to the stored one,
      `base64url(sha256(code_verifier))` (no padding) equal to `code_challenge` via
      `hmac.compare_digest`; `code_verifier` is 43–128 chars of `[A-Za-z0-9._~-]`. Any check
      failing after the claim still leaves the code consumed. Success → 200 `{access_token,
      token_type: "Bearer", expires_in, refresh_token, scope}`.
      **Reuse:** a code that was already consumed more than `OAUTH_REUSE_GRACE_SECONDS` (10)
      ago revokes the whole grant (OAuth 2.1 §4.1.2). Inside the grace window (a client
      timeout-and-retry — Claude.ai retries after 10 s) → `invalid_grant` without revoking.
    - `grant_type=refresh_token` + `refresh_token` + `client_id`: claimed atomically the same
      way (`UPDATE oauth_tokens SET replaced_by = 0 … WHERE token_hash = ? AND kind='refresh'
      AND replaced_by IS NULL AND revoked_at IS NULL RETURNING …`, then set the real
      `replaced_by` id). Must be unexpired and belong to a live grant of that client. Success →
      a **new** access + refresh pair; the old access tokens of that grant are revoked.
      **Expiry is absolute, not sliding:** every refresh token in a grant expires at
      `grant.created_at + OAUTH_REFRESH_TOKEN_TTL_SECONDS`; when that passes the client gets
      `invalid_grant` and the user consents again. A replaced refresh token presented again
      after the grace window revokes the whole grant → `invalid_grant`.
    - Errors (RFC 6749 §5.2): 400 `{error, error_description}` with `invalid_request`,
      `invalid_grant`, `unsupported_grant_type`, `invalid_target`; 401 `invalid_client` for an
      unknown `client_id`.
    - Token format: access `j360a_` + `secrets.token_urlsafe(32)` (43 chars), refresh `j360r_`
      + 43. Lifetimes `OAUTH_ACCESS_TOKEN_TTL_SECONDS` = 3600,
      `OAUTH_REFRESH_TOKEN_TTL_SECONDS` = 2 592 000 (30 days). Only SHA-256 hashes are stored.
      Every access token row carries `audience` = the code's `resource` (S13).

R6. **Bearer path.** `Authorization: Bearer j360a_…` authenticates `require_user`,
    `optional_user`, `require_verified_user` and the MCP mount exactly like `j360_…`:
    live grant, live token, unexpired → `CurrentUser(auth_via="oauth", audience=…)`. Anything
    else → 401 `invalid or revoked token`. A refresh token (`j360r_`) or any other prefix on the
    bearer line → 401. **Throttle:** OAuth bearer failures use their own key
    `oauth_bearer_fail:{ip}` with `OAUTH_BEARER_FAIL_MAX_PER_MIN` (30) — never the
    `api_token_fail` bucket; only a hash that matches **no** row counts as a failure. An
    expired or revoked token returns 401 without touching the counter (expiry is the normal
    hourly state of every connected client, not an attack). `oauth_grants.last_used_at` is
    written at most once per 5 min per grant (same rule as `api_tokens`).
    `CurrentUser.auth_via` gains the literal `"oauth"`; `require_session_user` therefore keeps
    refusing it (403 `session_required`).

R7. **MCP 401 challenge.** Every 401 from `/api/mcp` (no bearer, bad bearer, expired, wrong
    audience) carries `WWW-Authenticate: Bearer realm="job360",
    resource_metadata="{SITE}/.well-known/oauth-protected-resource/api/mcp", scope="job360"`.
    Stamped in `_mcp_asgi` (`mcp_server.py` ~460-466) only — **not** by editing
    `auth_deps._BEARER_CHALLENGE`, which every `/api/*` route shares. The 429 keeps `Bearer`.
    `/api/mcp` additionally requires `audience == canonical MCP URL` for OAuth users (S13).
    Gate parity (`test_mcp_gate_parity.py`) is untouched.

R8. **Revocation.** `POST /api/oauth/revoke` (form: `token`, optional `token_type_hint`,
    `client_id`), RFC 7009: revokes the **grant** the token belongs to (access or refresh —
    either kills everything under that grant; a deliberate deviation from per-token
    revocation, stated in the discovery `service_documentation` page) when the token is the
    client's; **always 200** with an empty body, even for unknown tokens.
    `Access-Control-Allow-Origin: *`. Web side (session only): `GET /api/oauth/grants` →
    `{grants: [{id, client_name, redirect_uri, scope, created_at, last_used_at}]}` (active
    only; `redirect_uri` is the client's **first registered** URI — a grant has no
    redirect column of its own, and the code row that carried it is pruned after a day);
    `DELETE /api/oauth/grants/{id}` → 204 (404 if not the user's or already revoked).
    Revoking sets `oauth_grants.revoked_at`; every access token under it fails on the very
    next request (R6 checks the grant row live, no cache).

R9. **Frontend + login bounce.** `/oauth/consent/[rid]` is a protected path: the Next
    middleware bounces a logged-out user to `/login?next=/oauth/consent/<rid>` (the rid is in
    the **path** because `middleware.ts:31` keeps only the pathname). **The magic-link path
    must carry `next` through the email:** `POST /api/auth/magic-link/request` gains optional
    `next` (validated server-side: starts with `/`, not `//`, no scheme, ≤ 512 chars, else
    ignored); the emailed link becomes `{origin}/auth/magic?token=…&next=…`; `/auth/magic`
    redirects to `safeNext(next)` after consume (`/dashboard` when absent). The login page
    passes its `next` to `requestMagicLink`. Today `auth/magic/page.tsx:40` hardcodes
    `/dashboard` — that is the dead-end this fixes.
    The consent page shows: the client name (untrusted text, with the line "This app
    registered itself with Job360 — its name is not verified"), the **full** redirect URI
    ("You will be sent back to: `https://chatgpt.com/connector/oauth/abc…`"), the signed-in
    email ("Signed in as …"), the plain-words scope description, Allow / Deny. It re-fetches
    the request on window focus so an expired rid shows "This request has expired. Go back to
    the app and try again." instead of a live Allow button. On decision →
    `window.location.assign(redirect_to)`. `/settings/connect` gains a **Connected apps**
    section (name, redirect host, connected date, last used, Revoke with an inline confirm)
    above the personal tokens. `api.ts` gets `getConsentRequest`, `decideConsent`,
    `listGrants`, `revokeGrant`; `requestMagicLink(email, next?)`.

R10. **Data + housekeeping.** `oauth_grants` joins `_PER_USER_TABLES` (erased with the account;
    codes/tokens cascade via FK) and `_EXPORT_TABLES`, and `observe.py PER_USER_TABLES`.
    **Pruning** (Postgres has no `DELETE … LIMIT`; `pg.py translate()` has no rule for it):
    `DELETE FROM t WHERE ctid IN (SELECT ctid FROM t WHERE <cond> LIMIT 100)`. Runs in its own
    transaction **after** the response has been computed, sampled 1-in-`OAUTH_PRUNE_SAMPLE`
    (20) calls, failures logged and never surfaced — so a slow prune can never delay or fail a
    token exchange (Claude.ai's 10 s budget). Conditions: clients with **no `oauth_grants` row
    at all (revoked included)** and `created_at` older than `OAUTH_CLIENT_PRUNE_DAYS` (7);
    expired/consumed requests, codes and tokens older than 1 day.
    Audit-log events (`get_audit_logger`, never with a secret, never with `client_name`):
    `oauth_client_registered` (client_id, redirect_hosts), `oauth_grant_created` (user_id,
    client_id, grant_id), `oauth_grant_revoked` (grant_id, by: user|client|reuse),
    `oauth_token_issued` (grant_id, grant_type), `oauth_token_refused` (client_id, reason).

## Design
- **Tables** (migration `0036_oauth`):
  `oauth_clients(id TEXT PK, client_name, redirect_uris TEXT/JSON, token_endpoint_auth_method,
  created_at, last_used_at)`;
  `oauth_authorization_requests(id TEXT PK, client_id FK→oauth_clients CASCADE, redirect_uri,
  scope, state, code_challenge, code_challenge_method, resource NOT NULL, created_at,
  expires_at, consumed_at)`;
  `oauth_grants(id INTEGER PK AUTOINCREMENT, user_id FK→users CASCADE, client_id FK→oauth_clients
  CASCADE, scope, created_at, last_used_at, revoked_at)` + index `(user_id, revoked_at)` +
  partial unique index `(user_id, client_id) WHERE revoked_at IS NULL`;
  `oauth_authorization_codes(code_hash TEXT PK, grant_id FK CASCADE, client_id, redirect_uri,
  code_challenge, resource NOT NULL, scope, created_at, expires_at, used_at)`;
  `oauth_tokens(id INTEGER PK AUTOINCREMENT, grant_id FK CASCADE, kind 'access'|'refresh',
  token_hash TEXT UNIQUE, audience TEXT NOT NULL, created_at, expires_at, revoked_at,
  replaced_by INTEGER)` + index `(grant_id, kind)`. Same conventions as 0035 (TEXT timestamps,
  `IF NOT EXISTS`, down drops in FK order). The partial unique index is written in Postgres
  syntax (`CREATE UNIQUE INDEX … WHERE revoked_at IS NULL`) — SQLite accepts it too.
- **Modules**: `src/services/auth/oauth_clients.py` (register, load, allow-list, prune),
  `src/services/auth/oauth_flow.py` (requests, consent decision, codes, PKCE, token issue /
  refresh / revoke, `resolve_access_token`), `src/api/routes/oauth.py` (R2–R5, R8 web
  routes), `src/api/routes/well_known.py` (R1). Pure helpers (PKCE check, redirect
  normalisation/allow-list match, token format, `safe_next`) are module-level functions so
  tests hit them without a DB.
- **Bearer dispatch** in `auth_deps._current_user_from_bearer`: prefix `j360a_` →
  `oauth_flow.resolve_access_token` (own throttle key, R6); `j360_` → `api_tokens.resolve`
  (unchanged; `"j360a_".startswith("j360_")` is False so nothing overlaps); anything else →
  the existing 401 + throttle. `CurrentUser` gains `audience: str | None = None`.
- **Issuer/canonical URLs** come from `settings.SITE_BASE_URL` only. The canonical resource is
  `f"{SITE_BASE_URL}/api/mcp"`; `resource` comparison strips one trailing slash and lower-cases
  scheme+host, nothing else.
- **CSRF**: `/api/oauth/register`, `/token`, `/revoke` use no cookie, so `OriginCheckMiddleware`
  exempts exactly those three paths and they answer `Access-Control-Allow-Origin: *`.
  `/authorize/{rid}` + `/decision` and `/grants` use the cookie and stay Origin-checked and
  CORS-restricted. `/api/oauth/authorize` (GET) is safe by method.
- **Magic-link `next`**: `MagicLinkRequest.next: str | None`; `magic_link.request_magic_link`
  takes `next_path: str | None` and appends `&next=` (url-quoted) to the emailed link;
  `safe_next()` lives in `src/services/auth/magic_link.py` and mirrors the frontend's
  `safeNext`.
- **Frontend**: `next.config.ts` three well-known rewrites; `middleware.ts` `PROTECTED_PATHS`
  gains `/oauth`. Consent page is a server `page.tsx` (awaits `params`) with a client child
  component that fetches R4 and posts the decision.
- No new dependency: `hashlib`, `hmac`, `secrets`, `base64`, `urllib.parse`, `unicodedata`
  cover PKCE, hashing, redirects and name sanitising. The `mcp` SDK's `AuthSettings` stays
  unused.

## Security guardrails (mandatory section)
S1. **PKCE S256 only.** `plain` and a missing challenge are refused at `/authorize`;
    `code_challenge` must be exactly 43 base64url chars; the verifier is checked with
    `hmac.compare_digest`. No client secret exists, so PKCE + exact redirect match is the whole
    binding between the browser leg and the token leg.
S2. **Exact redirect match** at `/authorize` and again at `/token` (the code stores the URI it
    was issued for). No prefix, wildcard, or path-only comparison.
S3. **Redirect allow-list at registration — host-anchored, never a string prefix.**
    `OAUTH_REDIRECT_ALLOWLIST` is a comma-separated list of full URLs (default
    `https://claude.ai/api/mcp/auth_callback,https://chatgpt.com/connector_platform_oauth_redirect,
    https://chatgpt.com/connector/oauth/`). Both the entry and the candidate are **parsed**;
    a candidate passes an entry when `scheme`, `hostname` (lower-cased) and effective port are
    equal **and** the candidate path is either byte-equal to the entry path, or the entry path
    ends in `/` and the candidate path starts with it. An entry with an empty path is refused
    at load time (logged, ignored) — `https://grok.x.ai` can never match `grok.x.ai.evil.com`.
    Before comparing or storing, every candidate is checked: `https` (unless loopback), no
    fragment, no userinfo, ≤ 2 048 chars, no `.` or `..` path segment, no `%2e`/`%2E`, no
    backslash, no `//` run in the path (defeats `/connector/oauth/../../x`). Normalisation:
    lower-case scheme + host, drop a default port, keep path/query byte-for-byte.
    **Loopback** (RFC 8252) is decided on the parsed hostname only: exactly `127.0.0.1`,
    `::1` or `localhost` (case-insensitive, no trailing dot), scheme `http`, any port —
    `localhost.evil.com` is not loopback. `OAUTH_ALLOW_LOOPBACK_REDIRECTS` **defaults to 0**;
    nothing in production needs it (Claude Code keeps a personal token instead).
    Anything else → `invalid_redirect_uri`. Adding a client = editing the env var, no deploy.
S4. **Codes and tokens are opaque 256-bit randoms stored as SHA-256** (`secrets.token_urlsafe(32)`;
    lookup by hash, `UNIQUE`-indexed; no timing channel on a random value). The plaintext
    appears in exactly one response and never in a log, an audit event, an export, or a list.
    Codes live 60 s and are single-use; consumption is one atomic `UPDATE … RETURNING` so two
    concurrent exchanges cannot both win.
S5. **Revocation is immediate and total.** `resolve_access_token` joins
    `oauth_tokens → oauth_grants → users` and requires `t.revoked_at IS NULL AND t.expires_at >
    now AND g.revoked_at IS NULL AND u.deleted_at IS NULL`. Code reuse (after grace), refresh
    reuse (after grace), user revoke, client `/revoke`, account deletion (FK cascade +
    `_PER_USER_TABLES`) all end every token under the grant. A refresh that commits after a
    revoke still yields a dead token — the grant row is read on every request.
S6. **Rate limits** (existing `rate_limit` helper, keys by client IP via `_client_ip`; `0`
    disables a limit):
    - `/register`: `OAUTH_REGISTER_MAX_PER_HOUR` (60) per IP **and**
      `OAUTH_REGISTER_MAX_PER_HOUR_GLOBAL` (600) across all IPs → 429. Claude.ai re-registers
      per fresh connection, so the global budget is sized well above real traffic.
    - `/authorize`: `OAUTH_AUTHORIZE_MAX_PER_MIN` (60) per IP → 429.
    - `/token` failures: counted on `oauth_token_fail:{ip}` **unconditionally** (a missing or
      unknown `client_id` counts here too — it cannot be dodged by varying `client_id`) with
      `OAUTH_TOKEN_FAIL_MAX_PER_MIN` (30), plus the narrower `oauth_token_fail:{ip}:{client_id}`
      at the same cap. Resolved-first: a valid exchange is never refused by the counter.
    - Bearer failures: `oauth_bearer_fail:{ip}`, R6.
    **Deploy note:** behind the Next.js rewrite every agent shares the proxy IP unless
    `JOB360_TRUST_PROXY=1` is set on the backend service; without it every per-IP limit
    degrades to a global one. The PR checklist asks the owner to confirm the variable exists.
S7. **Bounded storage.** Each client ≤ 10 redirect URIs; `client_name` ≤ 100 chars; `state` ≤
    512; `scope` ≤ 200; `resource` ≤ 2 048; request body for `/register` ≤ 16 KB. Ceiling
    `OAUTH_MAX_CLIENTS` (10 000): when the count is at or above it, `/register` first prunes
    the oldest grant-less clients (any age, ≤ 100) to make room and only answers 503
    `temporarily_unavailable` if that freed nothing — a full table never locks real clients
    out for a week. The COUNT→INSERT race may overshoot the ceiling by the number of
    concurrent registrations; accepted (the ceiling is a brake, not an invariant).
S8. **No open redirect.** `/authorize` redirects only to a URI that matched a registered client's
    list and only after the client is verified (R3 normative order); `/decision` returns the
    URL in JSON and the browser navigates — the backend never 303s to a client-supplied URL
    from a cookie-authenticated POST.
S9. **Consent is per grant, from the session, and shows where the code goes.** The user is the
    cookie's user; `rid` is a 256-bit random that expires in 30 min and is consumed once. The
    allow-listed hosts are multi-tenant (`chatgpt.com/connector/oauth/{id}` is per connector),
    so a look-alike `client_name` ("Claude") plus a host is not enough: the page shows the
    **full registered redirect URI**, the signed-in email, and says the name is unverified.
    Clickjacking is already blocked (`frame-ancestors 'none'` + `X-Frame-Options: DENY` on
    `/:path*`, `frontend/src/lib/security-headers.ts`); CSRF on the decision is blocked twice
    (SameSite=Lax cookie + `OriginCheckMiddleware`).
S10. **Scope discipline.** Only `job360` is accepted; `offline_access` is deliberately not in
    `scopes_supported`. An access token has exactly the powers of a personal token — never the
    session's credential-management routes (`require_session_user` refuses
    `auth_via != "session"`).
S11. **Headers.** Token/authorize responses `Cache-Control: no-store`; discovery documents are
    cacheable and secret-free. The MCP 401 challenge contains only public URLs.
S12. **Logs.** Audit events carry ids and reasons, never a code, token, verifier, challenge or
     `client_name` (attacker text). The access log already redacts `Authorization`.
S13. **Audience (RFC 8707) is enforced where the MCP spec says MUST.** Every access token
     carries `audience` (the code's `resource`, which defaults to the canonical MCP URL).
     `/api/mcp` rejects an OAuth token whose audience is not the canonical MCP URL (401 with
     the R7 challenge). Elsewhere under `/api/*` an OAuth token is a user credential like a
     personal token and the audience is not checked — a **deliberate, stated deviation**
     (intent constraint 3, "same routes, same rules"). Today only one audience is ever
     issued, so the check is future-proofing, not a gate that can fail a real client.

## Review log (Opus adversarial pass, 2026-09-03)
Important, all folded in: host-anchored allow-list (S3); dot-segment traversal (S3); loopback
hostname + default off (S3); consent shows full redirect URI + unverified-name line (R4/R9/S9);
magic-link drops `next` (R9); atomic claim + reuse grace (R5/S4); separate OAuth bearer
throttle, expiry not a failure (R6); `/token` throttle keyed on IP unconditionally (S6);
registration cap self-DoS → prune at ceiling + global budget (S6/S7); audience column +
enforcement at `/api/mcp` (S13); prune SQL/hot path (R10).
Nits folded in: normative validation order; `urlencode`; `state` > 512 → 400; `scope` in
challenge; stamp in `_mcp_asgi` only; `Content-Type` handled by hand; absolute refresh
expiry; `token_urlsafe(24)` client ids; partial unique index; prune "no grants row at all";
COUNT race accepted; R8 deviation stated; exact root rewrite; CORS `*` on the cookie-less
endpoints; `SITE_BASE_URL` startup warning; `code_challenge` format; `client_name` out of
audit. Confirmed sound and unchanged: prefix non-collision, clickjacking headers, decision
CSRF, `rid` entropy, userinfo, refresh-vs-revoke race, PKCE charset, deletion cascade, no
open redirect from the decision POST, privilege containment.

## Frozen tests (`backend/tests/test_oauth_server.py`, added red first; frontend Playwright)
Metadata shapes + CORS header; DCR happy path; redirect outside allow-list → 400
`invalid_redirect_uri` (cases: `grok.x.ai.evil.com` against a host-only entry, dot-segment
traversal, `%2e`, `localhost.evil.com`, `http` non-loopback, userinfo, fragment); loopback
refused by default, accepted with the flag; non-`none` auth method rejected; authorize unknown
client / bad redirect / long state → 400 without redirect; missing PKCE → redirect with error;
happy → 302 consent; consent GET returns full redirect_uri + user_email; decision approve →
code; token exchange happy; JSON body → 400 `invalid_request`; wrong verifier →
`invalid_grant`; code reuse inside grace → `invalid_grant` and grant still alive; code reuse
after grace (time monkeypatched) → grant revoked and prior access token 401; refresh rotation;
rotated refresh reuse after grace revokes grant; refresh expiry is absolute; access token works
on `/api/auth/me` and MCP `tools/list`; wrong audience → 401 at `/api/mcp` only; expired access
→ 401 and does not count toward the throttle; unknown `j360a_` counts, N+1 → 429 on its own
key while a `j360_` guess still uses the old key; DELETE grant → 401 immediately; `/revoke`
with an access token kills the refresh token too and always 200; personal `j360_` still works;
`j360r_` bearer → 401; access token cannot call `/api/tokens` or `/api/oauth/grants` (403
`session_required`); MCP 401 carries `resource_metadata` and `scope`; token endpoint with a
foreign Origin still 200; `/register` global budget → 429; ceiling prune frees room; magic-link
request with `next` puts it in the emailed link and refuses `//evil`; migration 0036
up/down/up; gate-parity test still green; pure helpers (allow-list match, PKCE, `safe_next`)
table-driven. Playwright: consent page Allow/Deny + expired copy + connected-apps revoke;
`/auth/magic` honours `next`.

## Flagged concerns
C1. **CIMD** (client ID metadata documents) is what both clients prefer; without it Claude.ai
    registers a fresh client per connection. S7 keeps that bounded. Follow-up issue.
C2. **`resource` mismatch** is the silent-failure people report most: the PRM `resource` must be
    byte-equal to what the user types into the client (`https://job360.uk/api/mcp`, no slash).
    The verifier checks the document text, not just the status.
C3. **`SITE_BASE_URL` must be right in prod** (default `https://job360.uk`). A wrong issuer makes
    every client refuse the metadata. Local dev: set it to the frontend origin (R1 warns).
C4. **Windows full suite is flaky** — targeted gate locally; Linux CI is the verdict.
C5. **Claude.ai gives the token endpoint 10 s.** All `/token` work is one transaction of indexed
    lookups; no external calls; pruning is sampled and after the response.
C6. **`JOB360_TRUST_PROXY`** must be set on the Railway backend for any per-IP limit to mean
    per-client (S6). Owner confirms the variable name exists — never its value.
