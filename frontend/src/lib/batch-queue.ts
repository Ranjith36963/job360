// Pure words for the batch queue ("Queued" filter on the applications list). No React.

export type QueueSummary = {
  queued?: number;
  outreach?: number;
  ready_to_send?: number;
  asked?: number;
  blocked?: number;
  daily_left?: number | null;
  by_browser?: { job360?: number; own_browser?: number; human?: number };
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
  if (num(s.outreach) > 0) parts.push(`${num(s.outreach)} outreach`);
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

/** Where a job came from (owner ask 2026-10-18): the tag on every job. "" = nothing to show. */
export const ORIGIN_LABEL: Record<string, string> = {
  ai_hunt: "AI hunt",
  user_link: "Your link",
  user_paste: "Pasted ad",
  your_mail: "Your mail",
  user: "You brought it",
  ai: "Your AI brought it",
};
export function originLabel(origin: unknown): string {
  return typeof origin === "string" ? (ORIGIN_LABEL[origin] ?? "") : "";
}

/** The queue filter's three browser kinds plus "all" (owner, 2026-10-26). */
export type QueueBrowserFilter = "all" | "job360" | "own_browser" | "human";

const BROWSER_FILTER_WORDS: Record<Exclude<QueueBrowserFilter, "all">, string> = {
  job360: "Job360's browser",
  own_browser: "Your own browser",
  human: "Only you",
};

/** Plain words for why a queued job left Job360's browser (the server's route reason). Unknown = no line. */
const BROWSER_REASON_WORDS: Record<string, string> = {
  job360_login_required: "Job360's browser hit a sign-in wall",
  job360_bot_check: "Job360's browser hit a bot check",
  job360_safety_stop: "Job360's browser stopped to be safe",
  mfa: "This site wants a code only you have",
  password: "This site wants your password",
  only_you: "Only you can do this step",
  link_unclear: "The apply link has no clear web address",
};

export function browserReasonWords(reason: string | undefined): string {
  return (reason && BROWSER_REASON_WORDS[reason]) || "";
}

export type BrowserFilterOption = { key: QueueBrowserFilter; text: string; count: number };

/** The filter row: "All 23", "Job360's browser 17", "Your own browser 6", and "Only you N" only above 0.
 *  Counts come from the server's `by_browser` (the whole queue); "All" is the queue size. */
export function browserFilterOptions(
  s: QueueSummary | null | undefined,
  fallbackTotal: number,
): BrowserFilterOption[] {
  const by = (s?.by_browser ?? {}) as Record<string, unknown>;
  // One queue (owner, 2026-10-26): "All" counts the approved LinkedIn sends too, as `by_browser` does.
  const total = typeof s?.queued === "number" ? s.queued + num(s.outreach) : fallbackTotal;
  const opts: BrowserFilterOption[] = [
    { key: "all", text: `All ${total}`, count: total },
    { key: "job360", text: `${BROWSER_FILTER_WORDS.job360} ${num(by.job360)}`, count: num(by.job360) },
    { key: "own_browser", text: `${BROWSER_FILTER_WORDS.own_browser} ${num(by.own_browser)}`, count: num(by.own_browser) },
  ];
  if (num(by.human) > 0) {
    opts.push({ key: "human", text: `${BROWSER_FILTER_WORDS.human} ${num(by.human)}`, count: num(by.human) });
  }
  return opts;
}

/** Narrow the loaded rows to one browser kind (client side, over the rows already fetched). */
export function filterByBrowser<T extends { browser?: { kind?: string } | null }>(
  rows: T[],
  filter: QueueBrowserFilter,
): T[] {
  return filter === "all" ? rows : rows.filter((r) => r.browser?.kind === filter);
}

/** The kind tag on one queue item (owner, 2026-10-26). Email has no approved-but-unsent state yet, so the
 *  backend never sends one; the word is here for when it does. */
const KIND_TAG: Record<string, string> = {
  application: "Application",
  linkedin_note: "LinkedIn note",
  linkedin_message: "LinkedIn message",
  email: "Email",
};
export function kindTag(kind: string | undefined): string {
  return (kind && KIND_TAG[kind]) || "";
}

type Chip = { kind?: string } | null | undefined;
export type QueueItem<A, O> =
  | { key: string; kind: "application"; row: A; browser: Chip }
  | { key: string; kind: "outreach"; row: O; browser: Chip };

/** The one queue in order: the server's `queue_order` (task ids, oldest yes first) over the loaded rows.
 *  Rows the order does not name (an older backend, or a row past the page) follow in their own order. */
export function mergeQueue<A extends { id: number; browser?: Chip }, O extends { task_id: string; browser?: Chip }>(apps: A[], outreach: O[], order: string[] | null | undefined): QueueItem<A, O>[] {
  const all = new Map<string, QueueItem<A, O>>();
  for (const a of apps) {
    const key = `application:${a.id}`;
    all.set(key, { key, kind: "application", row: a, browser: a.browser });
  }
  for (const o of outreach) all.set(o.task_id, { key: o.task_id, kind: "outreach", row: o, browser: o.browser });
  const out: QueueItem<A, O>[] = [];
  for (const k of order ?? []) {
    const item = all.get(k);
    if (item) {
      out.push(item);
      all.delete(k);
    }
  }
  return [...out, ...all.values()];
}
