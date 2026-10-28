// The Needs-you page's items, in one place (owner plan §2.4-§2.6): the jobs it lists (one per
// application), the sort, the filter chips and ONE count. The page and the chips count through
// `countNeedsYou`, so "N things need you", the All chip and the chips' sum agree; the nav badge reads the
// server's count of the same rules (GET /api/needs-you-count, backend services/applications/needs_you_count.py).
// Pure except `listAllOpenAsks`. Every string here was written by an assistant: never rendered here.

import { listAsks } from "@/lib/api";
import type { Ask, CvToApproveRow, ReadyCardData } from "@/lib/api";
import { groupAsks } from "@/lib/job-cards";
import { jobTier, newestAt, sortByUrgency } from "@/lib/needs-you-rank";
import type { JobRankInput, JobTier } from "@/lib/needs-you-rank";

const ASKS_PAGE = 50; // the server's page size for GET /api/asks
const MAX_ASK_PAGES = 10;

/** Every open ask, page by page (at most 10 pages of 50), not only the first page. */
export async function listAllOpenAsks(): Promise<Ask[]> {
  const open: Ask[] = [];
  for (let page = 0; page < MAX_ASK_PAGES; page++) {
    const { asks } = await (open.length ? listAsks("open", open.length) : listAsks("open"));
    open.push(...asks);
    if (asks.length < ASKS_PAGE) break;
  }
  return open;
}

/** A job with a drafted note to someone that is not marked sent yet. */
export type PersonJob = { application_id: number; job_company: string; job_title: string };

/** One job on the page: what it ranks by, plus what its header shows. */
export type JobHeader = JobRankInput & {
  id: number;
  company: string;
  role: string;
  cv: { artifactId: number; version: number } | null;
  people: boolean;
};

/** One header per job, from the open asks, the ready cards, the CVs to check and the people to reach. */
export function jobHeaders(open: Ask[], ready: ReadyCardData[], cvRows: CvToApproveRow[], people: PersonJob[]): JobHeader[] {
  const all = new Map<number, JobHeader>();
  const add = (id: number, company: string, role: string, set: Partial<JobHeader>) =>
    all.set(id, Object.assign(all.get(id) ?? { id, company, role, asks: [], ready: null, cv: null, cvSavedAt: null, people: false }, set));
  for (const [id, asks] of groupAsks(open).byJob) add(id, asks[0]?.job_company ?? "", asks[0]?.job_title ?? "", { asks });
  for (const c of ready) add(c.application_id, c.job_company, c.job_title, { ready: c });
  for (const r of cvRows)
    add(r.application_id, r.job_company, r.job_title, { cv: { artifactId: r.cv.artifact_id, version: r.cv.version }, cvSavedAt: r.cv.saved_at });
  for (const j of people) add(j.application_id, j.job_company, j.job_title, { people: true });
  return [...all.values()];
}

// ---- Sort ----

export type SortKey = "urgent" | "newest" | "oldest";
export const SORTS: { key: SortKey; label: string; line: string }[] = [
  { key: "urgent", label: "Most urgent", line: "Forms open now, then ready to send, then waiting on your answer, then quick checks." },
  { key: "newest", label: "Newest first", line: "The job with the newest question, filled form or CV first." },
  { key: "oldest", label: "Oldest first", line: "The job that has waited longest first." },
];
/** The group names shown to the user, by tier (owner 2026-10-26); New connections sit below the jobs. */
export const GROUP_NAMES = ["Form open now", "Ready to send", "Waiting on your answer", "Quick checks", "New connections"] as const;

const SORT_STORE = "needs-you-sort";
const isSort = (v: unknown): v is SortKey => SORTS.some((s) => s.key === v);

/** The sort this browser chose last; "urgent" when none, or storage is blocked. */
export function readSort(): SortKey {
  try {
    const v = window.localStorage.getItem(SORT_STORE);
    return isSort(v) ? v : "urgent";
  } catch {
    return "urgent";
  }
}

export function saveSort(key: SortKey): void {
  try {
    window.localStorage.setItem(SORT_STORE, key);
  } catch {
    // Storage blocked (private window): the choice lasts this visit only.
  }
}

/** Most urgent: today's rank. Newest / Oldest: by the newest open item's date; no date goes last in both. */
export function sortJobs<T extends JobRankInput>(jobs: T[], key: SortKey): T[] {
  if (key === "urgent") return sortByUrgency(jobs);
  const dir = key === "newest" ? -1 : 1;
  const keyed = jobs.map((j) => ({ j, ms: Date.parse(newestAt(j)) }));
  keyed.sort((a, b) => {
    const na = Number.isNaN(a.ms), nb = Number.isNaN(b.ms);
    if (na || nb) return Number(na) - Number(nb);
    return dir * (a.ms - b.ms);
  });
  return keyed.map((k) => k.j);
}

// ---- Filter chips ----

export type Chip = "all" | "ready" | "waiting" | "checks" | "other";
export const CHIPS: { key: Chip; label: string }[] = [
  { key: "all", label: "All" },
  { key: "ready", label: "Ready" },
  { key: "waiting", label: "Waiting on you" },
  { key: "checks", label: "Checks" },
  { key: "other", label: "Other" },
];
export const isChip = (v: unknown): v is Chip => CHIPS.some((c) => c.key === v);

/** The one chip a job sits in: Ready = tier 2; Waiting on you = tiers 1 and 3 (a form open now needs
 *  you too); Checks = tier 4 and a job whose only item left is a person (tier 5). */
export function chipOfTier(tier: JobTier): "ready" | "waiting" | "checks" {
  if (tier === 2) return "ready";
  return tier === 1 || tier === 3 ? "waiting" : "checks";
}
export const jobChip = (j: JobRankInput) => chipOfTier(jobTier(j));

// ---- One count ----

export type NeedsYouInputs = {
  /** Every open ask (all pages). Practice asks are left out here. */
  open: Ask[];
  /** The ready cards, or only their total when the cards were not read. The badge no longer counts here
   *  (it reads GET /api/needs-you-count, which applies these same rules on the server). */
  ready: ReadyCardData[] | number;
  /** The ready total when `ready` holds the cards: cards past the page still count, as Ready. */
  readyTotal?: number;
  cvRows?: CvToApproveRow[];
  people?: PersonJob[];
  /** People with no job and an unsent note (New connections). */
  connections?: number;
  /** Setting changes waiting for an OK, outcome suggestions, unmatched mail. */
  waiting: number;
  outcomes?: number;
  mail?: number;
  /** Jobs the list dropped (their card loaded with nothing open, or failed with nothing ready). */
  dropped?: number[];
  /** Asks of jobs whose record failed to load: the page shows them in Other. */
  failedAsks?: number;
};

export type NeedsYouCount = { n: number; jobs: number; other: number; byChip: Record<Chip, number> };

/** N = jobs (one per application, each in exactly one chip) + Other items. All = N = the chips' sum. */
export function countNeedsYou(i: NeedsYouInputs): NeedsYouCount {
  const cards = typeof i.ready === "number" ? [] : i.ready;
  const dropped = new Set(i.dropped ?? []);
  const byChip: Record<Chip, number> = { all: 0, ready: 0, waiting: 0, checks: 0, other: 0 };
  for (const h of jobHeaders(i.open, cards, i.cvRows ?? [], i.people ?? []))
    if (!dropped.has(h.id)) byChip[jobChip(h)]++;
  // Ready jobs the cards did not carry (a counts-only read, or past the page); a counts-only read cannot
  // see which of them also have an open ask, so only a caller that holds the cards counts exactly.
  byChip.ready += typeof i.ready === "number" ? i.ready : Math.max(0, (i.readyTotal ?? cards.length) - cards.length);
  const jobs = byChip.ready + byChip.waiting + byChip.checks;
  byChip.other = groupAsks(i.open).general.length + (i.failedAsks ?? 0) + i.waiting + (i.outcomes ?? 0) + (i.mail ?? 0) + (i.connections ?? 0);
  byChip.all = jobs + byChip.other;
  return { n: byChip.all, jobs, other: byChip.other, byChip };
}

/** "57 things need you" (owner plan §2.6). */
export function summaryLine(n: number): string {
  if (n <= 0) return "Nothing needs you right now.";
  return `${n} ${n === 1 ? "thing needs" : "things need"} you`;
}
