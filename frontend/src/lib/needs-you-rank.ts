// "Most urgent first" on Needs you (owner 2026-10-26): one rank per job, from what the page already
// holds before any job record loads (the open asks, the ready-to-send card, a CV to approve, people).
// Pure: no fetching, no React. Every value read here was written by an assistant (untrusted) and
// only decides an order; nothing here is rendered.

import type { Ask, ReadyCardData } from "@/lib/api";
import { askRowLabel, isDraftStub, readyRowLabel } from "@/lib/answer-status";
import { initialRow, isBlocker, mergeQuestions, rowKind } from "@/lib/job-cards";

/** 1 a held form waiting for a human step; 2 ready to send, nothing blocking; 3 stuck on you;
 *  4 quick checks (Check answers, a CV to approve); 5 people or notes only. */
export type JobTier = 1 | 2 | 3 | 4 | 5;

export type JobRankInput = {
  asks: Ask[];
  ready: ReadyCardData | null;
  /** When the CV waiting for approval was saved; null = no CV to approve. */
  cvSavedAt: string | null;
};

/** Flag codes the send check reads as "the user must check this" (the rest need the user, but
 *  "changed" is the user's own edit and blocks nothing). Matches lib/answer-status. */
const CHECK_FLAGS: readonly string[] = ["guessed", "unapproved", "not_in_memory"];

/** What the ready card still waits on: nothing, a Check, or the user (Needs you). */
function readyNeeds(c: ReadyCardData): "none" | "check" | "needs_you" {
  const codes = c.flags.map((f) => f.code).filter((code) => code !== "changed");
  const rows = c.answers.map((a) => (isDraftStub(a) ? "needs_you" : readyRowLabel(a, c).status));
  if (rows.includes("needs_you") || codes.some((code) => !CHECK_FLAGS.includes(code))) return "needs_you";
  return rows.includes("check") || codes.length > 0 ? "check" : "none";
}

/** What the job's open asks wait on, each form question read as the job card reads it. */
function asksNeed(asks: Ask[]): "none" | "check" | "needs_you" {
  const kinds = asks.map(rowKind);
  if (kinds.some((k) => isBlocker(k) || k === "question" || k === "yes_no")) return "needs_you";
  const structured = asks.filter((a, i) => kinds[i] === "structured");
  const merged = mergeQuestions(structured);
  const saved = new Set<string>();
  for (const a of structured)
    for (const k of merged.byAsk.get(a.id) ?? []) if (String(a.answers?.[k.key] ?? "").trim()) saved.add(k.rowId);
  const words = merged.rows.map((m) => (saved.has(m.id) ? "ok" : askRowLabel(m.q, initialRow(m.q)).status));
  if (words.includes("needs_you")) return "needs_you";
  return words.includes("check") ? "check" : "none";
}

export function jobTier(j: JobRankInput): JobTier {
  if (j.asks.some((a) => (a.held_seconds ?? 0) > 0) || (j.ready?.held_seconds ?? 0) > 0) return 1;
  const fromAsks = asksNeed(j.asks);
  const fromReady = j.ready ? readyNeeds(j.ready) : "none";
  if (j.ready && fromReady === "none" && fromAsks === "none" && !j.cvSavedAt) return 2;
  if (fromAsks === "needs_you" || fromReady === "needs_you") return 3;
  if (fromAsks === "check" || fromReady === "check" || j.cvSavedAt) return 4;
  return 5;
}

/** The newest open item on the job (an ask, the fill, the CV); "" when none has a date. */
export function newestAt(j: JobRankInput): string {
  const dates = [...j.asks.map((a) => a.asked_at), j.ready?.filled_at, j.cvSavedAt];
  let best = "";
  let bestMs = -Infinity;
  for (const d of dates) {
    const ms = d ? Date.parse(d) : NaN;
    if (!Number.isNaN(ms) && ms > bestMs) [best, bestMs] = [d!, ms];
  }
  return best;
}

/** Most urgent first: by tier, then newest first; a job with no date goes last in its tier. */
export function sortByUrgency<T extends JobRankInput>(jobs: T[]): T[] {
  const keyed = jobs.map((j) => ({ j, tier: jobTier(j), at: newestAt(j) }));
  keyed.sort((a, b) => a.tier - b.tier || (b.at ? Date.parse(b.at) : -Infinity) - (a.at ? Date.parse(a.at) : -Infinity) || 0);
  return keyed.map((k) => k.j);
}
