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

// The browser fields live behind one adapter (the Browser agent owns their names).
import { needsOwnBrowser } from "@/lib/browser-contract";
export { needsOwnBrowser };

export { JOB360_BROWSER_LABEL, OWN_BROWSER_LABEL } from "@/lib/browser-labels";

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
  /** How many of this card's open asks must run in the owner's own browser. */
  ownBrowserCount: number;
  /** True when any of them does. */
  ownBrowser: boolean;
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
    ownBrowserCount: sorted.filter(needsOwnBrowser).length,
    ownBrowser: sorted.some(needsOwnBrowser),
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

// ---- Form answers, each question once (owner 2026-10-25) -------------------------------------

export type FormQuestion = NonNullable<Ask["questions"]>[number];

/** One form question on a card, however many open asks listed it. */
export type MergedRow = {
  /** Identity inside the card (the question's key, or its normalised text when it has no key). */
  id: string;
  /** The version shown: the one with a real source and an answer. */
  q: FormQuestion;
  /** The ask whose copy is shown; row test ids are `q-<askId>-<q.key>`. */
  askId: number;
  /** Every ask that listed this question. */
  askIds: number[];
};

export type MergedQuestions = {
  rows: MergedRow[];
  /** Per ask: each of its own keys and the merged row that now carries the answer. */
  byAsk: Map<number, { key: string; rowId: string }[]>;
};

const SOURCE_RANK: Record<string, number> = { profile: 6, memory: 5, cv: 4, draft: 3, guess: 2, none: 1 };

function normText(s: string): string {
  return s.toLowerCase().replace(/\s+/g, " ").trim();
}

function betterThan(a: FormQuestion, b: FormQuestion): boolean {
  const ae = a.answer.trim() ? 1 : 0;
  const be = b.answer.trim() ? 1 : 0;
  if (ae !== be) return ae > be;
  return (SOURCE_RANK[a.source] ?? 0) > (SOURCE_RANK[b.source] ?? 0);
}

/** The questions of every structured ask, deduped by key (else by question text). Several open asks
 *  on one job often list the same questions; each is shown once and every ask is still answered. */
export function mergeQuestions(asks: Ask[]): MergedQuestions {
  const rows: MergedRow[] = [];
  const byId = new Map<string, MergedRow>();
  const byText = new Map<string, MergedRow>();
  const byAsk = new Map<number, { key: string; rowId: string }[]>();
  for (const ask of asks) {
    const mine: { key: string; rowId: string }[] = [];
    byAsk.set(ask.id, mine);
    for (const q of ask.questions ?? []) {
      const text = normText(q.question);
      const id = q.key.trim() ? q.key : text;
      let row = byId.get(id) ?? (text ? byText.get(text) : undefined);
      if (!row) {
        row = { id, q, askId: ask.id, askIds: [ask.id] };
        rows.push(row);
        byId.set(id, row);
        if (text) byText.set(text, row);
      } else {
        if (!row.askIds.includes(ask.id)) row.askIds.push(ask.id);
        if (betterThan(q, row.q)) {
          row.q = q;
          row.askId = ask.id;
        }
      }
      mine.push({ key: q.key, rowId: row.id });
    }
  }
  // An empty answer to a plain consent / yes-no question is pre-guessed (shown as Guessed).
  for (const row of rows) {
    if (row.q.answer.trim()) continue;
    const guess = fallbackGuess(row.q);
    if (guess) row.q = { ...row.q, answer: guess, source: "guess" };
  }
  return { rows, byAsk };
}

// Owner 2026-10-25: only two states. "Verified" = the owner said it or it is a saved fact;
// "Guessed" = the assistant's best guess, one click to confirm.
const VERIFIED_SOURCES: readonly string[] = ["profile", "memory", "cv", "owner", "user", "answered", "confirmed"];
/** Sources whose answer is already stored on the profile, so approving them unchanged saves nothing new. */
const STORED_SOURCES: readonly string[] = ["profile", "memory", "cv"];

export type AnswerState = "Verified" | "Guessed";

export function isVerifiedSource(source: string): boolean {
  return VERIFIED_SOURCES.includes(source);
}

export function isStoredSource(source: string): boolean {
  return STORED_SOURCES.includes(source);
}

/** A row's first state: a Verified answer starts checked, a Guessed or empty one does not. `source`
 *  is written by the assistant (untrusted), so a consent or personal-category question never starts
 *  checked whatever its source: only the owner's own click checks it. */
export function initialRow(q: { key: string; question: string; answer: string; source: string; sensitive?: boolean }): {
  value: string;
  ok: boolean;
} {
  return { value: q.answer, ok: isVerifiedSource(q.source) && q.answer.trim().length > 0 && !isSensitiveQuestion(q) };
}

const NEGATED = /\b(?:object|withdraw|opt[- ]?out|decline|refuse|do not|don't)\b/i;
const CONSENT =
  /\bi (?:agree|accept|acknowledge|consent)\b|\b(?:consent|agree)\b[^?\n]{0,60}\b(?:data|privacy|personal information|processing|gdpr|terms|policy|retain|stored?)\b|\b(?:data processing|privacy (?:policy|notice|statement)|gdpr)\b/i;
const REFERRED =
  /\breferred\b[^?\n]{0,40}\b(?:employee|employees|staff|team member)\b|\b(?:employee|staff)\b[^?\n]{0,30}\breferr/i;

/** A sensible answer for a plain consent or "referred by an employee?" question, else "". With
 *  choices, the matching choice's own text; none of them matching means no guess. */
export function fallbackGuess(q: { key: string; question: string; choices?: string[] | null }): string {
  const text = `${q.key.replace(/[_-]+/g, " ")} ${q.question}`;
  let want = "";
  if (REFERRED.test(text)) want = "No";
  else if (CONSENT.test(text) && !NEGATED.test(text)) want = "Yes";
  if (!want) return "";
  if (!q.choices?.length) return want;
  const re = want === "Yes" ? /^yes\b/i : /^no\b/i;
  return q.choices.find((c) => re.test(c.trim())) ?? "";
}

const SENSITIVE =
  /consent|\bagree|privacy|gdpr|data processing|\bterms\b|\beeo|\bgender|\bsex\b|\bsexual|ethnic|\brace\b|disabilit|veteran|religio|criminal|convict|marital|pregnan|date of birth|birth ?date|\bdob\b/i;

/** Consent and personal-category questions: shown pre-filled, but only the owner's own click checks them. */
export function isSensitiveQuestion(q: { key: string; question: string; sensitive?: boolean }): boolean {
  if (q.sensitive === true) return true;
  return SENSITIVE.test(`${q.key.replace(/[_-]+/g, " ")} ${q.question}`);
}

/** One short line saying what waits on a job, from its open asks (owner 2026-10-25). Up to three parts,
 *  in this order: CAPTCHA, sign in, CV upload, a step on the form, answers to check, notes to send.
 *  `peopleCount` (people with an unsent drafted note) adds to the notes; without it and with no asks,
 *  a people-only header reads "Send notes". */
export function waitSummary(asks: Ask[], peopleCount?: number): string {
  const kinds = asks.map(rowKind);
  const has = (k: RowKind) => kinds.includes(k);
  const answers =
    mergeQuestions(asks.filter((a) => rowKind(a) === "structured")).rows.length +
    kinds.filter((k) => k === "question" || k === "yes_no").length;
  const notes = kinds.filter((k) => k === "outreach").length + (peopleCount ?? 0);
  const parts: string[] = [];
  if (has("captcha")) parts.push("Solve a CAPTCHA");
  if (has("login")) parts.push("Sign in");
  if (has("upload")) parts.push("Attach your CV");
  if (has("safety")) parts.push("Finish a step on the form");
  if (answers > 0) parts.push(`Check ${answers} ${answers === 1 ? "answer" : "answers"}`);
  if (notes > 0) parts.push(`Send ${notes} ${notes === 1 ? "note" : "notes"}`);
  else if (asks.length === 0 && peopleCount === undefined) parts.push("Send notes");
  return parts.slice(0, 3).join(" · ");
}

const HEAD_MAX = 140;

/** An ask's text as a one-line action and the rest: the head is the first sentence or line (cut at
 *  ". ", "? ", "! " or a newline, at most 140 characters, "…" when cut mid-sentence); the rest is
 *  everything after it (empty = nothing to hide). Plain text only. */
export function splitAskText(text: string): { head: string; rest: string } {
  const t = (text ?? "").trim();
  const m = /[.?!](?=\s)|\n/.exec(t);
  let cut = m ? (t[m.index] === "\n" ? m.index : m.index + 1) : t.length;
  let head = t.slice(0, cut).trim();
  if (head.length > HEAD_MAX) {
    cut = HEAD_MAX - 1;
    head = `${t.slice(0, cut).trimEnd()}…`;
  }
  return { head, rest: t.slice(cut).trim() };
}
