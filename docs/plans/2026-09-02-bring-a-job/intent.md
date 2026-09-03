# Intent: bring a job, keep the receipt
Author: Ranjith (owner), captured from chat 2026-09-02 by Claude. Status: draft — owner approves by merging.

## Problem
Job360 spent a year sourcing jobs. Nobody needed that — every board already does it. What nobody does well is
**everything after the click**: what did I send, to whom, when; what did they say back; what is next.
Tsenta (autojobs.me) does this but fires applications at volume, and its users complain about wrong
applications and lost credits.

## Proposed outcome
The user brings the job (link or pasted ad). We score it against their profile, tailor the CV, and when they say
"I applied" we freeze an **application receipt**: the ad as it read, the exact CV and cover letter sent, the date.
"What did I send Acme?" is answerable forever, even after the ad is gone or the CV was re-tailored.

This is slice one. Later slices push outcomes to the user (Gmail eyes, WhatsApp, MCP) instead of asking
them to open a dashboard. The dashboard is the record, not the product.

## Affected users and systems
- Job seekers (owner dogfoods first). Recruiters later, consent-first.
- Backend: new `POST /jobs/bring`, `POST/GET /receipts`; new table `application_receipts` (migration 0034).
- Frontend: `/bring` page, "I applied" on the job page, `/receipts` list + detail.
- Existing tables reused: `jobs` (shared catalog, rule #10), `user_feed`, `user_actions`, `applications`,
  `tailored_documents`.

## Constraints (owner's words, made rules)
1. Never source or recommend jobs. Matching runs only on a job the user brings.
2. No auto-submit, ever. No volume.
3. Global from day one — a Berlin or Tokyo ad is accepted. The UK gate stays on the search pipeline only.
4. A receipt is append-only. Nothing rewrites it. Two applications = two receipts.
5. Free for now. No credits, no per-application pricing.
6. Same API for every surface — web today, WhatsApp/MCP later call the same routes.

## Open questions
- URL fetch: deferred. LinkedIn/Indeed/Workday block bots and fetching user URLs is an SSRF surface.
  Day one is paste; the link is stored so the receipt can point back.
- Two users paste the same (company, title): they share one catalog row and the first paste's description
  wins. Acceptable for a handful of users; revisit when the `existing=True` rate is measurable.
