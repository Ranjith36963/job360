<!-- doc: PLAN | status: ACTIVE | pr: — -->
# Intent: any agent can log in to Job360 with a browser (OAuth 2.1 for MCP clients)
Author: Ranjith (owner), decision 12 + build-order step 1 of `docs/product/VISION.md`
(2026-09-03). Issue #479. Status: draft — owner approves by merging.

## Problem
The MCP server at `https://job360.uk/api/mcp` works, but only for clients that can carry a
static header: Claude Code, Cursor, scripts. ChatGPT connectors and Claude.ai custom
integrations cannot. They discover an OAuth authorization server, send the user to a
login page in the browser, and come back with a token. Job360 has no such server, so the
two biggest agent surfaces cannot connect at all. That is the single blocker on "the
owner uses it daily through ChatGPT".

## Proposed outcome
Job360 becomes its own OAuth 2.1 authorization server. A user pastes
`https://job360.uk/api/mcp` into ChatGPT or Claude.ai, is sent to job360.uk, logs in with
the existing magic link, sees one consent screen ("Claude wants to read your profile,
bring jobs, tailor documents and record applications"), clicks Allow, and the agent is
connected. Tokens are short-lived and refreshed silently. The user can see and revoke
every connected app under Settings → Connect; a revoked app stops working at once.

Personal `j360_…` tokens keep working unchanged — the CLI fallback.

## Affected users and systems
- Job seekers who use ChatGPT, Claude.ai (web/desktop/mobile) or any spec-following
  MCP client. Owner dogfoods first.
- Backend: migration 0036 (five OAuth tables); routes under `/api/oauth/*`; two
  discovery documents under `/.well-known/*`; the bearer path in `auth_deps.py` learns a
  second token kind; the MCP 401 challenge points at the discovery document.
- Frontend: consent page `/oauth/consent/[rid]`; "Connected apps" list on
  `/settings/connect`; two `next.config.ts` rewrites so the `/.well-known/*` documents
  (which must live at the site root) reach the backend.

## Constraints (owner's words + pivot rules, made rules)
1. Standards, not inventions. What ChatGPT and Claude.ai need was read from their live
   docs on 2026-09-03 (`spec.md` §"What the clients need"), not remembered.
2. A grant is consent. It is shown to the user in plain words before it exists, listed
   where they can see it, and dies the moment they revoke it — not at token expiry.
3. Same routes, same rules. An OAuth access token is just another way to become the
   `CurrentUser`; every route and MCP tool behaves exactly as with a personal token.
   No token of any kind can manage credentials (mint/revoke tokens, revoke grants) —
   session only.
4. Anything that varies is a parameter: token lifetimes, redirect allow-list, rate
   limits, client cap. Never hard-code a client name.
5. Free, pull, consent-first. No scopes ladder yet — one scope, `job360`, meaning
   "everything the user can do except manage credentials" (intent of the MCP slice,
   unchanged). Split it when a second client type needs less.
6. Never log or store a secret in the clear. Codes and tokens are stored as SHA-256;
   the plaintext exists only in the response that issues it.

## Open questions
- Client ID Metadata Documents (CIMD): both clients now *prefer* it over dynamic
  registration. It needs the server to fetch a URL the client names (SSRF surface) and
  is not required for either client to connect. Deferred to a follow-up issue; DCR
  with a redirect allow-list is enough for "done".
- Claude Code OAuth (loopback redirect): allowed by the redirect rules (RFC 8252) but
  not in the "done when"; the owner keeps using a personal token there.
- Per-tool scopes: not until a client asks for less than everything.
