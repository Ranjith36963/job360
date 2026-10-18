// Pure words for the batch queue ("Queued" filter on the applications list). No React.

export type QueueSummary = {
  queued?: number;
  ready_to_send?: number;
  asked?: number;
  blocked?: number;
  daily_left?: number | null;
  [key: string]: unknown;
};

const num = (v: unknown): number => (typeof v === "number" && Number.isFinite(v) ? v : 0);

/** "8 queued · 5 ready · 2 need you · 3 left today". "Need you" = questions open plus blocked.
 *  "Left today" is left out when there is no daily limit. */
export function queueSummaryLine(s: QueueSummary | null | undefined): string {
  if (!s) return "";
  const parts = [
    `${num(s.queued)} queued`,
    `${num(s.ready_to_send)} ready`,
    `${num(s.asked) + num(s.blocked)} need you`,
  ];
  if (typeof s.daily_left === "number") parts.push(`${s.daily_left} left today`);
  return parts.join(" · ");
}

const DUPLICATE_WORDS: Record<string, string> = {
  same_job: "Possible duplicate: you already have this job",
  same_role: "Possible duplicate: same role at this company",
  same_company: "Possible duplicate: you already applied to this company",
};

/** Plain-words problems of one queued row, from the server's `preflight`. Empty list = nothing wrong. */
export function preflightProblems(pf: Record<string, unknown> | null | undefined): string[] {
  if (!pf) return [];
  const out: string[] = [];
  const missing = Array.isArray(pf.kit_missing) ? (pf.kit_missing as unknown[]).map(String) : [];
  if (missing.length > 0) {
    const n = missing.length;
    out.push(`Missing ${n} ${n === 1 ? "answer" : "answers"}`);
  }
  if (pf.has_cv === false) out.push("No CV yet");
  const dup = typeof pf.duplicate_flag === "string" ? pf.duplicate_flag : "";
  if (dup) out.push(DUPLICATE_WORDS[dup] ?? "Possible duplicate");
  if (pf.avoided === true) out.push("On your never-apply list");
  if (pf.company_left === 0) out.push("You are at your limit for this company");
  const blocked = num(pf.blocked_count);
  if (blocked > 0) out.push(`Blocked ${blocked} ${blocked === 1 ? "time" : "times"}`);
  if (pf.hold) out.push("Another assistant session is working on it");
  return out;
}
