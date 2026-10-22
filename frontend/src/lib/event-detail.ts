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
  skill_gap_report: "Skill gap report",
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

/** "85,000 GBP per year" from an offer's stored figures; null unless all three
 * are there (the backend only stores them together). */
export function offerFigures(e: { event_type: string; payload?: unknown }): string | null {
  if (e.event_type !== "offer") return null;
  const { offer_amount: amount, offer_currency: currency, offer_period: period } = payloadOf(e);
  if (typeof amount !== "number" || typeof currency !== "string" || typeof period !== "string") return null;
  return `${amount.toLocaleString("en-GB")} ${currency} per ${period}`;
}

/** "Technical · 45 min · Head office" from an interview event's stored details;
 * null when none of them were given. */
export function interviewDetails(e: { event_type: string; payload?: unknown }): string | null {
  if (e.event_type !== "interview_requested" && e.event_type !== "interview_scheduled") return null;
  const p = payloadOf(e);
  const parts: string[] = [];
  if (typeof p.round === "string" && p.round) {
    parts.push(p.round.replace(/-/g, " ").replace(/^./, (c) => c.toUpperCase()));
  }
  if (typeof p.duration_minutes === "number") parts.push(`${p.duration_minutes} min`);
  if (typeof p.location === "string" && p.location) parts.push(p.location);
  return parts.length ? parts.join(" · ") : null;
}

/** The one-line title for an event: the label, made specific when the payload
 * allows. Never claims more than the payload carries. */
const CONTACT_EVENTS = new Set([
  "outreach_sent",
  "outreach_replied",
  "contact_meeting",
  "contact_call",
  "contact_intro",
  "referral_asked",
  "referral_given",
]);

export function eventTitle(e: { event_type: string; payload?: unknown; recorded_by?: string }): string {
  if (e.event_type === "artifact_saved") return artifactTitle(e) ?? eventLabel(e);
  if (CONTACT_EVENTS.has(e.event_type)) {
    const name = contactNameOf(e);
    return name ? `${eventLabel(e)} · ${name}` : eventLabel(e);
  }
  return eventLabel(e);
}
