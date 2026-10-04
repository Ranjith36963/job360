// ---------------------------------------------------------------------------
// One plain sentence per event, for the home page's "What your assistant did"
// feed and the ledger's "last thing that happened" column (redesign slice 2).
//
// The event-type vocabulary is closed in the BACKEND (settings.py:
// APPLICATION_STATUS_EVENT_TYPES + APPLICATION_NOTE_EVENT_TYPES). This is
// display copy only: an unknown type (APPLICATION_EXTRA_EVENT_TYPES, or
// drift) falls back to its own name, humanised — never hidden, never guessed.
// ---------------------------------------------------------------------------

const PHRASE: Record<string, string> = {
  brought: "Saved the job",
  applied: "Recorded that you applied",
  replied: "Logged a reply",
  interview_requested: "Logged an interview request",
  interview_scheduled: "Logged an interview",
  interview_done: "Marked the interview done",
  offer: "Logged an offer",
  rejected: "Logged a rejection",
  withdrawn: "Marked the application withdrawn",
  ghosted: "Marked it as gone quiet",
  fit_judged: "Judged the fit",
  artifact_saved: "Saved a document",
  contact_added: "Added a contact",
  outreach_sent: "Recorded a message sent",
  outreach_replied: "Logged a reply to a message",
  note: "Added a note",
  lesson: "Flagged a lesson for next time",
  asked: "Asked you a question",
  answered: "Recorded an answer",
  ask_withdrawn: "Withdrew a question",
};

/** "interview_requested" → "Interview requested". */
function humanise(type: string): string {
  const words = type.replace(/[_-]+/g, " ").trim();
  return words ? words[0].toUpperCase() + words.slice(1) : "Something happened";
}

/** The phrase for the type, then the event's own detail when it has one.
 * `detail` is written by the assistant or the user — untrusted; callers
 * render the result as a React text node only. */
export function describeEvent(e: { event_type: string; detail?: string | null }): string {
  const phrase = PHRASE[e.event_type] ?? humanise(e.event_type);
  const detail = (e.detail ?? "").trim();
  return detail ? `${phrase}: ${detail}` : `${phrase}.`;
}
