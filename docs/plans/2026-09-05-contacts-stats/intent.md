<!-- doc: PLAN | status: ACTIVE | pr: — -->
# Intent: contacts, outreach, stats, profile edits
Author: Ranjith (owner), decisions 9, 13 and 14 and build-order step 4 of
`docs/product/VISION.md` (2026-09-03). Issue #482. Slice 4, on top of the spine (slice 2,
merged 2026-09-04 as `d910ffa` / fixed `1fba085`) — branch `feat/contacts-stats`.
Status: draft — owner approves by merging.

## Problem

The spine gave the agent one Application per job, a typed event log and versioned
artifacts. Three things the interview decided are still missing from it:

- **Decision 9 — outreach and people.** *"We store contacts + messages. Agent finds and
  writes."* The `outreach` artifact kind and the `contact_added` / `outreach_sent` event
  types already exist (`backend/src/core/settings.py:455-472`), but there is **no table
  and no tool for the person**. Today the agent can save the message it wrote to a
  recruiter and record that it sent it — and has nowhere to put the recruiter.
- **Decision 13 — the improvement loop.** *"Both — cheap counts on our side + full export
  for the agent."* `export_history` shipped with the spine; the cheap counts did not.
  The question the owner will ask after twenty applications — *"which CV version gets
  replies?"* — has no answer that does not mean pulling the whole export into the agent's
  context every time.
- **Decision 14 — the profile.** *"Keep our extraction, add `update_profile`."* The agent
  can read the profile (`get_profile`) but cannot fix it. When extraction misreads the
  location, or the seeker mentions a portfolio, a certification or a visa situation in
  chat, the agent has to tell the seeker to go to the web form. The web form only edits
  preferences; the extracted CV fields have no edit path at all.

None of these three is a "do" tool (VISION rule 5). Finding the recruiter, writing the
message, analysing the counts, deciding what the profile should say — the agent does all
of that with its own tools. We add the **store** for each.

## Proposed outcome

Four additions to the agent surface, each the same function on REST and MCP, each
`recorded_by`-stamped like everything else in the spine:

| Tool | What the agent can now do |
|---|---|
| `add_contact` | put a recruiter / hiring manager (name, role, email, LinkedIn, note) on an application; appends a `contact_added` event; shows on the application page |
| `save_artifact(kind="outreach")` + `record_event("outreach_sent")` | already exist — this slice makes them **first-class on the web**: the application page shows the contact next to the message that went to them |
| `stats` | cheap counts: applications → replied → interview → offer, as counts and rates, grouped by **CV version** (the artifact named on the receipt) and by **role** (the job title as brought). No charts, no ranking, nothing we invent — every number is a `COUNT` over the event log the agent already wrote |
| `update_profile` | set or clear a closed set of profile fields (name, headline, location, summary, skills, certifications, links/portfolio, right-to-work, languages, and every preference). An edit **wins over extraction until it is cleared**, is stamped who/when, and the web profile page shows it in place with an "edited by your agent" mark |

## Why an overlay for profile edits, not a write into the extracted data

Extraction owns `cv_data` and rewrites it wholesale on every CV/LinkedIn upload
(`profile.py:638-696`). Writing the agent's fix into that same JSON means the next upload
silently undoes it — the seeker fixed their location in chat on Monday and it is wrong
again on Friday with no trace. An append-only `profile_edits` table applied at
`load_profile` time means the fix persists across re-extraction, every change is kept
forever with its author (VISION: *"nothing is deleted"*), and clearing an edit is itself a
recorded edit. It also answers "why does the web say X when my CV says Y" in one query.

## Non-goals (on purpose)

- No contact **provider** (Apollo, LinkedIn lookup, email finder). Decision 9.
- No editing or deleting a contact in this slice — add only, idempotent on email. A wrong
  contact is superseded by adding the right one; the event log keeps both.
- No charts, trends, cohort analysis. `stats` is `COUNT(DISTINCT application_id)` grouped
  two ways; anything richer is the agent's job on top of `export_history`.
- No free-form profile field creation. The editable set is closed and is a parameter.
- No web form to add a contact or edit the profile fields the agent can edit. The web is
  the record (VISION); the preferences form it already has stays as it is.

## Done when (issue #482)

- The agent adds a recruiter and an outreach message to an application → both readable on
  `GET /applications/{id}` and on `/applications/{id}` in the browser.
- `stats` returns counts that match a hand count of the event log — a frozen test builds
  a known event history and asserts the exact numbers.
- A profile field edited by the agent shows on the web — the profile page renders the
  edited value in place plus the edit's author and time.
