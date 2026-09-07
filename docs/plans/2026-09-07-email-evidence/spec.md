<!-- doc: PLAN | status: ACTIVE | pr: — -->
# Spec: email evidence on events + interview datetime (slice 6, #513)

Builds on the spine spec (`docs/plans/2026-09-04-application-spine/spec.md`, R1–R10,
S1–S12) and the contacts spec (`2026-09-05-contacts-stats/spec.md`). Every rule there
still holds; this document only adds. Line numbers are as of `e0dde31`.

Owner decisions this implements (VISION.md decisions 19–20, 2026-09-07): the agent's own
Gmail connector reads the inbox — we never do (decision 8, hard rule M2). What we add is
the **store** side: an event can name the email it came from, a re-read inbox adds zero
rows, and an interview has a real datetime instead of prose in `detail`.

## Measured starting point

| Thing | Where | State |
|---|---|---|
| `application_events` columns | `migrations/0037_application_spine.up.sql:66-83` | `id, user_id, application_id, event_type, detail, payload, occurred_at, recorded_at, recorded_by, corrects_event_id` — nothing names a source |
| write door | `services/applications/spine.py:176 append_event` | one INSERT, then `replay_status` → `applications` slot |
| route | `api/routes/applications.py:646 record_event` → `RecordEventRequest :75` (`extra="forbid"`) → `RecordEventResponse :302` | no `source`, no `scheduled_at` |
| MCP tool | `api/mcp_server.py:508 record_event` | builds the same request model; parity table `tests/test_mcp_gate_parity.py:49-82` |
| read shapes | `ApplicationEventOut :169` (detail + export) · `WhatsNewEventOut :323` | both emit the raw event dict; nothing formats "interview at …" server-side |
| idempotency idiom to copy | `0038_contacts_and_profile_edits.up.sql:39-40` + `services/applications/contacts.py:134 add_contact` | partial UNIQUE `WHERE email <> ''`, read-before-insert, `already_existed: true` |
| timestamp idiom to copy | `spine.py:83 parse_occurred_at` | ISO-8601, tz required, bounded future, stored as `+00:00` (TEXT sorts lexically) |
| append-only guard | `tests/test_application_spine.py:366 test_events_are_append_only` | greps `backend/src/` for `UPDATE|DELETE FROM application_events` |
| timeline | `frontend/src/components/applications/Timeline.tsx` · header `app/applications/[id]/ApplicationClient.tsx:78-113` | shows `event_type`, `detail`, `occurred_at`, `recorded_by` |
| api types | `frontend/src/lib/api-types.ts` (generated; `npm run gen:types`; gate runs `check:types-drift`) | must be regenerated with any response-model change |

## Requirements

- **R1 — an event may carry its source.** `record_event` accepts an optional `source`
  object: `{kind, message_id, sender, subject, received_at}`. `kind` is one of
  `settings.APPLICATION_EVENT_SOURCE_KINDS` (today `("email",)`). It is stored on the
  event row itself (six new columns, see Data model) — not in `payload`, so the
  idempotency key can be indexed and the shape is typed on every read.
- **R2 — same message, zero new rows.** `(application_id, source.message_id)` is the
  identity. A second `record_event` naming a message id already on this application
  returns the **existing** event (`already_existed: true`, its `event_id`, its
  `event_type`, the application's current `status`) and writes nothing — no row, no
  status replay. No UPDATE, ever (M3): if the agent classified the email wrongly the
  first time, it records a correcting event with `corrects_event_id`, as today. The same
  message id on a *different* application is a new row (an email can concern two roles).
  An event without `source` behaves exactly as before.
- **R3 — interview datetime is a field.** `record_event` accepts an optional
  `scheduled_at` (ISO-8601 with timezone, stored normalised to `+00:00`). Allowed only on
  `settings.APPLICATION_SCHEDULABLE_EVENT_TYPES` (today `("interview_requested",
  "interview_scheduled")`) — 422 elsewhere. May be up to
  `APPLICATION_SCHEDULED_AT_MAX_FUTURE_SECONDS` (default 366 days) in the future; may be
  in the past (recording an interview that already happened is normal).
- **R4 — every reader shows both.** `ApplicationEventOut` and `WhatsNewEventOut` gain
  `source` (object or `null`) and `scheduled_at` (string or `null`). `export_history`
  inherits it (same function). `GET /applications/{id}` gains `interview_at`: the
  `scheduled_at` of the newest **non-superseded** event that carries one, else `null`.
- **R5 — the web shows it.** `Timeline.tsx` renders a source line under an event
  (`✉ sender — "subject"`, plus received time when present) and a `Scheduled for <local
  datetime>` line when `scheduled_at` is set. The detail header shows an
  `Interview <local datetime>` pill when `interview_at` is set. Text only — React escapes
  it; no HTML, no links built from email content.
- **R6 — MCP parity (M5).** The `record_event` tool gains `source` and `scheduled_at`
  parameters and calls the same route function. The docstring tells the agent the
  idempotency rule in one sentence so it can re-read an inbox without bookkeeping.

## Data model — migration `0041_event_source_and_schedule`

`up.sql` (DDL only, no row read or changed; conventions from 0038):

```sql
ALTER TABLE application_events ADD COLUMN IF NOT EXISTS source_kind        TEXT NOT NULL DEFAULT '';
ALTER TABLE application_events ADD COLUMN IF NOT EXISTS source_message_id  TEXT NOT NULL DEFAULT '';  -- '' = no source
ALTER TABLE application_events ADD COLUMN IF NOT EXISTS source_sender      TEXT NOT NULL DEFAULT '';
ALTER TABLE application_events ADD COLUMN IF NOT EXISTS source_subject     TEXT NOT NULL DEFAULT '';
ALTER TABLE application_events ADD COLUMN IF NOT EXISTS source_received_at TEXT NOT NULL DEFAULT '';  -- ISO +00:00 or ''
ALTER TABLE application_events ADD COLUMN IF NOT EXISTS scheduled_at       TEXT NOT NULL DEFAULT '';  -- ISO +00:00 or ''
CREATE UNIQUE INDEX IF NOT EXISTS uq_application_events_app_source_msg
    ON application_events(application_id, source_message_id) WHERE source_message_id <> '';
```

`down.sql`: `DROP INDEX IF EXISTS …;` then six `ALTER TABLE … DROP COLUMN IF EXISTS …;`
with the 0037-style "what this costs" header (every stored source and datetime is lost;
the events themselves survive).

`''` means "none" (the 0038 convention) so the partial index and the readers need no
NULL branch; the API still emits `null` for none (R4).

## Tool contracts

### `record_event` — `POST /applications/{id}/events` (additions)

Request (`RecordEventRequest`, still `extra="forbid"`):

```
source:       EventSource | null = null
scheduled_at: str | null = null
EventSource (extra="forbid"):
  kind: str = "email"          # ∈ APPLICATION_EVENT_SOURCE_KINDS
  message_id: str              # required, trimmed, 1..APPLICATION_EVENT_SOURCE_MESSAGE_ID_MAX_CHARS (256)
  sender: str = ""             # trimmed, ≤ APPLICATION_EVENT_SOURCE_SENDER_MAX_CHARS (320)
  subject: str = ""            # trimmed, ≤ APPLICATION_EVENT_SOURCE_SUBJECT_MAX_CHARS (500)
  received_at: str | null      # ISO-8601 with tz → normalised +00:00 (parse_occurred_at rules, no future beyond its bound)
```

Response (`RecordEventResponse`): existing six fields **plus `already_existed: bool`**.
On a duplicate: `event_id`/`event_type`/`occurred_at`/`recorded_at`/`recorded_by` are
the existing row's, `status` is the application's current status.

Order of checks in the route (unchanged order, new ones slotted in):
`validate_event_type` → `clamp_detail` → `validate_payload` → `parse_occurred_at` →
**`validate_source`** → **`parse_scheduled_at(raw, event_type)`** → ownership 404 →
`append_event`.

`append_event(…, source: Optional[dict] = None, scheduled_at: str = "")`:

```
if source: existing = SELECT … WHERE application_id=? AND source_message_id=?
           if existing: audit "application_event_duplicate" (ids only); return existing + already_existed=True
INSERT (… six new columns …)
  except pg.IntegrityError:        # two agents raced the same message id
      rollback; re-read; return existing + already_existed=True
replay_status → UPDATE applications slot (as today)
```

### Read shapes (additions)

```
EventSourceOut: {kind: str, message_id: str, sender: str, subject: str, received_at: str | null}
ApplicationEventOut  += source: EventSourceOut | null, scheduled_at: str | null
WhatsNewEventOut     += source: EventSourceOut | null, scheduled_at: str | null
ApplicationDetailOut += interview_at: str | null
```

`list_events_for_display` and `whats_new` build `source` from the six columns
(`None` when `source_message_id == ''`).

## Security guardrails

- **S1 — ownership first.** The duplicate lookup runs only after `get_owned_application`
  returned a row, so a foreign caller gets 404 before any message-id probe (rule #12/#25).
  The UNIQUE index is per `application_id`, and the application is per `user_id`.
- **S2 — caps are settings, breaches are 422 naming the setting** (S5 of the spine
  spec): message id 256, sender 320, subject 500 chars; `kind` outside the tuple;
  `scheduled_at` on a non-schedulable type; missing timezone. Never clipped silently.
- **S3 — no control characters.** `message_id`, `sender`, `subject` reject Unicode
  category `Cc` (C0, DEL, C1), `Zl`/`Zp` (U+2028/U+2029 — line terminators in JSON/JS
  exports) and the bidi embeddings/overrides/isolates U+202A–U+202E, U+2066–U+2069
  (they flip how the rest of a timeline line renders). NOT all of `Cf`: emoji ZWJ
  sequences are legal subjects. (Widened from `< 0x20 or == 0x7f` at review.) Trim
  first, then check, then cap.
- **S4 — the audit log carries no body.** The duplicate/record audit records carry
  `application_id`, `event_id`, `event_type`, `recorded_by`, `already_existed` — never
  `subject`, `sender` or `message_id` (extends `test_audit_log_never_carries_a_body`).
- **S5 — append-only stays provable.** No `UPDATE`/`DELETE` on `application_events`
  is added; `test_events_are_append_only` must still pass unchanged. The race path
  re-reads, it never upserts.
- **S6 — text stays text on the web.** Rendered through React text nodes only; no
  `dangerouslySetInnerHTML`, no `href` built from `sender`/`subject`.
- **S7 — no new fetch, no new token, no background job.** The agent's connector reads
  the inbox; Job360 never touches Gmail (M2, decision 8).
- **S8 — the mirror of `occurred_at`'s future bound.** `received_at` uses the
  `occurred_at` bound (an email cannot arrive in the future); `scheduled_at` has its own,
  longer bound (an interview can).

## Frozen tests (red before the build) — `backend/tests/test_application_spine.py`

1. `test_event_with_email_source_is_stored_and_shown` — record `replied` with a full
   source → `GET /applications/{id}` event carries the same `source` (received_at
   normalised to `+00:00`); `whats_new` shows it too; `already_existed` is `false`.
2. `test_same_message_id_twice_adds_zero_rows` — second call (even with a different
   `event_type`) returns the first `event_id`, `already_existed: true`, the first
   `event_type`; `SELECT COUNT(*)` on the application's events is unchanged; status
   unchanged.
3. `test_same_message_id_on_another_application_is_a_new_row` — two applications, same
   message id → two rows, two ids.
4. `test_event_without_source_has_null_source` — plain `note` → `source: null`,
   `scheduled_at: null`, `already_existed: false`.
5. `test_scheduled_at_is_normalised_and_becomes_interview_at` — `interview_scheduled`
   with `2026-09-15T10:00:00+01:00` → event `scheduled_at == "2026-09-15T09:00:00+00:00"`,
   detail `interview_at` equals it; a later correcting event that supersedes it makes
   `interview_at` fall back to the correcting event's value (or `null`).
6. `test_scheduled_at_on_a_non_interview_event_is_422` — `note` + `scheduled_at` → 422
   naming `APPLICATION_SCHEDULABLE_EVENT_TYPES`.
7. `test_scheduled_at_without_timezone_is_422`.
8. `test_source_caps_and_control_chars_are_422` — subject of 501 chars → 422 naming
   `APPLICATION_EVENT_SOURCE_SUBJECT_MAX_CHARS`; `kind: "sms"` → 422; subject with `\n`
   → 422; `message_id: "  "` → 422; unknown key inside `source` → 422 (forbid).
9. `test_source_never_reaches_the_audit_log` — record with source twice; no audit
   record contains the subject, sender or message id.
10. `test_a_foreign_application_id_is_404_before_the_duplicate_lookup` — user B posts
    user A's application id with A's message id → 404, and A's row count is unchanged.
11. `tests/test_mcp_server.py::test_record_event_tool_passes_source_and_scheduled_at` —
    the tool call lands both fields on the stored event and returns `already_existed`.
12. `test_events_are_append_only` and `test_the_parity_table_covers_every_tool` stay
    green unchanged.

## Done when

- Migration 0041 pair applied by `init_db()` on boot; up→down→up round-trips locally.
- All twelve above green; full gate green; api-types regenerated (drift check green).
- Draft PR; owner merges; then the live proof: the owner's Claude.ai + Gmail connector
  against prod records a rejection, an invite with a date, and a plain reply — and a
  second read of the same inbox adds zero rows (`stats`/`whats_new` unchanged).

## Flagged concerns

- The **applications list card** does not show `interview_at` — that needs a per-row
  subquery or a cached slot on `applications`; deferred until the detail page proves the
  field earns it.
- `source.kind` is a tuple today with one member; adding `"whatsapp"` later is a settings
  change, not a schema change (decision 22).
- `interview_at` is "newest non-superseded event with a `scheduled_at`", not "next in the
  future" — a rescheduled interview is a correcting event, which is what makes the old
  value drop out.
