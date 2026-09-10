// ---------------------------------------------------------------------------
// One label module for the application event feed (agentic UX audit, fix 3).
//
// The event-type vocabulary is closed in the BACKEND, not here
// (backend/src/core/settings.py: APPLICATION_STATUS_EVENT_TYPES,
// APPLICATION_NOTE_EVENT_TYPES). This file is display copy only — it must
// cover every member of both tuples, never invent a third vocabulary.
//
// A status event's underlying application status is the event_type ITSELF —
// there is no `new_status` field in the payload. The one exception is
// `brought`, which maps to `considering`
// (backend/src/services/applications/status.py: `_EVENT_TO_STATUS`). Every
// other status event's name IS the status.
// ---------------------------------------------------------------------------

/** APPLICATION_STATUS_EVENT_TYPES (settings.py) — the closed set of
 * status-bearing event types, in the application's own status vocabulary. */
const STATUS_EVENT_TYPES = new Set([
  "brought",
  "applied",
  "replied",
  "interview_requested",
  "interview_scheduled",
  "interview_done",
  "offer",
  "rejected",
  "withdrawn",
  "ghosted",
]);

/** `status.py`'s `_EVENT_TO_STATUS` — the one status event whose name is NOT
 * the status. Every other status event's name IS the status. */
const EVENT_TYPE_TO_STATUS: Record<string, string> = {
  brought: "considering",
};

/** Display copy for the application's `status` column (ApplicationSummaryOut
 * / ApplicationDetailOut.status), keyed by every value `status_for_event` can
 * ever produce. */
export const STATUS_LABEL: Record<string, string> = {
  considering: "Considering",
  applied: "Applied",
  replied: "Replied",
  interview_requested: "Interview requested",
  interview_scheduled: "Interview scheduled",
  interview_done: "Interview done",
  offer: "Offer",
  rejected: "Rejected",
  withdrawn: "Withdrawn",
  ghosted: "Ghosted",
};

/** Display copy for APPLICATION_NOTE_EVENT_TYPES (settings.py) — the
 * non-status ("note-family") event types. */
export const EVENT_LABEL: Record<string, string> = {
  fit_judged: "Fit judged",
  artifact_saved: "Artifact saved",
  contact_added: "Contact added",
  outreach_sent: "Outreach sent",
  note: "Note",
  lesson: "Lesson",
};

/** The label for one timeline row. A status event reads "Status → <label>";
 * a note-family event reads its own label; anything unrecognised (a future
 * `APPLICATION_EXTRA_EVENT_TYPES` addition, or drift) falls back to the raw
 * `event_type` string rather than hiding the row. */
export function eventLabel(e: { event_type: string; payload?: unknown }): string {
  if (STATUS_EVENT_TYPES.has(e.event_type)) {
    const status = EVENT_TYPE_TO_STATUS[e.event_type] ?? e.event_type;
    return `Status → ${STATUS_LABEL[status] ?? status}`;
  }
  return EVENT_LABEL[e.event_type] ?? e.event_type;
}

/** Who did this — for the Timeline's "recorded by" chip.
 *
 * `recorded_by` has exactly three shapes (backend
 * `src/services/applications/authorship.py::actor_for`):
 * `"web"` (the seeker, in the browser), `"token:<name>"` (a personal-token
 * MCP client), `"agent:<client>"` (an OAuth-grant MCP client). Both token and
 * agent forms are an autonomous caller acting on the user's behalf, so both
 * render as "Agent" — the distinction is internal auth plumbing, not
 * something the seeker needs to see.
 */
export function whoLabel(recordedBy: string): { who: "you" | "agent"; name: string } {
  if (recordedBy === "web") {
    return { who: "you", name: "You" };
  }
  if (recordedBy.startsWith("token:")) {
    return { who: "agent", name: recordedBy.slice("token:".length) };
  }
  if (recordedBy.startsWith("agent:")) {
    return { who: "agent", name: recordedBy.slice("agent:".length) };
  }
  return { who: "agent", name: recordedBy };
}
