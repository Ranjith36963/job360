<!-- doc: PLAN | status: ACTIVE | pr: — -->
# Spec: the application spine
Skills applied: `hard-rules` (M1 never source/rank, M2 store-not-do,
M3 one door for history, M5 MCP gate parity, #3 purge, #10 no `user_id` on `jobs`,
#12/#25 every per-user route scopes by `user.id`, #16 lazy imports, #21 value-presence
tests). Status: shipped (PR #480). (The `intent.md` this spec read from is deleted
scaffolding, 2026-09-05 — git history holds it.)

## Measured starting point (2026-09-04, `railway run -s Postgres`)
`applications` 3 · `application_stage_history` 0 · `application_receipts` 0 ·
`tailored_documents` 8 · `jobs` 19 196 · `users` 14. The fold touches 11 real rows in
production; the migration is still written to be correct for any size.

## Requirements

R1. **An Application is born at `bring_job`.** `POST /jobs/bring` (`backend/src/api/routes/
    bring.py:79`) additionally, in one transaction: upserts the `applications` row for
    `(user.id, job_id)` with `status='considering'`, copies the ad onto it (R2 snapshot),
    and appends a `brought` event. Response gains `application_id: int` and `status: str`;
    `job`, `existing`, `scored` are unchanged (constraint 4). Bringing the same job twice
    returns the same `application_id` and appends **no second** `brought` event
    (`existing=true`), because the row already exists.

R2. **The job snapshot lives on the application.** `job_title`, `job_company`,
    `job_location`, `job_url`, `job_source`, `job_description_snapshot`, `snapshot_at` are
    copied at birth, never joined. Reason, verified: `purge_old_jobs`
    (`backend/src/repositories/database.py:775-830`) deletes any `jobs` row whose
    `COALESCE(last_seen_at, first_seen)` is older than 30 days, has **no**
    `source='user_brought'` exemption, and `applications` is not in
    `_PURGE_CASCADE_TABLES` (`database.py:35-40`) — so today the application silently goes
    blank. **Both fixes ship**: the snapshot (durable answer) *and*
    `purge_old_jobs` gains `AND source <> ?` bound to `settings.USER_BROUGHT_SOURCE`
    (`settings.py:169`) so the live catalog row also survives for the detail page. This is
    the hard-rule-#3 amendment the rule itself asks for.

R3. **The event log is the history, and it is append-only.** One door,
    `POST /applications/{id}/events`. Every event carries `event_type` (R7 enum), `detail`
    (free text), `payload` (JSON object), `occurred_at` (when it happened in the world —
    the agent supplies it; backdating is legitimate), `recorded_at` (when we stored it),
    `recorded_by` (S3, derived) and optional `corrects_event_id`. **No UPDATE and no DELETE
    exists anywhere**: no PATCH/PUT/DELETE route, and a grep guard over `backend/src/`
    (same shape as `tests/test_receipts.py::test_receipts_are_append_only:125`). A wrong
    event is retired by a new event whose `corrects_event_id` names it; reads mark the
    target `superseded: true` and the status recompute skips it.

R4. **`applications.status` is a cache of the last status event, and must be rebuildable.**
    A status-bearing event (R7) sets `status`, `last_event_at` and `updated_at` in the same
    transaction that inserts the event. Denormalised because `list_applications` and the
    web home sort and filter on it and must not read the whole log. A frozen test replays
    the events and asserts the same value, so the cache can never drift silently.
    Write-through: `applications.stage` (legacy, read by `/api/pipeline`) is set from the
    new status by one mapping dict in `src/services/applications/status.py`:
    `considering→applied` is **not** written (a considering row has no legacy stage; `stage`
    keeps its `applied` default and the pipeline board is unchanged until slice 5),
    `applied→applied`, `replied→applied`, `interview_requested|interview_scheduled|
    interview_done→interview`, `offer→offer`, `rejected→rejected`, `withdrawn→rejected`,
    `ghosted→ghosted`. The dict is the only place the two vocabularies meet.

R5. **Artifacts are versioned forever.** `POST /applications/{id}/artifacts` with `kind`
    (`cv` | `cover_letter` | `answers` | `outreach`), `text`, optional `label`, optional
    `model`. The server allocates `version_no = COALESCE(MAX(version_no), 0) + 1` per
    `(application_id, kind)` inside the insert transaction; `UNIQUE(application_id, kind,
    version_no)` turns a race into a retry, never a duplicate. `made_by` and
    `profile_version` are stamped by the server (S3), never taken from the body. An
    `artifact_saved` event is appended with `{artifact_id, kind, version_no}` in `payload`.
    Nothing ever updates or deletes an artifact row (same grep guard as R3).

R6. **The fit verdict is stored, never computed** (VISION rule 4). `PUT /applications/{id}/
    fit` with `score` (0–100, optional), `verdict` (short string), `gaps` (list of strings),
    `reasoning` (text). It overwrites the slot on `applications` **and** appends a
    `fit_judged` event carrying the whole verdict in `payload` — so the slot is "the current
    answer" and the log is the version history. No scorer, matcher, judge or enrichment call
    is made on this path; `save_fit` never reads `skill_matcher`.

R7. **Event vocabulary — closed set in code, extensible without a migration.**
    `src/core/settings.py`:
    ```
    APPLICATION_STATUS_EVENT_TYPES = ("brought", "applied", "replied", "interview_requested",
        "interview_scheduled", "interview_done", "offer", "rejected", "withdrawn", "ghosted")
    APPLICATION_NOTE_EVENT_TYPES  = ("fit_judged", "artifact_saved", "contact_added",
        "outreach_sent", "note", "lesson")
    APPLICATION_EXTRA_EVENT_TYPES = _env_list("APPLICATION_EXTRA_EVENT_TYPES", ())  # non-status only
    ```
    `brought` maps to status `considering`; every other status event's name **is** the
    status. Validation is **app-level, not a CHECK constraint** — a new event type must
    never need a migration (constraint 5), and an env-added type can only be a non-status
    one because a status type also needs an R4 mapping. Unknown type → 422 naming the
    allowed list. A frozen test asserts the two tuples equal the list in
    `docs/product/VISION.md:66-71` so doc and code cannot drift.

R8. **`record_application` is the rich receipt** (decision 10). `POST /applications/{id}/
    receipt` takes `channel`, `note`, `confirmation`, `answers` (list of `{question,
    answer}`), `fields_filled` (JSON object), `cv_artifact_id`, `cover_letter_artifact_id`,
    `applied_at`. Artifact ids default to the newest version of that kind; a supplied id
    must belong to this application and this user or the call is 404 (S4). The receipt
    still **copies the text** as well as naming the version — the frozen copy is 0034's
    whole contract and a version id is not a substitute. It appends an `applied` event.
    The legacy `POST /receipts/{job_id}` (`receipts.py:99`) keeps working unchanged from the
    caller's side and now writes through: resolves/creates the application, fills
    `application_id`, appends the `applied` event.

R9. **`whats_new`.** `GET /whats-new?since=<ISO8601>&after_id=<int>&limit=<n>` → 200
    `{now, since, events: [...], applications: [{id, job_title, job_company, status,
    last_event_at}], next_since, next_after_id, truncated}`. **Ordered and paged by
    `recorded_at`, not `occurred_at`** — `occurred_at` is agent-supplied and may be in the
    past, so a cursor on it would silently skip backdated events. `(recorded_at, id)` is the
    cursor pair because `recorded_at` collides. `since` defaults to
    `WHATS_NEW_DEFAULT_WINDOW_DAYS` (7) ago; `limit` capped at `WHATS_NEW_MAX_EVENTS` (200)
    with `truncated: true` when the cap bites. `applications` lists only those touched in
    the window.

R10. **`export_history`.** `GET /applications/export?since=&include_text=false` → the user's
     applications, their events, and artifact **metadata** (id, kind, version_no, made_by,
     model, profile_version, chars, created_at); artifact and receipt text only when
     `include_text=true`. Bounds are explicit, never silent: `EXPORT_HISTORY_MAX_APPLICATIONS`
     (500) and `EXPORT_HISTORY_MAX_BYTES` (8 MiB) — on either, the response stops at an
     application boundary and returns `truncated: true` with `next_since`. Rate-limited
     `EXPORT_HISTORY_MAX_PER_HOUR` (12) per **user** (not per IP — every agent shares the
     proxy IP behind the Next rewrite, the `JOB360_TRUST_PROXY` trap from the OAuth slice).

R11. **`get_application` / `list_applications`.**
     `GET /applications/{id}?with_artifact_text=false` → `{id, job_id, status, created_at,
     updated_at, last_event_at, job: {…snapshot…, catalog_present}, fit: {…} | null,
     artifacts: [{id, kind, version_no, made_by, model, profile_version, label, chars,
     created_at, text?}], events: [{id, event_type, detail, payload, occurred_at,
     recorded_at, recorded_by, corrects_event_id, superseded}], receipts: [{id, sent_at,
     channel, confirmation, cv_artifact_id, cover_letter_artifact_id, note}]}`.
     Artifact **text is off by default** — ten CV versions is ~80 KB and would blow an
     agent's context on every read. With `with_artifact_text=true` the texts are included
     newest-first up to `EXPORT_HISTORY_MAX_BYTES / 4`; versions past the cap come back with
     `text: null, truncated: true`. One version in full is always available at
     `GET /applications/{id}/artifacts/{artifact_id}` (this is how "every version still
     readable" is met without an unbounded default read).
     `GET /applications?status=&limit=&offset=&updated_since=` → `{applications: [summary],
     total}`, newest `last_event_at` first; summary is the snapshot fields, status, counts
     (`events`, `artifacts` per kind, `receipts`) — no bodies.

R12. **The old search UI goes behind a flag, off.** New `SEARCH_UI_ENABLED` (default
     **false**), both sides:
     - Backend `src/core/settings.py` `SEARCH_UI_ENABLED = _env_flag("SEARCH_UI_ENABLED",
       False)`. `POST /api/search` and `GET /api/search/{run_id}/status`
       (`routes/search.py:174,264`) answer **404** when off — a hidden UI in front of a live
       route is cosmetic, and slice 5 deletes a route nobody can reach. `backend/tests/
       conftest.py` sets `SEARCH_UI_ENABLED=1` for the suite (one `monkeypatch.setenv`
       beside the existing `LOOP_WATCHDOG_ENABLED` line at `conftest.py:148`) so the legacy
       search tests keep running; two new tests pin the gate in both positions.
     - Frontend `NEXT_PUBLIC_SEARCH_UI_ENABLED` (default false, read the same way as
       `NEXT_PUBLIC_API_URL` at `frontend/src/lib/api.ts:47`): `middleware.ts` 404s
       `/dashboard` and `/jobs` (the redirect page) when off, and `Navbar.tsx:22-29` drops
       the Dashboard link. **Trap to state in the PR:** `NEXT_PUBLIC_*` is inlined at build
       time, so flipping it needs a redeploy, not a restart.
     - Not a security control. The search routes keep `Depends(require_user)` exactly as
       today; the flag hides a feature, it does not protect anything.

R13. **Catalog crons off by parameter, not deletion.** `src/workers/settings.py:232,239` —
     `refresh_catalog` and `enrichment_sweep` are appended to `cron_jobs` only when
     `CATALOG_CRONS_ENABLED` (default **false**). `nightly_ghost_sweep` and
     `notification_tick` stay unconditional. Nothing runs any of this in production (the
     `worker` and `Redis` services were deleted 2026-09-02); the change is code
     truthfulness and local/dev behaviour.

R14. **The web home is your applications.** `frontend/src/app/page.tsx` renders the
     applications home for a signed-in user (session cookie present) and keeps the existing
     landing page for a signed-out visitor — `/` is deliberately **not** in
     `middleware.ts` `PROTECTED_PATHS:7-18` (unfurl bots and `landing-cta-auth.spec.ts`
     depend on the public landing) and must stay out. The landing copy loses "41 Sources"
     — advertising sourcing contradicts VISION rule 4. New `/applications` (list) and
     `/applications/[id]` (the record: status, timeline, every artifact version, receipts,
     fit verdict, the tailor button). `/pipeline` and `/receipts` keep working by URL but
     leave `NAV_LINKS`; slice 5 removes them.

R15. **Our tailoring stays, as a web fallback.** `POST /tailor/{job_id}/generate`
     (`routes/tailor.py:111`) is unchanged for the caller and additionally writes each
     generated document as an artifact version (`made_by="web:tailor"`, `model` from the
     generator, `profile_version` as today). `tailored_documents` keeps its DELETE+INSERT
     behaviour — that table is the editor's working copy; the artifact table is the memory.
     `PATCH /tailor/{job_id}/{doc_kind}` (save an edit) writes a further artifact version
     with `made_by="human"`.

## Data model (migration `0037_application_spine`)

Conventions copied from 0034/0036: TEXT ISO-8601 timestamps, `IF NOT EXISTS`,
`INTEGER PRIMARY KEY AUTOINCREMENT` (the shim rewrites it to `BIGSERIAL`,
`pg.py:193-195`), inline `REFERENCES users(id) ON DELETE CASCADE` written for documentation
value even though **the shim strips every FK clause** (`pg.py:217-226`) — meaning there is
no database-level cascade or integrity here, so every read filters on `user_id` by hand and
account deletion goes through `_PER_USER_TABLES`.

**`applications` — ALTER, 12 new columns** (existing `id, user_id, job_id, stage, notes,
created_at, updated_at, last_advanced_at, interview_dates, notes_history` and
`UNIQUE(user_id, job_id)` all stay):
```
status TEXT NOT NULL DEFAULT 'considering'
last_event_at TEXT
job_title TEXT NOT NULL DEFAULT ''      job_company TEXT NOT NULL DEFAULT ''
job_location TEXT NOT NULL DEFAULT ''   job_url TEXT NOT NULL DEFAULT ''
job_source TEXT NOT NULL DEFAULT ''     job_description_snapshot TEXT NOT NULL DEFAULT ''
snapshot_at TEXT
fit_score INTEGER   fit_verdict TEXT   fit_gaps TEXT DEFAULT '[]'   fit_reasoning TEXT
fit_recorded_by TEXT   fit_recorded_at TEXT
```
Indexes: `idx_applications_user_last_event ON applications(user_id, last_event_at DESC)`,
`idx_applications_user_status ON applications(user_id, status)`.

**`application_events` — NEW:**
```
id INTEGER PRIMARY KEY AUTOINCREMENT
user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE
application_id INTEGER NOT NULL
event_type TEXT NOT NULL          -- app-validated (R7), deliberately no CHECK
detail TEXT NOT NULL DEFAULT ''
payload TEXT NOT NULL DEFAULT '{}'   -- JSON object
occurred_at TEXT NOT NULL            -- world time (agent-supplied, may be backdated)
recorded_at TEXT NOT NULL            -- our time; the whats_new / export cursor
recorded_by TEXT NOT NULL            -- derived (S3)
corrects_event_id INTEGER
```
Indexes: `(user_id, recorded_at, id)` — the `whats_new` cursor; `(application_id,
occurred_at, id)` — the timeline; `(user_id, event_type)` — slice-4 `stats`.

**`application_artifacts` — NEW:**
```
id INTEGER PRIMARY KEY AUTOINCREMENT
user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE
application_id INTEGER NOT NULL
kind TEXT NOT NULL                -- cv | cover_letter | answers | outreach
version_no INTEGER NOT NULL
text TEXT NOT NULL
made_by TEXT NOT NULL             -- "agent:<client>" | "token:<name>" | "web:tailor" | "human"
model TEXT   profile_version INTEGER   label TEXT NOT NULL DEFAULT ''
chars INTEGER NOT NULL   created_at TEXT NOT NULL
UNIQUE(application_id, kind, version_no)
```
Index: `(user_id, application_id, kind, version_no DESC)`.

**Storage decision — TEXT in Postgres, not a file store.** A tailored CV measured on this
codebase is 2–8 KB; the cap is `APPLICATION_ARTIFACT_MAX_CHARS` (60 000) and
`APPLICATION_ARTIFACT_MAX_VERSIONS` (200) per kind, so the worst case is ~12 MB per
(application, kind) and typically under 100 KB. Postgres TOASTs and compresses text that
size out of line, and the existing `application_receipts.cv_text` already stores exactly
this kind of blob. A file store would add a second consistency domain (orphan blobs, backup
skew between DB and disk, a new secret) for no benefit at this size. Both numbers are
parameters; if a user ever hits them the answer is to raise the parameter, and the 422 says
which one bit.

**`application_receipts` — ALTER, 7 new columns** (0034's table, still append-only):
```
application_id INTEGER          cv_artifact_id INTEGER      cover_letter_artifact_id INTEGER
answers TEXT NOT NULL DEFAULT '[]'        fields_filled TEXT NOT NULL DEFAULT '{}'
confirmation TEXT NOT NULL DEFAULT ''     recorded_by TEXT NOT NULL DEFAULT 'web'
```
Index: `idx_receipts_application ON application_receipts(user_id, application_id)`.

**Registrations:** `application_events` and `application_artifacts` join
`JobDatabase._PER_USER_TABLES` (`database.py:1779`), `_EXPORT_TABLES` (`:1798`) and
`scripts/observe.py PER_USER_TABLES` (`:43`). Nothing goes in `_PURGE_CASCADE_TABLES` —
these belong to the user, not to the catalog.

## Migration fold — the no-row-loss argument

The fold **copies; it never moves and never deletes.** After `0037` every one of the four
legacy tables still holds exactly the rows it held before, and the routes that read them
(`/api/pipeline`, `/api/receipts`, `/api/tailor`) still work. That is the whole argument,
and it is checkable with counts.

Order inside one transaction (the runner wraps each migration body — `pg.py:676-686`):

1. **Add the columns and create the two tables.** Pure DDL, no row touched.
2. **No orphans.** Insert a missing `applications` row for every `(user_id, job_id)` that
   appears in `application_receipts`, `tailored_documents` or `application_stage_history`
   but not in `applications` — written as `INSERT INTO … SELECT DISTINCT … WHERE NOT EXISTS
   (…)` rather than `INSERT OR IGNORE`, so it needs no `translate()` rule and reads the same
   in either dialect. These rows get `status='applied'` (a receipt or a stage row means the
   user got at least that far) and `created_at` from the source row.
3. **Backfill `status` from `stage`** by CASE, using R4's mapping in reverse:
   `applied|outreach→applied`, `interview→interview_scheduled`, `offer→offer`,
   `rejected→rejected`, `ghosted→ghosted`, anything else → `applied`. `stage` is left
   exactly as it was.
4. **Backfill the job snapshot** from `jobs` by `LEFT JOIN` on `job_id` (blank strings where
   the catalog row is already gone — honest, and better than a NULL join at read time).
5. **`application_stage_history` → events.** One event per row:
   `event_type = CASE to_stage WHEN 'interview' THEN 'interview_scheduled' … END`,
   `occurred_at = transitioned_at`, `recorded_at = transitioned_at`,
   `recorded_by = 'migration:0014_history'`, `detail = COALESCE(notes, '')`,
   `payload = json of {from_stage, to_stage}`. Source rows untouched.
6. **`application_receipts` → `application_id` + one `applied` event each.** The
   `application_id` backfill is the migration's only `UPDATE` against a receipt row; it sets
   a column that did not exist a statement earlier, changes no user-visible field, and is
   outside the append-only guard's scope (that guard greps `backend/src/`, not
   `migrations/` — `tests/test_receipts.py:129-135`). Say so in the migration header, as
   0034 says it about its own down file. Events get `recorded_by='migration:0034_receipts'`,
   `occurred_at = sent_at`.
7. **`tailored_documents` → artifact versions.** For each row: `ai_draft` (when non-empty)
   becomes `version_no=1` with `made_by='migration:0023_tailored'`; `polished` (when NOT
   NULL) becomes `version_no=2` with `made_by='human'` — so a user who edited keeps **both**
   the draft and the edit, which is precisely what `upsert_tailored_doc`'s DELETE+INSERT
   (`database.py:1011-1043`) has been destroying. `model` and `profile_version` carry over.
   Source rows untouched.
8. **`last_event_at`** = MAX(`recorded_at`) of that application's events, else
   `updated_at`.

**Count invariants, asserted by the frozen migration test** (before → after):
| Check | Assertion |
|---|---|
| `applications` | `after >= before`, and `after - before == ` the orphans created in step 2 |
| `application_stage_history` | unchanged |
| `application_receipts` | unchanged; every row now has a non-NULL `application_id` |
| `tailored_documents` | unchanged |
| events from the fold | `count(events WHERE recorded_by='migration:0014_history') == count(application_stage_history)` and `… '0034_receipts') == count(application_receipts)` |
| artifacts from the fold | `count(artifacts WHERE made_by LIKE 'migration:%' OR made_by='human') == count(td WHERE ai_draft <> '') + count(td WHERE polished IS NOT NULL)` |

**Down migration** (`0037_application_spine.down.sql`): `DROP TABLE application_artifacts`,
`DROP TABLE application_events`, then `ALTER TABLE … DROP COLUMN` for all 19 added columns
(plain Postgres DDL; the SQLite-can't-DROP-COLUMN note in `0014_application_history.down.sql`
is obsolete — the store has been Postgres since the pg migration). **What down costs, stated
plainly:** every pre-migration row survives untouched, so a rollback the day after deploy
loses nothing; events and artifact versions created *after* the migration are dropped with
their tables and are not recoverable except from a backup. The down file says this in its
header, in those words. `up → down → up` is a frozen test.

## Tool contracts (8) — REST route and MCP tool are the same function

New module `backend/src/api/routes/applications.py`, mounted `prefix="/api"` in
`src/api/main.py` beside `bring`/`receipts`. Every route `Depends(require_user)` (matching
`bring.py:83` and `receipts.py:104`; not `require_verified_user` — nothing here spends an
LLM call). **Route-declaration order matters:** `/applications/export` and
`/applications/{application_id}` collide, so `export` is declared first; `application_id: int`
means a stray `"export"` would 422 rather than 500 either way.

| # | MCP tool | REST | Request | Response |
|---|---|---|---|---|
| 1 | `get_application` | `GET /applications/{id}` | `application_id: int`, `with_artifact_text: bool = false` | R11 object; 404 for a foreign or unknown id |
| 2 | `list_applications` | `GET /applications` | `status?: str`, `updated_since?: str`, `limit: int = 20 (≤200)`, `offset: int = 0` | `{applications: [summary], total}` |
| 3 | `save_artifact` | `POST /applications/{id}/artifacts` | `application_id`, `kind`, `text`, `label?`, `model?` | `{artifact_id, kind, version_no, chars, made_by, model, profile_version, created_at, event_id}` |
| 4 | `save_fit` | `PUT /applications/{id}/fit` | `application_id`, `score?: int 0-100`, `verdict?: str ≤200`, `gaps?: list[str] ≤50`, `reasoning?: str ≤4000` | `{application_id, fit: {...}, event_id}` |
| 5 | `record_event` | `POST /applications/{id}/events` | `application_id`, `event_type`, `detail?: str`, `payload?: object`, `occurred_at?: ISO`, `corrects_event_id?: int` | `{event_id, event_type, occurred_at, recorded_at, recorded_by, status}` |
| 6 | `record_application` | `POST /applications/{id}/receipt` | `application_id`, `channel?`, `note?`, `confirmation?`, `answers?: [{question, answer}]`, `fields_filled?: object`, `cv_artifact_id?`, `cover_letter_artifact_id?`, `applied_at?` | `{receipt_id, sent_at, cv_artifact_id, cv_version_no, cover_letter_artifact_id, channel, confirmation, url, event_id}` |
| 7 | `whats_new` | `GET /whats-new` | `since?: ISO`, `after_id?: int`, `limit: int = 50 (≤200)` | R9 object |
| 8 | `export_history` | `GET /applications/export` | `since?: ISO`, `include_text: bool = false` | R10 object |

`record_application` **is the existing tool enriched** (`mcp_server.py:332-356`), not a new
one; its old `(job_id, channel, note)` call shape keeps working — a `job_id` resolves to its
application. `bring_job` (`mcp_server.py:262`) gains `application_id` and `status` in its
return. Total MCP tools 8 → 15, and `tests/test_mcp_gate_parity.py TOOL_ROUTES:33-42` gains
a row for each of the seven new ones (its
`test_the_parity_table_covers_every_tool` turns red otherwise — that is the design).

## Security guardrails (mandatory section)

S1. **Auth on every new route.** All eleven new routes (8 tools + the single-artifact read +
    two internal write-through helpers) declare `Depends(require_user)`, which accepts the
    session cookie, a personal `j360_…` token and an OAuth `j360a_…` bearer identically
    (`auth_deps`, slice 1 R6). No route accepts an unauthenticated call.
    `backend/tests/test_route_auth_coverage.py` already enumerates routes and will fail on
    any new one that is not gated or explicitly public — no new `PUBLIC_ROUTES` entry is
    added by this slice.

S2. **No cross-user read or write, ever.** Every statement filters `user_id = ?` from
    `CurrentUser.id` — never from a path, query or body (#12/#25). A foreign or unknown
    `application_id`, `artifact_id` or `event_id` returns **404, not 403** (existence is
    itself information). Child writes verify ownership through the parent in the same
    transaction: `INSERT … SELECT` guarded by `WHERE EXISTS (SELECT 1 FROM applications
    WHERE id = ? AND user_id = ?)`, so a TOCTOU between "check" and "insert" cannot land a
    row under someone else's application. **The pg shim strips every FK clause**
    (`pg.py:217-226`), so there is no database-level protection to fall back on — this is
    the only protection.

S3. **`recorded_by` / `made_by` are derived, never accepted.** One helper,
    `src/services/applications/authorship.py::actor_for(user)`, returns
    `"web"` for `auth_via == "session"`, `"token:<token name>"` for a personal token, and
    `"agent:<client_name>"` for an OAuth grant (client name sanitised and truncated to 60
    chars — it is attacker-supplied text, sanitised at registration by slice 1 R2 and
    re-truncated here, never rendered as markup). A `recorded_by` or `made_by` field in a
    request body is **rejected with 422**, not ignored — silently dropping it would let a
    caller believe it worked. Forging authorship is the one thing an append-only log must
    not permit.

S4. **Artifact ids on a receipt are ownership-checked.** `cv_artifact_id` /
    `cover_letter_artifact_id` must resolve to a row with this `user_id` **and** this
    `application_id`; otherwise 404. Without both checks a caller could name another
    application's CV in their own receipt and read it back through `get_application`.

S5. **Input caps** — every one a parameter in `src/core/settings.py`, every breach a 422
    naming the field and the limit:
    `APPLICATION_EVENT_DETAIL_MAX_CHARS` 2 000 · `APPLICATION_EVENT_PAYLOAD_MAX_BYTES` 8 192
    (and the payload must be a JSON **object**, not a list or scalar — a list of 8 KB of
    strings is a different read cost) · `APPLICATION_ARTIFACT_MAX_CHARS` 60 000 ·
    `APPLICATION_ARTIFACT_MAX_VERSIONS` 200 per kind (429 with a plain-words message when
    exceeded, never a silent drop) · `APPLICATION_RECEIPT_ANSWERS_MAX` 50 items, 2 000 chars
    each · `APPLICATION_RECEIPT_FIELDS_MAX_BYTES` 8 192 · `APPLICATION_FIT_REASONING_MAX_CHARS`
    4 000 · `label` 100 · `channel` 100 · `confirmation` 200. FastAPI/Pydantic enforces the
    scalar ones with `Field(max_length=…)` exactly as `bring.py:42-47` does; the JSON ones
    are checked after `json.dumps` on the server, on the **serialised** size, because that
    is what the column costs.

S6. **`occurred_at` is bounded on the future side only.** It must parse as ISO-8601 with a
    timezone and be no more than `APPLICATION_EVENT_MAX_FUTURE_SECONDS` (300) ahead of now →
    otherwise 422. There is deliberately **no** lower bound: backdating is the normal case
    (the agent reads a reply from last Tuesday). `recorded_at` is always server time, so an
    unbounded `occurred_at` cannot poison the `whats_new` cursor (R9) or hide an event from
    an export.

S7. **Append-only is enforced three ways, not one.** (a) No PATCH/PUT/DELETE route exists on
    `/applications/{id}/events` or `/applications/{id}/artifacts` — asserted over
    `app.routes`, the shape used at `tests/test_receipts.py:139-143`. (b) A grep guard over
    `backend/src/**/*.py` for `UPDATE|DELETE FROM` against `application_events` or
    `application_artifacts` → must be empty. (c) `_PER_USER_TABLES` membership means account
    deletion is the only path that removes a row, and that is the user's own right.
    `PUT /applications/{id}/fit` is an update of the `applications` **slot**, which is
    explicitly not history — the history is the `fit_judged` event it appends.

S8. **Export is bounded and rate-limited per user.** R10's caps plus
    `EXPORT_HISTORY_MAX_PER_HOUR` (12) keyed on `user.id` via the existing `rate_limit`
    helper → 429. Per **user**, not per IP: behind the Next.js rewrite every agent shares
    the proxy address unless `JOB360_TRUST_PROXY=1` is set on the backend service (the trap
    the OAuth slice documented at its S6), so an IP key would throttle everyone at once.
    `whats_new` is bounded by its own limit and needs no separate budget.

S9. **Audit log, no bodies.** `get_audit_logger()` events `application_born`,
    `application_event_recorded` (`application_id`, `event_type`, `recorded_by`),
    `artifact_saved` (`artifact_id`, `kind`, `version_no`, `chars`), `fit_saved`,
    `receipt_created` (`receipt_id`, `cv_artifact_id`), `history_exported`
    (`applications`, `bytes`, `truncated`). **Never** artifact text, receipt text, event
    `detail`, `payload`, or the fit reasoning — the log is not a copy of the user's CV.

S10. **The search flag is not a security control** (R12). The search routes keep their
     existing auth; the flag only decides whether the feature exists. Nothing about the
     flag's state is used to skip an authorisation check.

S11. **MCP gate parity by hand** (M5). Tools call route *functions*, so `Depends` never
     runs: each new tool re-applies its route's gate itself, and each gets a `TOOL_ROUTES`
     row so `test_the_parity_table_covers_every_tool` proves nothing was forgotten. No new
     tool is `require_verified_user`, and `test_the_tailor_routes_really_are_gated` keeps
     proving the parity test is still testing something.

S12. **Deletion and export stay complete.** Both new tables go into `_PER_USER_TABLES`
     (Article 17 erase, and `hard_delete_user`'s survivor check will fail loudly if a table
     is missed — `database.py:1996-2004`) and `_EXPORT_TABLES` (Article 20). `text`,
     `detail` and `payload` are the user's own content and are exported as-is; nothing here
     is a credential, so no new entry is needed in `_EXPORT_REDACT_COLUMNS`.

## Frozen tests

**`backend/tests/test_application_spine.py`** (written red first):
1. `test_bring_creates_one_application_considering` — `POST /jobs/bring` returns an
   `application_id`, status `considering`, and exactly one `brought` event.
2. `test_bringing_twice_reuses_the_application` — same id, still one `brought` event.
3. `test_the_job_snapshot_survives_a_purge` — purge the catalog row, `get_application` still
   shows title and company; `catalog_present: false`.
4. `test_purge_spares_a_brought_job` — `purge_old_jobs` deletes an old scraped row and keeps
   an equally old `user_brought` row (hard rule #3).
5. `test_two_cv_versions_both_readable` — save v1 then v2; both readable in full by id, v1's
   text byte-identical after v2 exists.
6. `test_version_numbers_are_per_kind` — `cv` v1/v2 and `cover_letter` v1 coexist.
7. `test_artifact_over_the_cap_is_422` — and the message names `APPLICATION_ARTIFACT_MAX_CHARS`.
8. `test_made_by_and_recorded_by_come_from_the_credential` — session → `web`, personal token
   → `token:<name>`; a body field named `recorded_by` → 422.
9. `test_unknown_event_type_is_422_listing_the_allowed_types`.
10. `test_status_event_moves_status_and_note_does_not`.
11. `test_status_is_rebuildable_from_the_event_log` — replay == stored column (#21: assert a
    non-default value, not schema presence).
12. `test_a_correcting_event_supersedes_and_is_skipped_by_the_recompute`.
13. `test_events_are_append_only` — no PATCH/PUT/DELETE route; grep `backend/src/` clean for
    both new tables.
14. `test_backdated_occurred_at_is_accepted_and_ordered` / `test_future_occurred_at_is_422`.
15. `test_a_foreign_application_id_is_404_on_every_route` — get, save_artifact, save_fit,
    record_event, receipt, single-artifact read (two-user fixture).
16. `test_a_foreign_artifact_id_on_a_receipt_is_404` (S4).
17. `test_save_fit_stores_the_verdict_and_keeps_both_judgements` — slot overwritten, two
    `fit_judged` events in the log.
18. `test_save_fit_computes_nothing` — no `skill_matcher` / enrichment import or call on the
    path (monkeypatched to explode).
19. `test_record_application_freezes_the_named_version` — receipt names `cv` v2 and copies
    its text; an `applied` event appears.
20. `test_legacy_receipts_route_writes_through` — `POST /receipts/{job_id}` sets
    `application_id` and appends `applied`.
21. `test_legacy_bring_response_shape_is_unchanged` — `job`, `existing`, `scored` still there
    (constraint 4).
22. `test_whats_new_pages_on_recorded_at_not_occurred_at` — a backdated event still appears
    after a later `since`.
23. `test_whats_new_truncates_explicitly` — `truncated: true` + a usable `next_since`.
24. `test_export_history_bounds_and_truncates` — over `EXPORT_HISTORY_MAX_APPLICATIONS`,
    stops on a boundary and reports it.
25. `test_export_history_is_rate_limited_per_user` — 429 after the cap; a second user is
    unaffected (proves the key is the user, not the IP).
26. `test_get_application_omits_artifact_text_by_default` — and includes it under the cap
    with `with_artifact_text=true`.
27. `test_tailor_generate_also_writes_an_artifact_version` (R15) and
    `test_tailor_save_edit_writes_a_human_version`.
28. `test_event_types_match_vision_doc` — the two tuples equal the list at
    `docs/product/VISION.md:66-71`.
29. `test_audit_log_never_carries_a_body` — artifact text and event detail absent from the
    audit records (S9).

**`backend/tests/test_migration_0037.py`:**
30. `test_up_down_up_is_clean`.
31. `test_no_legacy_row_is_lost` — the count table above, before and after.
32. `test_stage_history_becomes_events` / `test_receipts_get_an_application_id_and_an_event`.
33. `test_a_tailored_doc_with_a_polish_becomes_two_versions`.
34. `test_an_orphan_receipt_gets_its_application_row` — a `(user, job)` present only in
    receipts.
35. `test_status_backfill_maps_every_legacy_stage` — all six values in `_VALID_STAGES`
    (`routes/pipeline.py:36`).

**`backend/tests/test_mcp_gate_parity.py`** (extended, not new): a `TOOL_ROUTES` row for each
of the seven new tools; the existing coverage and email-gate tests then prove parity.

**`backend/tests/test_search_flag.py`:**
36. `test_search_routes_404_when_the_flag_is_off` / `test_search_routes_work_when_on`.
37. `test_catalog_crons_are_off_by_default` — `WorkerSettings.cron_jobs` has no
    `refresh_catalog` / `enrichment_sweep` and still has `nightly_ghost_sweep` /
    `notification_tick`; both present with `CATALOG_CRONS_ENABLED=1`.

**Playwright `frontend/tests/e2e/applications-home.spec.ts`** (house style: fake the
`job360_session` cookie, `page.route` the API, assert the DOM — as
`tests/e2e/feed-visibility.spec.ts:21`):
38. Signed-in `/` shows the brought application with status `considering`, then `applied`
    after the mocked receipt; the Dashboard nav link is absent with the flag off; opening
    the application lists **two** CV versions and both open with their text.

## Done when
> The owner brings a job from Claude Code, saves two CV versions, records "applied" with the
> second, later records "replied" and "interview_requested" — and `whats_new` + the web home
> show exactly that, every version still readable.

## Flagged concerns
C1. **Two vocabularies until slice 5.** `status` (spine) and `stage` (pipeline UI) coexist,
    joined by one mapping dict (R4). Every other place that reads `stage` — `KanbanBoard`,
    `/api/pipeline`, `get_stale_applications` — is unchanged and still correct, but a
    reviewer should check the dict, not the call sites.
C2. **`NEXT_PUBLIC_SEARCH_UI_ENABLED` is baked at build time.** Flipping it is a redeploy.
    Say so in the PR so nobody debugs a "flag that does not work".
C3. **Windows full-suite flake** — targeted gate locally, Linux CI is the verdict.
C4. **The fold is 11 rows in production** (measured above), so the migration will look
    trivially green in prod. The real proof is the seeded migration test, not the deploy.
C5. **`recorded_by` for an OAuth caller depends on slice 1's `client_name`**, which is
    unverified attacker text by construction. It is truncated and stored as data, never
    rendered as markup, and the audit log carries the client id, not the name (slice 1 R10).
