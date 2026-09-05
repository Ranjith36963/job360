<!-- doc: PLAN | status: ACTIVE | pr: — -->
# Spec: contacts, outreach, stats, profile edits (slice 4, #482)

Intent: `intent.md`. Builds on the spine spec
(`docs/plans/2026-09-04-application-spine/spec.md`) — every rule there (R1–R10, S1–S12)
still holds; this document only adds. Line numbers are as of `1fba085`.

## Measured starting point

| Thing | Where | State |
|---|---|---|
| `contact_added`, `outreach_sent` event types | `backend/src/core/settings.py:455-457` | exist, validated by `spine.py:46 validate_event_type` |
| `outreach` artifact kind | `settings.py:472`, checked at `spine.py:324-325` | exists, saveable today |
| a table for the person | — | **none** |
| `application_receipts.cv_artifact_id` | `migrations/0037:107` | exists — the CV version a receipt names |
| `applications.job_title` | `migrations/0037:45` | the role, copied at bring time |
| `application_events` indexes | `0037:78-83` | `(user_id, event_type)` — stats reads this |
| `GET /profile` | `routes/profile.py:414` → `_build_profile_response :193-401` | read-only; web edits preferences only via `POST /profile/preferences :713` → `_apply_preferences :571` |
| `load_profile` | `services/profile/storage.py:322-343` | the one door every reader uses (routes, tailor, MCP `get_profile`) |
| `save_profile` | `storage.py:56-153` | rewrites `cv_data` wholesale on every extraction |
| MCP tools | `mcp_server.py:220-563` | 15; parity table `tests/test_mcp_gate_parity.py:49-73` needs one row per tool |
| `_PER_USER_TABLES` / `_EXPORT_TABLES` | `database.py:1798 / :1817` | new per-user tables must join both, plus `backend/scripts/observe.py PER_USER_TABLES` |
| actor stamp | `services/applications/authorship.py:19-34 actor_for` | "web" \| "token:<name>" \| "agent:<name>" |

## Requirements

- **R1 — contact is a row, not a payload.** A contact lives in `application_contacts`,
  owned by one application, one user. Adding one appends a `contact_added` event whose
  `payload` is `{"contact_id": <id>}` and whose `detail` is the contact's display line
  (name — role). The event, not the row, is what `whats_new` / `export_history` show.
- **R2 — add only, idempotent on email.** Same application + same non-empty email
  (lower-cased, trimmed) → the existing row comes back with `already_existed: true`, no
  second row, no second event. Without an email there is no identity → a new row each
  time (the agent chose not to give one). No update, no delete (intent §Non-goals).
- **R3 — contacts ride the application.** `GET /applications/{id}` gains `contacts: [...]`
  (all, oldest first). `export_history` gains `contacts` per application. Deleting the
  account deletes them (`_PER_USER_TABLES`).
- **R4 — outreach is visible on the web.** The application detail page shows a
  **People** section (contacts) and, under it, the `outreach` artifacts and
  `outreach_sent` events already in the log. No new endpoint for this — the page
  already has the artifacts and the events; it needs the contacts and the layout.
- **R5 — stats are counts over the log, nothing else.** For the user's applications:
  `brought`, `applied`, `replied`, `interview`, `offer`, `rejected` as
  `COUNT(DISTINCT application_id)` having ≥1 event of that type
  (`interview` = `interview_requested` OR `interview_scheduled` OR `interview_done`);
  `brought` = number of applications in scope. Rates: `reply_rate = replied/applied`,
  `interview_rate = interview/applied`, `offer_rate = offer/applied`, null when
  `applied = 0` (rule #29 — an empty shelf stays silent, never 0%).
- **R6 — two groupings, both from stored data.** `by_cv_version`: artifacts are
  per application, so the cross-application identity of "a CV version" is the
  **label** the agent gave it in `save_artifact` (spine R5: `label`). Key =
  `lower(trim(label))` of the CV artifact named by the application's **latest**
  receipt (`MAX(id)` per application); display = the label as written on the group's
  earliest application (lowest `applications.id`), same rule for `by_role`. Applications
  with no receipt, a receipt without a CV artifact, or an unlabelled artifact fall in one
  group with `label: null` — the tool description tells the agent to label its variants
  to get per-variant counts. Each group also carries `profile_versions` (distinct
  `application_artifacts.profile_version` values seen, nulls dropped) so the agent can
  tie a label back to the profile snapshot it was built from.
  `by_role`: key = `lower(trim(applications.job_title))`, display as above.
  Note: `record_application` itself appends an `applied` event (spine `spine.py:574`),
  so a receipted application is an applied one without a second call.
  No keyword lists, no normalisation beyond case/whitespace (memory: never hand-type
  a vocabulary). Groups are ordered by `applied DESC, brought DESC`, capped at
  `STATS_MAX_GROUPS` (default 50); the response says `groups_truncated: true` when cut.
- **R7 — `since` scopes the universe.** Optional ISO date/datetime; applications with
  `created_at >= since` are in scope. Events are never filtered by time on their own —
  an application either counts or it doesn't.
- **R8 — profile edits are an append-only overlay.** `profile_edits(path, value, set_by,
  set_at)`; the current overlay = the newest row per path; `value = NULL` means
  "cleared — fall back to extraction". `load_profile` applies the overlay after
  building the dataclasses, so **every** reader (web, tailor, MCP `get_profile`) sees the
  same profile. A re-extraction never removes an edit; only a clear does.
- **R9 — the editable set is closed and is a parameter.** `PROFILE_EDITABLE_PATHS`
  (settings tuple, env-extendable via `PROFILE_EXTRA_EDITABLE_PATHS` only for paths
  that already exist on the dataclasses):
  `cv_data.name`, `cv_data.headline`, `cv_data.location`, `cv_data.summary`,
  `cv_data.skills`, `cv_data.job_titles`, `cv_data.education`, `cv_data.certifications`,
  `cv_data.achievements`, `cv_data.cv_right_to_work`, `cv_data.cv_languages`,
  `cv_data.links` (**new** `list[str]` field on `CVData`, default empty — portfolio,
  website, etc.), `preferences.target_job_titles`, `preferences.preferred_locations`,
  `preferences.industries`, `preferences.additional_skills`,
  `preferences.excluded_skills`, `preferences.negative_keywords`,
  `preferences.salary_min`, `preferences.salary_max`, `preferences.work_arrangement`,
  `preferences.experience_level`, `preferences.about_me`, `preferences.needs_visa`.
  A path outside the set → 422 naming the path and listing the set.
- **R10 — values are typed by the dataclass, not by a hand-written table.** The
  validator reads the field's annotation (`str`, `list[str]`, `Optional[float]`, `bool`)
  from `dataclasses.fields(CVData/UserPreferences)` and coerces/refuses accordingly.
  Closed-set preferences (`work_arrangement`, `experience_level`) use the same
  vocabulary the web form uses (`profile.py:522` inline set → lift it to a named
  `_VALID_WORK_ARRANGEMENTS` beside `_VALID_EXPERIENCE_LEVELS :538`) — one vocabulary,
  not two. The difference: the web normaliser turns an unknown value into silence
  (rule #29, the form can only send what it offers); the agent gets a **422 listing the
  allowed values**, because silently dropping what it said would hide the mistake.
  An empty string is the explicit "unset" for either and is accepted.
- **R11 — provenance is visible.** `GET /profile` gains `agent_edits: [{path, value,
  set_by, set_at}]` (current overlay, non-cleared). MCP `get_profile` gains the same
  list plus `editable_paths` and `fields` — a `{path: current value}` map over every
  editable path, so the agent sees what it may change and what it currently says
  (today's slim summary shows neither name nor location). The web profile page shows
  the edited value **in place**
  and an "Edited by <set_by> on <date>" mark beside it; there is no separate list to
  reconcile.
- **R12 — same function on REST and MCP.** `add_contact` → `POST
  /applications/{id}/contacts`; `stats` → `GET /applications/stats`; `update_profile` →
  `PATCH /profile`. The MCP tool calls the route function (parity test), the actor is
  derived from the caller (S3), never accepted as input.

## Data model

Migration `0038_contacts_and_profile_edits` (up + down; conventions of 0037: TEXT ISO
timestamps, `IF NOT EXISTS`, FK clauses for documentation only, DDL only — no row
touched, nothing folded).

```sql
CREATE TABLE IF NOT EXISTS application_contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    application_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',          -- stored lower-cased + trimmed; '' = none
    linkedin_url TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    added_by TEXT NOT NULL,                  -- actor_for(user)
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_application_contacts_user_app
    ON application_contacts(user_id, application_id, id);
-- R2 idempotency: one row per non-empty email per application. Partial unique
-- index so email='' never collides.
CREATE UNIQUE INDEX IF NOT EXISTS uq_application_contacts_app_email
    ON application_contacts(application_id, email) WHERE email <> '';

CREATE TABLE IF NOT EXISTS profile_edits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    path TEXT NOT NULL,                      -- one of PROFILE_EDITABLE_PATHS
    value TEXT,                              -- JSON-encoded value; NULL = cleared
    set_by TEXT NOT NULL,                    -- actor_for(user)
    set_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_profile_edits_user_path_id
    ON profile_edits(user_id, path, id DESC);
```

The pg shim rewrites `AUTOINCREMENT` (`pg.py:193-195`) and strips FK clauses
(`:217-226`); confirm in `tests/test_pg_translate.py` that a partial unique index
(`WHERE email <> ''`) passes `translate()` unchanged — if it does not, the worker adds
the translate case, never drops the index.

Registries: both tables join `JobDatabase._PER_USER_TABLES` (`database.py:1798`),
`_EXPORT_TABLES` (`:1817`) and `backend/scripts/observe.py PER_USER_TABLES`.

Current overlay query (one round trip, used by `load_profile` and `GET /profile`):

```sql
SELECT path, value, set_by, set_at
FROM profile_edits
WHERE user_id = ? AND id IN (
    SELECT MAX(id) FROM profile_edits WHERE user_id = ? GROUP BY path
)
```

Rows with `value IS NULL` are dropped after the query (they only exist to shadow older
rows). `load_profile` is sync (`pgsync`) — the overlay read is one extra statement on
the same connection, not a second connection.

## Tool contracts

Every response carries `recorded_by`/`added_by`/`set_by` from `actor_for(user)`. Every
cap below is a settings parameter with an env override; a breach is a 422 naming the
field and the limit (spine S5 pattern).

### `add_contact` — `POST /applications/{id}/contacts`

Request (`extra="forbid"`):

| Field | Type | Rule |
|---|---|---|
| `name` | str | required, 1–`CONTACT_NAME_MAX_CHARS` (200) after trim |
| `role` | str | optional, ≤200 |
| `email` | str | optional, ≤254, must match `^[^@\s]+@[^@\s]+\.[^@\s]+$` when non-empty; stored lower+trim |
| `linkedin_url` | str | optional, ≤300, must start `https://` or `http://` when non-empty; refused otherwise (no `javascript:`) |
| `notes` | str | optional, ≤`CONTACT_NOTES_MAX_CHARS` (2000) |
| `occurred_at` | ISO datetime | optional; when the agent actually found/spoke to them; default now; S6 future bound |

Response `201` (or `200` when `already_existed`):
`{contact: {id, application_id, name, role, email, linkedin_url, notes, added_by,
created_at}, already_existed: bool, event_id: int | null}`.
Errors: 404 foreign/missing application (same shape as `get_application`), 422 caps,
409 `CONTACTS_PER_APPLICATION_MAX` (50) reached.

Write path (one transaction): ownership-guarded `INSERT … SELECT … WHERE EXISTS
(applications row for this user)` → on unique-index conflict, `SELECT` the existing row
→ return it with `already_existed: true` and no event; otherwise `append_event(…,
event_type="contact_added", detail=f"{name} — {role}", payload={"contact_id": id})`.

### `stats` — `GET /applications/stats`

Query: `since?` (ISO). Declared **before** `/applications/{id}` in the router (the same
trap `export` avoided at `applications.py:356`).

Response:

```json
{
  "since": null,
  "overall": {"brought": 12, "applied": 9, "replied": 3, "interview": 2, "offer": 0, "rejected": 4,
              "reply_rate": 0.333, "interview_rate": 0.222, "offer_rate": 0.0},
  "by_cv_version": [{"label": "quant-heavy", "profile_versions": [12], "brought": 5, "applied": 5, "...": "..."},
                    {"label": null, "profile_versions": [], "brought": 3, "applied": 1, "...": "..."}],
  "by_role": [{"role": "Data Engineer", "brought": 6, "applied": 5, "...": "..."}],
  "groups_truncated": false,
  "computed_at": "2026-09-05T10:00:00Z"
}
```

Rates are rounded to 3 dp; `null` when `applied = 0`. Implementation is one SQL per
grouping over `application_events` joined to `applications` (filtered on `user_id`
**on both tables**) with `CASE WHEN event_type IN (…)` sums over
`COUNT(DISTINCT application_id)` — the event-type sets come from
`APPLICATION_STATUS_EVENT_TYPES`, not literals. Rate-limited `STATS_MAX_PER_HOUR` (60).

### `update_profile` — `PATCH /profile`

Request (`extra="forbid"`): `{edits: [{path: str, value: Any | null}]}` — 1 to
`PROFILE_EDIT_MAX_PATHS_PER_CALL` (30) items; `value: null` clears.

Per edit: path ∈ `PROFILE_EDITABLE_PATHS` else 422; value coerced by the dataclass
annotation (R10) — `str` ≤ `PROFILE_EDIT_MAX_CHARS` (2000), `list[str]` ≤
`PROFILE_EDIT_MAX_LIST_ITEMS` (100) items each ≤ 200 chars and de-duplicated
preserving order, `Optional[float]` finite ≥ 0, `bool` strictly bool; closed sets via
the existing normalisers, unknown value → 422 listing the allowed values.

Response: `{applied: [{path, value, set_by, set_at}], profile: <ProfileResponse>}` — the
full profile as `GET /profile` now returns it, so the agent sees the effect without a
second call. Rate-limited `PROFILE_EDIT_MAX_PER_HOUR` (120). A user with no profile row
gets an empty base created first (`save_profile(UserProfile(), user_id,
source_action="agent_edit")`) so the overlay has something to sit on.

### Additions to existing tools

- `get_application` (REST + MCP): `contacts: [...]`.
- `export_history`: `contacts` per application; `profile_edits` (all rows, including
  cleared — it is the history) at the top level.
- `get_profile` (MCP): `agent_edits`, `editable_paths`.
- `GET /profile`: `agent_edits`.

## Security guardrails

- **S1 — ownership on every write and read.** Contacts insert via `INSERT … SELECT …
  WHERE EXISTS(applications WHERE id=? AND user_id=?)`; 0 rows → 404, the same 404 as a
  missing id (no existence oracle). Stats and overlay queries filter `user_id` on every
  table in the join, not just the driving one.
- **S2 — actor is derived, never accepted.** `added_by` / `set_by` / event
  `recorded_by` come from `actor_for(user)`; the request models forbid those fields
  (`extra="forbid"`).
- **S3 — PII stays out of logs.** Audit lines for `contact_added` and `update_profile`
  log ids, counts, paths and lengths — never `name`, `email`, `linkedin_url`, `notes`
  or the edit value. Test greps the audit call sites.
- **S4 — caps are parameters, breaches are 422s.** Every limit above is in
  `settings.py` with an env override and appears in `.env.example`; no silent clipping.
- **S5 — URL and email shape.** `linkedin_url` must be http(s); `email` must match the
  simple regex; both are stored as given (trimmed) and rendered as text on the web —
  the frontend never builds an `href` from `linkedin_url` unless it starts with
  `https://`.
- **S6 — `occurred_at` future bound** reuses `APPLICATION_EVENT_MAX_FUTURE_SECONDS`.
- **S7 — rate limits per user, never per IP** (the proxy trap): `STATS_MAX_PER_HOUR`,
  `PROFILE_EDIT_MAX_PER_HOUR`, and contacts share the spine's existing per-user write
  budget if one exists, else `CONTACTS_MAX_PER_HOUR` (120).
- **S8 — the overlay cannot reach outside the dataclasses.** Paths are matched against
  the closed tuple, then split on `.` and applied with `setattr` only when the attribute
  is a declared dataclass field — never `__dict__`, never `getattr` chains on user
  input. `PROFILE_EXTRA_EDITABLE_PATHS` is validated at import: an unknown path is
  refused with a startup error, not accepted.
- **S9 — no JSON bombs.** `value` is bounded before JSON-encoding (`PROFILE_EDIT_MAX_CHARS`
  applies to the encoded size too); lists are bounded by item count and item length.
- **S10 — stats cost is bounded.** One index-backed query per grouping; groups capped
  by `STATS_MAX_GROUPS`; no per-application loop in Python.
  Review addition: the FETCH is bounded too — the driving query reads the newest
  `STATS_MAX_APPLICATIONS` (default 5000, env-overridable) applications and the
  response carries `applications_truncated: bool`, so a large history gives an
  honest partial answer instead of an unbounded read.
- **S11 — MCP gate parity.** Three new `TOOL_ROUTES` rows; `test_mcp_gate_parity`
  proves the MCP tool inherits `require_user` and the same 404/422/429 behaviour.
- **S12 — append-only stays append-only.** No runtime `UPDATE`/`DELETE` on
  `application_contacts`, `profile_edits` (grep test, same pattern as
  `test_events_are_append_only`). Account deletion is the one exception and goes
  through `_PER_USER_TABLES`.

## Frozen tests (red before the build)

`backend/tests/test_slice4_contacts.py`, `test_slice4_stats.py`,
`test_slice4_profile_edits.py` — using `_bring`, `_record_event`, `_record_receipt`,
`_save_artifact`, `_bearer_client`, `_second_user_session_cookie` from
`test_application_spine.py` **copied locally, never imported** (memory: cross-module
fixture import breaks schema isolation).

Contacts: add → 201 + row + `contact_added` event with `payload.contact_id`; same email
twice → 200 `already_existed`, one row, one event; no email twice → two rows; foreign
application → 404; bad `linkedin_url` (`javascript:`) → 422; name over cap → 422;
`get_application` includes contacts; `export_history` includes contacts; second user
cannot see them; audit log line contains no email (caplog); append-only grep.

Stats: build a known history (3 applications: A applied+replied+interview_scheduled
with receipt on cv v1; B applied+rejected on cv v2; C brought only) → exact counts
`brought 3, applied 2, replied 1, interview 1, offer 0, rejected 1, reply_rate 0.5`;
`by_cv_version` has 3 groups ("quant-heavy", "platform", null) with the right counts —
the same label on two applications is ONE group; duplicate `applied`
events count once; `by_role` keys are case/space-insensitive; `since` excludes A when
set after A's `created_at`; second user sees zeros; `applied = 0` → rates `null`;
route declared before `/{id}` (`"stats"` is not parsed as an id).

Profile edits: set `cv_data.location` → `GET /profile` shows it + `agent_edits`
carries `set_by` = the token actor; MCP `get_profile` shows it; re-extraction
(`save_profile` with a new location) does **not** undo it; clear (`value: null`) →
extraction's value returns and the overlay row is gone from `agent_edits` but present in
`export_history.profile_edits`; unknown path → 422 listing the set; wrong type
(`skills: "python"`) → 422; `work_arrangement: "office"` → 422 with allowed values;
list de-dup + cap; 31 edits → 422; second user unaffected; no profile row → base
created; `cv_data.links` round-trips through `save_profile`/`load_profile`.

Parity: three new `TOOL_ROUTES` rows, `test_the_parity_table_covers_every_tool` green.

## Done when

- The three frozen files are green; the spine, receipts, profile and parity suites are
  untouched and green; ruff 0; mypy 0.
- Migration 0038 applies up and down on a fresh DB (the same real-boot walk slice 3 did).
- Frontend: `npm run type-check`, lint, unit green; regenerated `openapi.json` +
  `api-types.ts` committed; Playwright spec for the People section and the profile
  "edited by your agent" mark.
- Real-app walk with a bearer token: add a contact + save an outreach artifact + record
  `outreach_sent` → both visible on `/applications/{id}`; `stats` matches a hand count;
  `update_profile` location → visible on `/profile` with the mark.
- Two Opus review passes (bugs, conventions), every finding fixed and pinned.

## Flagged concerns

- **Web edits vs overlay.** `POST /profile/preferences` loads the (overlaid) profile,
  mutates, saves — so a web save copies the agent's value into the base JSON. Clearing
  the edit afterwards reveals *that* base, which now equals the edit. This is correct
  ("the seeker confirmed it on the web") and is documented in the tool description.
- **`applications.created_at` for `since`** is bring time, not apply time. Documented in
  the tool description; changing the anchor later is a parameter, not a schema change.
- **Contacts have no edit path.** A typo in a name is fixed by adding the right contact;
  slice 6+ may add `correct_contact` if the owner asks. Not in scope now.
