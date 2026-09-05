# Mission roadmap — what must be done, in order
<!-- doc: LIVING | written 2026-09-03 from docs/product/VISION.md; one GitHub issue per slice -->

The mission is in [`docs/product/VISION.md`](../product/VISION.md). This file is the
work list that follows from it. Each slice is one GitHub issue, one branch, one PR,
built with the SDLC playbook (intent → spec → plan → frozen tests → code → gate →
verifier → REVIEW passes → owner merges → prod check). The owner says "go" per slice.

Model economy per slice: Opus designs anything with security or schema in it;
Sonnet builds from a written spec; Fable reviews every diff. Nobody self-certifies.

| # | Slice | Depends on | Done when | Issue |
|---|---|---|---|---|
| 0 | **Mission sweep** — every doc, skill, agent and README a session reads says the new mission; sourcing-era issues closed; sourcing-only scheduled workflows stopped | — | a fresh session reading CLAUDE.md → VISION.md → STATUS.md cannot find a "build search" instruction anywhere | this PR |
| 1 | **OAuth 2.1 authorization server** for MCP clients — authorization code + PKCE, short-lived access tokens, refresh, revocation, discovery metadata at `/.well-known/oauth-authorization-server`, consent screen on the web; personal tokens stay as CLI fallback | 0 | ChatGPT "custom connector" and Claude.ai "add integration" both connect to `https://job360.uk/api/mcp` via the browser login, and a revoked grant stops working within the access-token lifetime | #479 |
| 2 | **The spine** — one `Application` object born at `bring_job` (status `considering`), typed append-only event log with `recorded_by`, versioned artifacts (`cv` / `cover_letter` / `answers` / `outreach`), fit verdict stored not computed; tools `get_application` / `list_applications` / `save_artifact` / `save_fit` / `record_event` / `record_application` / `whats_new` / `export_history`; migrations fold `applications` + `application_receipts` + `application_stage_history` + `tailored_documents` in without losing a row; web home = your applications; old search UI behind `SEARCH_UI_ENABLED=false`; batch scorer / judge / enrichment crons off | 0 | the owner brings a job from Claude Code, saves two CV versions, records "applied" with the second, later records "replied" and "interview_requested" — and `whats_new` + the web home show exactly that, with every version still readable | #480 |
| 3 | **URL fetch on the web** — paste a link OR the text; fetch + readability extraction; SSRF guard (deny private/link-local/metadata ranges, no redirects to them, size + time caps); paste is the fallback when a site blocks us | 2 | a LinkedIn/Indeed/Workday/company-site link either fills the form or falls back to paste with a clear message; the SSRF drill in `scripts/drill_registry.py` is declared and red-able | #481 |
| 4 | **Contacts, outreach, stats, profile edits** — `add_contact`, `outreach` artifact kind, `stats` (reply / interview rate per CV version, per role), `update_profile` | 2 | the agent adds a recruiter and an outreach message to an application; `stats` returns counts that match a hand count of the event log; a profile field edited by the agent shows on the web | #482 — draft PR #498 |
| 5 | **Delete the sourcing era** — search routes, `SOURCE_REGISTRY` + 41 sources, batch scorer / judge / enrichment, feed tables no longer read, the sourcing-only workflows and their drills; `add-source` skill and pillars 02/03 moved to `docs/_archive/` | 2 live for one release | `grep -r SOURCE_REGISTRY` finds nothing; test count drops and CI is green; ARCHITECTURE.md describes only what remains | #483 |
| 6 | **Later, on evidence only** — WhatsApp (needs worker + Redis back), multiple named profiles, our own Gmail watcher, recruiter side (consent-first) | the owner using it daily | not scheduled | — |

## Not on this list, on purpose

Job search, feeds, ranking, recommendations, auto-submit, browser automation,
people-database integrations, our own Gmail OAuth, push notifications, pricing,
mobile apps, Chrome extension. VISION.md "we never build" column — do not open
issues for them.

## How a session uses this

1. Read VISION.md, then this file, then the issue for the slice the owner named.
2. Write `docs/plans/<date>-<slice>/{intent,spec,plan}.md` before code.
3. Security guardrails are a mandatory spec section for anything that adds an
   input, a token, a fetch or a migration.
4. Owner merges. Never merge, never push to main.
