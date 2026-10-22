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
  "hired",
  "offer_declined",
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
  hired: "Hired",
  offer_declined: "Offer declined",
  rejected: "Rejected",
  withdrawn: "Withdrawn",
  ghosted: "Ghosted",
};

/** Display copy for APPLICATION_NOTE_EVENT_TYPES (settings.py) — the
 * non-status ("note-family") event types. */
export const EVENT_LABEL: Record<string, string> = {
  fit_judged: "Fit judged",
  artifact_saved: "Document saved",
  contact_added: "Contact added",
  outreach_sent: "Outreach sent",
  note: "Note",
  lesson: "Lesson",
  post_mortem: "Post-mortem: what to learn from this",
  outreach_replied: "Reply received to your outreach",
  // Needs-you asks.
  asked: "Assistant asked you a question",
  answered: "You answered a question",
  ask_withdrawn: "Question withdrawn",
  // Per-job submit switch.
  submit_mode_set: "Sending setting changed for this job",
  // Application kit trail (human-in-the-loop before sending).
  kit_read: "Assistant opened your application kit",
  cv_seen: "CV checked",
  submit_approved: "You said yes to sending",
  submit_declined: "You said don't send",
  autofill_set: "Autofill setting changed",
  duplicate_cleared: "Possible duplicate cleared",
  form_filled: "Form filled",
  form_swept: "Form double-checked before sending",
  form_checked: "Form questions checked against your saved answers",
  hold_released: "Hold on sending lifted",
  site_account: "Account on the employer site noted",
  account_needed: "Employer site needs an account",
  // S6 blocked record.
  blocked: "Assistant got stuck",
  unblocked: "Unblocked",
  // S7 proof of application.
  proof_text: "Saved the confirmation page text",
  proof_screenshot: "Saved a screenshot of the confirmation",
  // Automatic employer emails (sent by the assistant in the owner's name).
  checkin_sent: "Check-in email sent to the employer",
  feedback_asked: "Asked the employer for feedback",
  feedback_followup_sent: "Follow-up email sent to the employer",
  auto_email_skipped: "Automatic email skipped",
  // Simulated/test application marker.
  simulation: "Marked as a test (not a real application)",
  // Feedback loop slice 4 (interview side).
  interview_session: "Interview round debriefed",
  process_friction: "Hiring-process friction noted",
  assessment: "Skills test logged",
  // Batch queue (2026-10-18).
  queued: "Added to the batch",
  unqueued: "Removed from the batch",
  // Bulk-apply D1 (2026-10-18).
  submit_attempted: "Submit started",
  submit_resolved: "Submit marked as not sent",
  // Bulk-apply F4 (2026-10-18).
  answer_corrected: "Answer corrected by you",
  // Outreach pack #5/#6 (2026-10-20): contact interactions and referrals.
  contact_meeting: "Meeting with a contact",
  contact_call: "Call with a contact",
  contact_intro: "Introduced to a contact",
  referral_asked: "Asked for a referral",
  referral_given: "Referral given",
  // Gap roadmap slice 29: the exact file sent (server-written).
  document_uploaded: "Document file sent",
};

function relayedWords(payload: unknown): boolean {
  const w = (payload as { user_words?: unknown } | null | undefined)?.user_words;
  return typeof w === "string" && w.trim().length > 0;
}

/** The label for one timeline row. A status event reads "Status → <label>";
 * a note-family event reads its own label; anything unrecognised (a future
 * `APPLICATION_EXTRA_EVENT_TYPES` addition, or drift) falls back to the raw
 * `event_type` string rather than hiding the row. */
export function eventLabel(e: { event_type: string; payload?: unknown; recorded_by?: string }): string {
  if (e.event_type === "answered" && relayedWords(e.payload)) {
    // The assistant typed it, but the words are the user's (answer_ask user_words).
    const via = e.recorded_by && e.recorded_by !== "web" ? whoLabel(e.recorded_by).name : "";
    return via ? `Your answer (via ${via})` : "Your answer (via your assistant)";
  }
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
 * render as "Assistant" — the distinction is internal auth plumbing, not
 * something the seeker needs to see. The `who` value stays the internal word
 * "agent" (not renamed — it is code, never shown), only the display string in
 * `WhoChip` changed.
 */
/** Plain-words "who recorded it" line for the Timeline: "you", "your
 * assistant (via <client>)" or "Job360" (the server itself). */
export function actorLabel(recordedBy: string): string {
  if (recordedBy === "system" || recordedBy === "job360") return "Job360";
  const { who, name } = whoLabel(recordedBy);
  return who === "you" ? "you" : `your assistant (via ${name})`;
}

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
