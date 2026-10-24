import type { ApplicationDetail, Ask, Contact } from "@/lib/api";

/**
 * Needs you, one card per job (owner decision 2026-10-24). Pure helpers that turn the open asks
 * and each job's application record into the card's rows. Every string here is untrusted text
 * written by an assistant; it is only ever rendered as React text.
 */

export type BlockerKind = "captcha" | "upload" | "login" | "safety";
// "structured": an ask that lists its form questions (one row each, with where each answer came from).
export type RowKind = BlockerKind | "outreach" | "yes_no" | "question" | "structured";
const BLOCKER_KINDS: readonly string[] = ["captcha", "upload", "login", "safety"];

const BLOCKER_RULES: [BlockerKind, RegExp][] = [
  ["captcha", /captcha|i'?m not a robot|human check|bot check|solve the check/i],
  ["upload", /\bupload|\battach (?:the |your |my )?(?:cv|resume|file)|file (?:input|picker)/i],
  ["login", /\blog ?in\b|\bsign ?in\b|password|one-time code|verification code|\botp\b/i],
  ["safety", /safety (?:block|check|stop)|\bblocked\b/i],
];
const OUTREACH = /linkedin (?:message|note|dm)|connection (?:request|note)|send (?:this|the) (?:message|note|dm)/i;
const SIMULATION = /\bsimulation\b|lifecycle test|do not apply/i;
const YES_NO = /^(?:do|does|did|are|is|was|were|can|could|will|would|have|has|had|should)\b[^?\n]*\?$/i;
// "...? Yes or No?" / "Reply yes or no." at the end of a longer question.
const ENDS_YES_NO = /\byes or no[?.]?\s*$/i;

export function rowKind(ask: Ask): RowKind {
  // The kind the assistant stored wins; only an older ask (kind "") is read from its words.
  if (BLOCKER_KINDS.includes(ask.kind ?? "")) return ask.kind as BlockerKind;
  if (ask.questions?.length) return "structured";
  if (ask.kind === "question") return "question";
  const text = `${ask.question}\n${ask.context}`;
  for (const [kind, re] of BLOCKER_RULES) if (re.test(text)) return kind;
  if (OUTREACH.test(text)) return "outreach";
  const q = ask.question.trim();
  return (q.length <= 200 && YES_NO.test(q)) || ENDS_YES_NO.test(q) ? "yes_no" : "question";
}

export function isBlocker(kind: RowKind): kind is BlockerKind {
  return kind === "captcha" || kind === "upload" || kind === "login" || kind === "safety";
}

/** A practice ask (a SIMULATION run, a test job) never belongs on the real list. */
export function isSimulation(ask: Ask): boolean {
  return [ask.question, ask.context, ask.job_title ?? "", ask.job_company ?? ""].some((t) => SIMULATION.test(t));
}

export const BLOCKER_TITLE: Record<BlockerKind, string> = {
  captcha: "Solve the CAPTCHA on the form",
  upload: "Attach your CV on the form",
  login: "Sign in on the job site",
  safety: "Finish this step on the form yourself",
};

/** Someone on this job with a drafted message that has not been marked sent since. */
export type PersonToReach = { contact: Contact; message: string; linkedinUrl: string };

export function linkedinFor(contact: Contact, company: string): string {
  const url = contact.linkedin_url.trim();
  if (/^https:\/\/([a-z0-9-]+\.)?linkedin\.com\//i.test(url)) return url;
  const words = [contact.name, contact.company || company].filter(Boolean).join(" ");
  return `https://www.linkedin.com/search/results/people/?keywords=${encodeURIComponent(words)}`;
}

export function peopleToReach(detail: ApplicationDetail): PersonToReach[] {
  const out: PersonToReach[] = [];
  for (const c of detail.contacts) {
    const messages = c.outreach.messages;
    const last = messages[messages.length - 1];
    if (!last || !last.text.trim()) continue;
    const sent = c.outreach.last_sent;
    if (sent && sent.occurred_at >= last.occurred_at) continue;
    out.push({ contact: c, message: last.text, linkedinUrl: linkedinFor(c, detail.job.job_company) });
  }
  return out;
}

export type JobCardData = {
  applicationId: number;
  company: string;
  role: string;
  formUrl: string;
  asks: { ask: Ask; kind: RowKind }[];
  people: PersonToReach[];
  /** The newest CV version still waiting for the user's check, if any. */
  cv: { artifactId: number; version: number } | null;
  /** The newest CV version stored for this job (the file a blocked upload needs). */
  latestCv: { artifactId: number; version: number } | null;
  formFilled: boolean;
  oldestAskAt: string;
};

/** Open asks grouped by job (one card each), the rest (no job) left over. Practice asks dropped. */
export function groupAsks(open: Ask[]): { byJob: Map<number, Ask[]>; general: Ask[] } {
  const byJob = new Map<number, Ask[]>();
  const general: Ask[] = [];
  for (const ask of open) {
    if (isSimulation(ask)) continue;
    if (ask.application_id == null) general.push(ask);
    else byJob.set(ask.application_id, [...(byJob.get(ask.application_id) ?? []), ask]);
  }
  return { byJob, general };
}

export function buildCard(
  detail: ApplicationDetail,
  asks: Ask[],
  cv: { artifactId: number; version: number } | null,
): JobCardData {
  const sorted = [...asks].sort((a, b) => a.asked_at.localeCompare(b.asked_at));
  const cvs = detail.artifacts.filter((a) => a.kind === "cv").sort((a, b) => b.version_no - a.version_no);
  return {
    applicationId: detail.id,
    company: detail.job.job_company,
    role: detail.job.job_title,
    formUrl: /^https?:\/\//i.test(detail.job.job_url) ? detail.job.job_url : "",
    asks: sorted.map((ask) => ({ ask, kind: rowKind(ask) })),
    people: peopleToReach(detail),
    cv,
    latestCv: cvs[0] ? { artifactId: cvs[0].id, version: cvs[0].version_no } : null,
    formFilled: detail.form_state === "filled",
    oldestAskAt: sorted[0]?.asked_at ?? detail.updated_at,
  };
}

/** Most urgent first: a blocker on a filled form, then any blocker, then questions, then the rest;
 *  the job waiting longest first within each. */
export function sortCards(cards: JobCardData[]): JobCardData[] {
  const rank = (c: JobCardData) => {
    const blocked = c.asks.some((a) => isBlocker(a.kind));
    if (blocked && c.formFilled) return 0;
    if (blocked) return 1;
    return c.asks.length > 0 ? 2 : 3;
  };
  return [...cards].sort((a, b) => rank(a) - rank(b) || a.oldestAskAt.localeCompare(b.oldestAskAt));
}

/** Owner 2026-10-24: the People row says whether the user is connected to this person on LinkedIn,
 *  which decides the draft (connected: a message; not connected: a short connection note). */
export type LinkedinState = "connected" | "not_connected" | "pending" | "unknown";
export const NOTE_MAX_CHARS = 200; // LinkedIn's connection-note limit on a free account (300 with Premium)

export function linkedinState(raw: string | null | undefined): LinkedinState {
  return raw === "connected" || raw === "not_connected" || raw === "pending" ? raw : "unknown";
}

export const LINKEDIN_STATE_LABEL: Record<LinkedinState, string> = {
  connected: "Connected on LinkedIn",
  not_connected: "Not connected: send a connection note, at most 200 characters (300 with Premium)",
  pending: "Invite pending: nothing to send until they accept",
  unknown: "Are you connected on LinkedIn?",
};
/** Owner 2026-10-24: an old "send this LinkedIn message to <name> yourself" ask about someone already
 *  listed under People to reach is the same action twice. For each reminder ask, the ids of the people
 *  on this card it names (by full name, any case). A reminder naming someone here is not shown as its
 *  own row; it is answered when the last person it names is marked sent or skipped. */
export function remindersCovered(people: PersonToReach[], reminders: Ask[]): Map<number, number[]> {
  const out = new Map<number, number[]>();
  for (const r of reminders) {
    const text = `${r.question}\n${r.context}`.toLowerCase();
    const named = people.filter((p) => p.contact.name.trim().length > 2 && text.includes(p.contact.name.trim().toLowerCase()));
    if (named.length > 0) out.set(r.id, named.map((p) => p.contact.id));
  }
  return out;
}
