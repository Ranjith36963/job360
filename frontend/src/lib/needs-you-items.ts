// The Needs-you page's items, in one place (owner plan §2.4-§2.6): the jobs it lists (one per
// application), the sort, the filter chips and ONE count: the server's (GET /api/needs-you-count, backend
// services/applications/needs_you_count.py), read by the page, the chips, the nav badge and the dashboard.
// Pure except `listAllOpenAsks`. Every string here was written by an assistant: never rendered here.

import { listAsks } from "@/lib/api";
import type { NeedsYouCount as NeedsYouCountOut } from "@/lib/api";
import type { Ask, CvToApproveRow, ReadyCardData } from "@/lib/api";
import { groupAsks } from "@/lib/job-cards";
import { newestAt, sortByUrgency } from "@/lib/needs-you-rank";
import type { JobRankInput } from "@/lib/needs-you-rank";

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
  { key: "urgent", label: "Most urgent", line: "" },
  { key: "newest", label: "Newest first", line: "The job with the newest question, filled form or CV first." },
  { key: "oldest", label: "Oldest first", line: "The job that has waited longest first." },
];
/** The group names shown to the user, by tier (owner 2026-10-26); Network sits below the jobs. */
export const GROUP_NAMES = ["Form open now", "Send", "On you", "Quick checks", "Network"] as const;

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

/** One of the server's fixed filter keys ("all", "signin_captcha" ... "other"). */
export type Chip = string;
/** The fixed filters, in the server's order (backend needs_you_count.FILTERS). The server's answer is what the page
 *  shows; this table only gives a label to a link or a back-trail, and the fallback when an old answer has none. */
export const FILTER_CHIPS: { key: string; label: string; kind: "todo" | "view" | "stat" }[] = [
  { key: "all", label: "All", kind: "todo" },
  { key: "signin_captcha", label: "Sign in / CAPTCHA", kind: "todo" },
  { key: "check_send", label: "Check & send", kind: "todo" },
  { key: "answer_once", label: "Answer once", kind: "todo" },
  { key: "cv_weak", label: "CV still weak", kind: "todo" },
  { key: "finish_by_hand", label: "Finish by hand", kind: "todo" },
  { key: "went_out", label: "Did it go out?", kind: "todo" },
  { key: "strong_cvs", label: "Strong CVs (AI reviewed)", kind: "view" },
  { key: "posting_closed", label: "Posting closed", kind: "stat" },
  { key: "other", label: "Other", kind: "todo" },
];
export const chipLabel = (key: Chip): string => FILTER_CHIPS.find((c) => c.key === key)?.label ?? key;

// ---- One count ----

export type NeedsYouCount = {
  n: number;
  jobs: number;
  other: number;
  byChip: Record<Chip, number>;
  /** The chips to show: ALL the server's filters, in its fixed order, at zero too. */
  chips: Chip[];
  /** "todo" | "view" | "stat" per chip: a view is not a to-do and a stat is a count only. */
  kinds: Record<Chip, string>;
  /** The jobs that wait on the user (the page's rows). */
  jobIds: number[];
  /** The jobs of each filter (a job is in every filter it has an owner need in). */
  idsByKind: Record<string, number[]>;
  /** Open asks tied to a job that is not a row (e.g. proof missing on a closed job): listed in Other. */
  otherAskIds: number[];
  /** The Answer-once card has rows waiting: it shows under All and Answer once. */
  answerOnce: boolean;
};

/** The page's count IS the server's (GET /api/needs-you-count): N = jobs waiting on you + Other, All = N, and the
 *  chips are the server's fixed `filters`, every one always shown. The nav badge and the dashboard headline read the
 *  same response; nothing is counted or derived here. */
export function fromServerCount(c: NeedsYouCountOut): NeedsYouCount {
  const answerOnce = (c.answer_once ?? 0) > 0;
  const served = new Map((c.filters ?? []).map((f) => [f.key, f]));
  const byChip: Record<Chip, number> = {};
  const kinds: Record<Chip, string> = {};
  const idsByKind: Record<string, number[]> = {};
  for (const f of FILTER_CHIPS) {
    const s = served.get(f.key);
    byChip[f.key] = s ? s.count : f.key === "all" ? c.n : f.key === "other" ? c.other : 0;
    kinds[f.key] = s?.kind ?? f.kind;
    if (f.key !== "all") idsByKind[f.key] = s?.job_ids ?? [];
  }
  return {
    n: c.n, jobs: c.jobs, other: c.other, byChip, chips: FILTER_CHIPS.map((f) => f.key), kinds,
    jobIds: c.job_ids ?? [], idsByKind, otherAskIds: c.other_ask_ids ?? [], answerOnce,
  };
}
export const EMPTY_COUNT: NeedsYouCount = fromServerCount({
  n: 0, jobs: 0, other: 0, jobs_waiting_on_you: 0, by_actor: {}, by_kind: {}, job_ids: [], job_ids_by_kind: {}, other_ask_ids: [],
  answer_once: 0, filters: [],
});

/** "57 things need you" (owner plan §2.6). */
export function summaryLine(n: number): string {
  if (n <= 0) return "Nothing needs you right now.";
  return `${n} ${n === 1 ? "thing needs" : "things need"} you`;
}
