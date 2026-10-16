// ---------------------------------------------------------------------------
// The extra words a timeline / feed row can say from the event's own payload:
// which document (kind + version) and which contact. Pure, no React. The plain
// label for each event type stays in event-labels.ts; this only adds detail.
//
// Payload shapes (backend, all optional here — never invent a missing one):
//   artifact_saved   { artifact_id, kind: cv|cover_letter|answers|outreach|story_bank|interview_transcript|redflag_analysis, version_no }
//   outreach_*       { contact_id, outreach_id, channel, contact_name? }
//   `contact_name` is joined in by the backend (caller's own contacts only).
// ---------------------------------------------------------------------------

import { eventLabel } from "@/lib/event-labels";

const KIND_LABEL: Record<string, string> = {
  cv: "CV",
  cover_letter: "Cover letter",
  answers: "Answers",
  outreach: "Outreach message",
  story_bank: "Story bank",
  interview_transcript: "Interview transcript",
  redflag_analysis: "Red-flag analysis",
};

function payloadOf(e: { payload?: unknown }): Record<string, unknown> {
  return e.payload && typeof e.payload === "object" && !Array.isArray(e.payload)
    ? (e.payload as Record<string, unknown>)
    : {};
}

/** "CV v2 saved", "Cover letter v1 saved", "Outreach message saved". `null`
 * when the payload names no known kind (the caller falls back to the label). */
export function artifactTitle(e: { payload?: unknown }): string | null {
  const p = payloadOf(e);
  const kind = typeof p.kind === "string" ? p.kind : "";
  if (!kind) return null;
  const name = KIND_LABEL[kind] ?? kind.replace(/_/g, " ");
  const v = typeof p.version_no === "number" && p.version_no > 0 ? p.version_no : null;
  // An outreach message is a message, not a numbered application document.
  return v && kind !== "outreach" ? `${name} v${v} saved` : `${name} saved`;
}

/** The contact's name when the payload carries one. */
export function contactNameOf(e: { payload?: unknown }): string | null {
  const n = payloadOf(e).contact_name;
  return typeof n === "string" && n.trim() ? n.trim() : null;
}

/** The one-line title for an event: the label, made specific when the payload
 * allows. Never claims more than the payload carries. */
export function eventTitle(e: { event_type: string; payload?: unknown; recorded_by?: string }): string {
  if (e.event_type === "artifact_saved") return artifactTitle(e) ?? eventLabel(e);
  if (e.event_type === "outreach_sent" || e.event_type === "outreach_replied") {
    const name = contactNameOf(e);
    return name ? `${eventLabel(e)} · ${name}` : eventLabel(e);
  }
  return eventLabel(e);
}
