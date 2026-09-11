# Slice 9 — "Flag for next time": lessons written, listed, and handed to the agent (#516)
<!-- doc: PLAN | written 2026-09-11 -->

## Intent (the owner, 2026-09-11: "finish feature 24")

A lesson is a note-to-self after an application: "always mention the
Kubernetes cert". Today the backend accepts a `lesson` event (VISION event
list; `settings.py:273`) and nothing else exists — no box to write one on the
web, no list to read them back, and `get_profile` never returns them, so the
agent that writes the next CV never sees them. A lesson nobody re-reads is not
memory. This slice closes the loop: write it, read it, agent gets it.

Rule 5 check: could the agent do this alone? It can *write* a lesson
(`record_event`). It cannot *get them back* without us — that is the store
half we own. No new "do" tool.

## R1 — one read, two doors

`services/applications/lessons.py::list_lessons(user_id, *, limit, offset)`
— sync (pgsync), the same connection style as the profile read. Selects
`lesson` events for `user_id`, joined to `applications` for the job title and
company, newest `occurred_at` first, **excluding superseded events** (an event
that some other event `corrects_event_id` points at — the same rule as
`list_events_for_display`). Returns `(rows, total)`.

`LessonOut`: `event_id, application_id, job_title, job_company, detail,
occurred_at, recorded_by`.

Door 1 — `GET /api/applications/lessons?limit=&offset=` → `{lessons, total}`.
`require_user`; `limit` ≤ `LESSONS_PAGE_MAX` (env, default 100), default 50.
The web profile page reads this.

Door 2 — `ProfileResponse.lessons` = the last `PROFILE_LESSONS_MAX` (env,
default 20). `GET /profile` and MCP `get_profile` both carry it, from the same
function. The agent reads this before tailoring.

## R2 — the web writes and reads

- Application page, under the Timeline next to "Add a note": a **Flag for
  next time** box (`LessonForm`, same shape as `NoteForm`) that records a
  `lesson` event with the free text. `data-testid="lesson-input"` /
  `"lesson-submit"`.
- Profile page: a **Lessons** section (`LessonsList`) listing every lesson
  from door 1, newest first, each showing the text, the job (title · company)
  as a link to `/applications/{id}`, and the date. `data-testid="lesson-item"`.
  Empty state: one quiet line, no heading shouting (rule #29 spirit).
- The Timeline already labels `lesson` as "Lesson" (`event-labels.ts:61`).

## Security guardrails

- Per-user only: every query filters `user_id`; the route `Depends(require_user)`.
- Free text is length-capped by the existing `record_event` gate
  (`APPLICATION_EVENT_DETAIL_MAX_CHARS`); the read path never trusts the
  length. Rendered as text nodes only, never HTML.
- `limit`/`offset` are bounded ints (422 otherwise). No new write route, no
  new MCP tool, no migration (M3 untouched — nothing here updates or deletes).
- M5: no new tool, so no gate to re-apply; `get_profile` keeps its gate.

## Done when

- `tests/test_lessons.py`: a lesson recorded via `record_event` comes back
  from `GET /applications/lessons` with the right job title, from
  `GET /profile` `lessons`, and from MCP `get_profile`; a superseded lesson is
  excluded; a second user sees none; `limit` over the cap is 422; ordering is
  newest first; `PROFILE_LESSONS_MAX` caps door 2 but not door 1's `total`.
- Hermetic e2e: the application page's lesson box records a `lesson` event
  (mocked POST asserted); the profile page lists a mocked lesson linking to
  its application. Unit tests for both components.
- Types regenerated. Docs: VISION build-order line 9 marked shipped; roadmap
  row 9; `docs/README.md` slices row; ARCHITECTURE counts.
