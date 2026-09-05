<!-- doc: PLAN | status: ACTIVE | pr: — -->
# Intent: one Application object that remembers everything (the spine)
Author: Ranjith (owner), decisions 3, 6, 7, 10, 11, 13 and build-order step 2 of
`docs/product/VISION.md` (2026-09-03). Issue #480. Status: draft — owner approves by merging.

## Problem
The thing Job360 exists to be — *the memory of a job hunt* — is currently four half-objects
that do not know about each other:

| Table | Born | Holds | Loses |
|---|---|---|---|
| `applications` (0002 + 0014) | only at "I applied" | one `stage` string, free-text `notes` | everything before the click; a stage move overwrites the last one |
| `application_stage_history` (0014) | on `advance_application` | from→to stage | who moved it, why, anything that is not a stage |
| `application_receipts` (0034) | at "I applied" | frozen job + document text | which *version* of the CV; nothing after the click |
| `tailored_documents` (0023) | on tailor | ONE row per (user, job, kind) | every earlier draft — `upsert_tailored_doc` is DELETE+INSERT (`database.py:1011-1043`) |

So the four questions the product is *for* have no answer: **which CV version did I send,
what happened afterwards, who recorded it, what changed since Tuesday.** An agent that
brings a job today gets a `job_id` and a score — there is nowhere to put the fit verdict it
just wrote, the third draft it just produced, or the recruiter reply it just read in Gmail.

Two live facts make this urgent rather than tidy:
- **A brought job is deleted after 30 days.** `purge_old_jobs` (`backend/src/repositories/
  database.py:775-830`) keys on `last_seen_at` and has **no `source='user_brought'`
  exemption**; `applications` is not in `_PURGE_CASCADE_TABLES` (`database.py:35-40`), so the
  application survives pointing at a job row that no longer exists and `_get_application`'s
  LEFT JOIN returns a blank title. Hard rule #3 already flags this as slice 2's job.
- **Nothing that runs today writes history.** Prod, measured 2026-09-04 via
  `railway run -s Postgres`: `applications` 3, `application_stage_history` **0**,
  `application_receipts` **0**, `tailored_documents` 8, `jobs` 19 196, `users` 14.

## Proposed outcome
One **Application** object, born the moment a job is brought, that owns its own history.

The owner pastes a job into Claude Code. Job360 answers with an `application_id` and status
`considering`, having copied the ad onto the application so it can never go blank. The agent
writes a fit verdict — Job360 **stores** it, never computes it. The agent writes a CV; that
is artifact `cv` v1, kept forever with the model and profile version that made it. It writes
a better one: v2. **v1 is still readable.** The owner applies with v2 and the receipt names
that exact version. A week later the agent reads Gmail with its own connector and calls
`record_event("replied")`, then `record_event("interview_requested")`. Nothing is ever
overwritten and nothing is ever deleted; a mistake is corrected by a new event that points
at the old one.

`whats_new` answers "what changed since I last looked" — pull, not push (decision 11). The
web home stops being a search dashboard and becomes **your applications**. The old search UI
goes behind `SEARCH_UI_ENABLED=false` and the catalog crons go off, so the code stops
claiming to do a thing the product no longer does.

Same object over REST and MCP — eight tools: `get_application`, `list_applications`,
`save_artifact`, `save_fit`, `record_event`, `record_application`, `whats_new`,
`export_history`.

## Affected users and systems
- **The owner first** — the one measure is that he runs his own hunt through this.
- Backend: migration `0037_application_spine` (three new tables, new columns on
  `applications` and `application_receipts`, a lossless fold of the four legacy tables); a
  new `src/api/routes/applications.py`; `bring.py` and `receipts.py` write through to the
  spine; seven new MCP tools plus an enriched `record_application`; `purge_old_jobs` learns
  to spare a brought job; `src/workers/settings.py` cron list becomes conditional.
- Frontend: `/applications` and `/applications/[id]`; root `/` becomes the applications home
  for a signed-in user (and stops advertising "41 Sources"); `/dashboard` behind the flag.
- Nothing in production is running the crons anyway — `worker` and `Redis` were deleted from
  Railway 2026-09-02. Turning them off is about the code telling the truth, and about local
  and dev runs.

## Constraints (owner's words + VISION rules, made rules)
1. **We store, the agent thinks** (product rules 4–5, hard rules M1/M2). The fit verdict is
   a column we write what we are told into — never a number we compute. No ranking, no
   recommending, no scorer in the product path. New-feature test: if Claude Code could do it
   with its own tools, expose a **store** tool, not a **do** tool.
2. **Nothing rewrites history** (M3). The event log and the artifact table are append-only:
   no `UPDATE`, no `DELETE`, no PATCH/PUT/DELETE route. A wrong event is retired by a
   correcting event that names it, exactly like a ledger reversal.
3. **Not one row may be lost.** The fold copies; it never moves or deletes. Every legacy
   table is still there and still readable after the migration, and the down migration
   leaves every pre-migration row exactly where it was.
4. **Backwards compatible on purpose.** Agents in the wild already call `POST /jobs/bring`
   and `POST /receipts/{job_id}`. Both keep working and write through to the spine.
5. **Anything that varies is a parameter**: the event vocabulary, every size cap, the export
   bounds, the search-UI flag, the cron switch. No CHECK constraint on the event type — a
   new event type must never need a migration.
6. **Authorship is derived, never declared.** `recorded_by` comes from the credential that
   made the call (session, personal token, OAuth grant). A caller cannot claim to be someone
   else, because the field is not in the request body.
7. **Same routes, same rules** (M5). Every MCP tool calls the route function, and every gate
   the route declares is re-applied by hand in `mcp_server.py` — parity is enforced by
   `tests/test_mcp_gate_parity.py`, which turns red for any tool without a table row.

## Open questions (resolved here, so the build does not re-litigate them)
- **Two homes for one fact, for a while.** `applications.status` (spine) and
  `applications.stage` (legacy pipeline UI) both exist until slice 5. Resolved: `status` is
  the truth, `stage` is a written-through projection via one mapping table. Same for
  `tailored_documents`, which the web tailor keeps writing *and* now also mirrors into an
  artifact version.
- **One Application per (user, job), or one per attempt?** Resolved: one per (user, job) —
  the existing `UNIQUE(user_id, job_id)` stands. Re-applying months later is a second
  `applied` event and a second receipt under the same application, which is what
  `application_receipts` already allows by design (0034's header).
- **Contacts and `stats`** are slice 4 (#482), not here. `outreach` is a valid artifact kind
  from day one so slice 4 adds no schema.
- **URL fetch** is slice 3 (#481). `bring_job` still takes pasted text.
