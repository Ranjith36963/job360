// One label system for every answer row, on the job card (open asks) and the ready card (a filled
// form): one status word out of three, plus one line saying where the value came from.
//   Needs you - nothing usable to send yet, or only the user's own tap may set it.
//   Check     - the assistant filled it; sending waits for the user's OK.
//   OK        - the user's own word, or a saved fact. Nothing to do.
// Pure: no fetching, no React. Every value read here was written by an assistant (untrusted) and
// only ever decides which fixed words show; it is never rendered as markup.

import type { ReadyAnswer, ReadyCardData, ReadyFlag } from "@/lib/api";
import { looksLikePlaceholder } from "@/lib/answer-text";
import { whoLabel } from "@/lib/event-labels";
import { isSensitiveQuestion, isStoredSource } from "@/lib/job-cards";
import { dayMonth } from "@/lib/ready-to-send";

export type AnswerStatus = "needs_you" | "check" | "ok";
export type AnswerLabel = { status: AnswerStatus; source: string };

export const STATUS_WORD: Record<AnswerStatus, string> = { needs_you: "Needs you", check: "Check", ok: "OK" };

export const SOURCE = {
  profile: "From your profile",
  cv: "From your CV",
  memory: "From your Memory",
  memoryByAssistant: "From your Memory · your assistant saved it",
  typedLater: "From your Memory · will be typed before sending",
  guess: "Your assistant's guess",
  written: "Written by your assistant",
  you: "You wrote this",
  confirmed: "You confirmed this",
  approved: "You approved this",
  changed: "You changed this",
  notKnown: "Your assistant could not guess this",
  pending: "Your assistant will draft this, or write it now",
  stub: "Draft missing, write it or ask again",
  blank: "Blank on the form",
  hidden: "Typed on the form, hidden for privacy",
  onlyYou: "only you can choose",
} as const;

const OWN_WORD_SOURCES: readonly string[] = ["owner", "user", "answered", "confirmed"];

function withDate(text: string, iso: string | null | undefined): string {
  const d = dayMonth(iso);
  return d ? `${text} · saved ${d}` : text;
}

/** The value an answer row stands for, where it came from. */
function storedSource(source: string, savedAt?: string | null): string {
  if (source === "profile") return SOURCE.profile;
  if (source === "cv") return SOURCE.cv;
  return withDate(SOURCE.memory, savedAt);
}

/** An open ask's row: the status comes from where the value came from and whether it is a
 *  sensitive question (consent, personal categories), as the job card has always read it. */
export function askRowLabel(
  q: { key: string; question: string; answer: string; source: string; choices?: string[] | null; remembered_on?: string | null; sensitive?: boolean },
  row: { value: string; ok: boolean },
  editing = false,
): AnswerLabel {
  const value = row.value.trim();
  const original = q.answer.trim();
  if (value === "") {
    const source = q.source === "not_known" || q.choices?.length ? SOURCE.notKnown : SOURCE.pending;
    return { status: "needs_you", source };
  }
  if (!editing && !q.choices?.length && looksLikePlaceholder(q.question, row.value)) {
    return { status: "needs_you", source: SOURCE.stub };
  }
  const unchanged = value === original;
  let source: string;
  if (row.ok && original !== "" && !unchanged) source = SOURCE.changed;
  else if (unchanged && isStoredSource(q.source)) source = storedSource(q.source, q.remembered_on);
  else if (unchanged && OWN_WORD_SOURCES.includes(q.source)) source = SOURCE.you;
  else if (row.ok && original === "") source = SOURCE.you;
  else if (row.ok) source = SOURCE.confirmed;
  else if (q.source === "draft") source = SOURCE.written;
  else source = SOURCE.guess;
  // A choice row shows no pressed button until the user's own tap: the value it holds is said here.
  if (!row.ok && q.choices?.length) source = `${source}: ${value}`;
  if (isSensitiveQuestion(q) && !row.ok) return { status: "needs_you", source: `${source} · ${SOURCE.onlyYou}` };
  if (row.ok) return { status: "ok", source };
  const saved = unchanged && (isStoredSource(q.source) || OWN_WORD_SOURCES.includes(q.source));
  return { status: saved ? "ok" : "check", source };
}

/** Flag codes on a ready card that keep the safety check from sending (backend `ready.py`). */
const NEEDS_YOU_FLAGS: readonly string[] = ["sensitive", "blank"];
const CHECK_FLAGS: readonly string[] = ["guessed", "unapproved"];

/** The card's flags that belong to this answer: by its key, or, for a row with no key, by the
 *  key-less flag that names its question (the backend writes "<reason>: <question>"). */
export function flagsFor(a: Pick<ReadyAnswer, "key" | "question">, flags: ReadyFlag[]): ReadyFlag[] {
  return flags.filter((f) => (a.key ? f.key === a.key : !f.key && f.text.endsWith(`: ${a.question}`)));
}

/** A placeholder that only describes a draft (and was not replaced by a saved or edited answer). */
export function isDraftStub(a: ReadyAnswer): boolean {
  return a.changed_to == null && !a.redacted && looksLikePlaceholder(a.question, a.answer);
}

/** Memory written by an assistant, not yet confirmed by the user: OK (it blocks nothing), with a confirm button. */
export function isAssistantMemory(a: ReadyAnswer): boolean {
  return a.source === "memory" && !a.verified && !!a.saved_by && whoLabel(a.saved_by).who === "agent";
}

/** A ready card's row. The status comes from the backend flags for this row - the same reading
 *  the send check uses - never from `source` alone. */
export function readyRowLabel(a: ReadyAnswer, card: Pick<ReadyCardData, "flags">): AnswerLabel {
  if (a.changed_to != null) {
    return { status: "ok", source: a.changed_reason === "owner_edit" ? SOURCE.changed : SOURCE.typedLater };
  }
  if (isDraftStub(a)) return { status: "needs_you", source: SOURCE.stub };
  const codes = flagsFor(a, card.flags).map((f) => f.code);
  if (!a.redacted && !a.answer.trim()) return { status: "needs_you", source: SOURCE.blank };
  let source: string;
  if (a.redacted) source = SOURCE.hidden;
  else if (codes.includes("guessed")) source = SOURCE.guess;
  else if (codes.includes("unapproved")) source = SOURCE.written;
  else if (a.source === "memory") source = isAssistantMemory(a) ? SOURCE.memoryByAssistant : withDate(SOURCE.memory, a.saved_at);
  else if (a.source === "profile") source = SOURCE.profile;
  else if (a.source === "approved") source = dayMonth(a.saved_at) ? `${SOURCE.approved} · ${dayMonth(a.saved_at)}` : SOURCE.approved;
  else if (a.verified) source = SOURCE.confirmed;
  else source = a.source === "written" ? SOURCE.written : SOURCE.guess;
  if (codes.some((c) => NEEDS_YOU_FLAGS.includes(c))) {
    return { status: "needs_you", source: codes.includes("sensitive") ? `${source} · ${SOURCE.onlyYou}` : source };
  }
  if (codes.some((c) => CHECK_FLAGS.includes(c))) return { status: "check", source };
  // No flag matched, but the row is still an unconfirmed guess or draft: never say OK beside
  // "Your assistant's guess" - the send check refuses it either way.
  if (source === SOURCE.guess || source === SOURCE.written) return { status: "check", source };
  return { status: "ok", source };
}
