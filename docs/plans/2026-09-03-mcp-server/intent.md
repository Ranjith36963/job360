# Intent: Job360 as a tool any agent can call (MCP)
Author: Ranjith (owner), captured from chat 2026-09-03 by Claude ("I suggest just you build mcp").
Status: draft — owner approves by merging.

## Problem
Slice one made the after-the-click record real: bring a job, score it, keep the receipt. But it lives
behind a dashboard, and the pivot says the dashboard is the record, not the product. The owner already
runs his job hunt inside Claude Code / Claude. "Paste this ad into Job360, tell me the fit, and when I say
applied, keep the receipt" should be one sentence to an agent — not a tab switch.

## Proposed outcome
Job360 exposes its existing routes as an MCP server at `https://job360.uk/api/mcp`. Any MCP client
(Claude Code today; Claude Desktop, Cursor, ChatGPT later) can: read the user's profile, bring a job,
read the fit, tailor the documents, record "I applied", and read receipts. The web app gets a
**Settings → Connect** tab where the user mints a personal token and copies the one-line
`claude mcp add …` command.

Same routes, same rules, same data — an agent doing it for you is the whole point of "career-ops".

## Affected users and systems
- Job seekers with an agent (owner dogfoods first, in Claude Code).
- Backend: new `api_tokens` table (migration 0035); bearer-token path in `require_user`;
  `POST/GET/DELETE /api/tokens`; MCP endpoint mounted at `/api/mcp` (reaches the internet through the
  existing Next rewrite, no infra change).
- Frontend: `/settings/connect` tab.

## Constraints (owner's words + pivot rules, made rules)
1. Never source or recommend jobs. No tool lists jobs the user did not bring. (`list_receipts` lists
   applications, not jobs.)
2. No auto-submit, ever. `record_application` records a fact the human states; it sends nothing.
3. Same API for every surface. Tools call the route functions in-process — zero duplicated logic.
   If a route changes, the tool changes with it.
4. A token is a credential, not a session: shown once, stored hashed, revocable, cannot mint or
   revoke tokens (session only), cannot change password/email/delete the account (those already
   demand the current password — hard rule #26).
5. Free. No quota beyond the ones the routes already enforce (tailor: monthly cap).
6. Anything that varies is a parameter: allowed hosts, token cap per user, failed-token rate limit.

## Open questions
- OAuth for claude.ai / ChatGPT connectors: deferred. Those clients cannot send a static header; they
  need an OAuth authorisation server. Day one is bearer tokens (Claude Code, Cursor, scripts, the
  future WhatsApp adapter). The token table is the identity OAuth would issue against later.
- Scopes: one scope ("everything the user can do, except manage credentials"). Split read/write when
  a second client type needs it.
