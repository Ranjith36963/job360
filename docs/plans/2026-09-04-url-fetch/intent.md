<!-- doc: PLAN | status: ACTIVE | pr: — -->
# Intent: bring a job by link, on the web
Author: Ranjith (owner), decision 16 and build-order step 3 of `docs/product/VISION.md`
(2026-09-03). Issue #481. Slice 3, stacked on slice 2 (`feat/application-spine`).
Status: draft — owner approves by merging.

## Problem

The web bring form asks for four fields the user is already looking at on a page. Today
they must copy the title, copy the company, copy the location, and copy 3,000 characters
of ad text, one at a time, into four boxes. Decision 16 says that is only half the door:

> **16 | URL fetch | Both link and text must work on the web. Paste is the fallback.**

The reason it was paste-only is written down honestly in the code that shipped it —
`backend/src/api/routes/bring.py:7-10`:

> *"Why paste, not a URL fetch, on day one: LinkedIn/Indeed/Workday refuse bots, and
> fetching arbitrary user URLs is an SSRF surface that needs its own guard. A form that
> fails on the three biggest boards is worse than a form that asks for a paste."*

Both halves of that are still true. Neither is a reason to stay at paste-only forever:

- **The bot walls are a fallback problem, not a blocker.** A form that fills itself for
  Greenhouse, Lever, Ashby, Workday, SmartRecruiters, Workable and every company careers
  page, and says one clear sentence for LinkedIn, is strictly better than a form that
  never tries. Paste does not go away; it becomes the *second* thing the user does, and
  only sometimes.
- **"Needs its own guard" is a work item.** This slice is that guard.

The agent path does not need any of this. Claude Code and ChatGPT already have their own
fetch, read the page themselves, and call `bring_job` with the fields. **This slice is the
web fallback for a human at a browser** — which is exactly the shape VISION rule 5 asks
for, and the reason no MCP tool is added.

## Proposed outcome

One extra box at the top of `/bring`: **the link**. The user pastes
`https://boards.greenhouse.io/acme/jobs/4321`, presses *Fetch*, and the four fields below
fill in with the title, the company, the location and the ad text — marked as "we filled
these, check them". They edit anything wrong and press *Score this job*; the existing
bring flow runs unchanged from there.

When the site refuses us — and LinkedIn will — the form says so in one sentence
("LinkedIn blocks automated readers. Paste the ad text below instead."), keeps the link in
the link field, and puts the cursor in the paste box. The user is never left guessing
whether it worked.

Behind that one button sits the thing this slice is actually about: a **fetcher that
cannot be turned against us**. A URL the user supplies is a URL an attacker supplies. The
guard is a separate module with its own unit tests and its own drill in
`scripts/drill_registry.py`, because the harness LAW is that a guard nobody has watched go
red is decoration.

## Affected users and systems

- **The owner first**, on the web. His agent path is untouched.
- Backend: a new `POST /api/jobs/fetch-url` in `routes/bring.py`; a new
  `src/services/fetch/` package (`guard.py`, `fetcher.py`, `extract.py`); ~14 new
  parameters in `src/core/settings.py`; a new `scripts/ssrf_drill.py` wired into
  `ci.yml` and declared in `scripts/drill_registry.py`.
- Frontend: `frontend/src/app/bring/page.tsx` gains the link box, the fetch call, and one
  message per outcome.
- **No migration. No new table. No new MCP tool.** Nothing is stored by this route — it
  reads a page and hands the text back to the form. The catalog row is still only written
  by `POST /jobs/bring`, after a human has looked at what we extracted.
- No new Python dependency (see spec §Extraction).

## Constraints (owner's words + VISION rules, made rules)

1. **Both link and text must work** (decision 16). The link box never replaces the paste
   box; the paste box is never disabled while a fetch is running; a failed fetch always
   lands the user in the paste box with the link kept.
2. **Paste is the declared fallback, not an error state.** A site refusing us is a normal,
   expected outcome with its own sentence — never a red toast, never a stack trace, never
   "something went wrong".
3. **We store, the agent thinks** (VISION rules 4–5). No MCP `fetch_url` tool: an agent
   already has fetch, so exposing one would be a *do* tool where the rule demands a *store*
   tool. Pinned by a frozen test, not by a comment.
4. **The user always sees what we extracted before it is stored.** The fetch pre-fills a
   form; it never creates a job. Auto-submitting would mean a page no human read became a
   catalog row on a machine's say-so.
5. **The guard is the feature.** Every SSRF control is testable without DNS, without
   sockets and without the network, because the suite runs offline (rule #4) — which means
   the resolver and the clock are *injected*, not imported.
6. **Every cap is a parameter** (`src/core/settings.py`), never a literal in the fetcher:
   size, time, redirects, rate limits, content types, deny nets, user agent, and the
   feature switch itself.
7. **A guard declares its drill** (`scripts/drill_registry.py`). Undeclared guard, red
   build. And the drill must be able to make this guard go *red*, not merely run.
8. **We never pretend to be a browser.** The User-Agent names Job360 and says the fetch is
   on behalf of a signed-in user. Beating a bot wall by lying about who we are is not a
   product decision this repo gets to make quietly in a header constant.

## Open questions (resolved here, so the build does not re-litigate them)

- **Readability library or hand-rolled?** Resolved: **hand-rolled on the standard
  library**, and the primary extractor is not readability at all — it is
  `schema.org/JobPosting` JSON-LD, which is what job boards actually emit. Full reasoning
  in spec §Extraction.
- **Does the fetch create the job directly?** Resolved: **no.** It returns fields; the
  human presses the button. Constraint 4.
- **Where does the route live?** Resolved: in `routes/bring.py`, beside the form it serves
  — not a new module. It shares the bring caps and the bring story, and it keeps
  `ROUTE_MODULES` in `tests/test_route_auth_coverage.py` unchanged.
- **Do we respect robots.txt?** Resolved: **no fetch of robots.txt, and this is stated in
  the module docstring so nobody assumes otherwise.** This is a user-agent action on one
  URL the user is already reading, not crawling. If a crawler is ever built here, robots
  is its problem and this decision does not cover it.
- **Do we retry a blocked site with a browser User-Agent?** Resolved: **no** (constraint
  8). `blocked` → paste. That is what decision 16 already chose.
- **Is a fetched description different from a pasted one, downstream?** Resolved: **no.**
  It lands in the same `description` field, tag-stripped, under the same 40,000-char cap,
  rendered in the same text node. The prompt-injection exposure through the tailor is
  pre-existing and unchanged — this slice does not add it and does not claim to fix it.
- **Does the SSRF guard get its own module, or live in the fetcher?** Resolved: **its own
  module** (`src/services/fetch/guard.py`). The drill and the unit tests must be able to
  hit the decision function with no HTTP client anywhere in the picture; a guard tangled
  into the transport is a guard you can only test by making requests.
