import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Ask, CvToApproveRow, ReadyCardData } from "@/lib/api";
import { answer, card } from "@/components/needs-you/ready-fixtures";

const listAsks = vi.fn();
vi.mock("@/lib/api", () => ({ listAsks: (...a: unknown[]) => listAsks(...a) }));

import {
  CHIPS, GROUP_NAMES, SORTS, chipOfTier, countNeedsYou, jobChip, jobHeaders, listAllOpenAsks, readSort, saveSort,
  sortJobs, summaryLine,
} from "./needs-you-items";

const at = (h: number) => `2026-10-20T${String(h).padStart(2, "0")}:00:00Z`;
const ask = (id: number, app: number | null, over: Partial<Ask> = {}): Ask =>
  ({ id, application_id: app, question: "Salary?", context: "", asked_by: "x", asked_at: at(10), status: "open",
    withdrawn_at: null, job_company: `Co${app}`, job_title: "Dev", ...over }) as Ask;
const ready = (id: number, over: Record<string, unknown> = {}) =>
  card(id, { answers: [answer()], ...over }) as unknown as ReadyCardData;
const cvRow = (id: number, saved_at = at(9)) =>
  ({ application_id: id, job_title: "Dev", job_company: `Co${id}`, cv: { artifact_id: id, version: 1, saved_at, made_by: "x" } }) as CvToApproveRow;
const job = (over: { asks?: Ask[]; ready?: ReadyCardData | null; cvSavedAt?: string | null } = {}) =>
  ({ asks: [], ready: null, cvSavedAt: null, ...over });

describe("sort options", () => {
  const jobs = [
    { id: "undated", ...job() },
    { id: "q-old", ...job({ asks: [ask(1, 1, { asked_at: at(1) })] }) },
    { id: "cv-new", ...job({ cvSavedAt: at(9) }) },
    { id: "ready", ...job({ ready: ready(2, { filled_at: at(5) }) }) },
  ];

  it("offers exactly Most urgent (default), Newest first and Oldest first", () => {
    expect(SORTS.map((s) => s.label)).toEqual(["Most urgent", "Newest first", "Oldest first"]);
    expect(SORTS[0].line).toBe("Forms open now, then ready to send, then waiting on your answer, then quick checks.");
  });

  it("Most urgent is today's rank", () => {
    expect(sortJobs(jobs, "urgent").map((j) => j.id)).toEqual(["ready", "q-old", "cv-new", "undated"]);
  });

  it("Newest first and Oldest first sort by the newest open item; a job with no date goes last in both", () => {
    expect(sortJobs(jobs, "newest").map((j) => j.id)).toEqual(["cv-new", "ready", "q-old", "undated"]);
    expect(sortJobs(jobs, "oldest").map((j) => j.id)).toEqual(["q-old", "ready", "cv-new", "undated"]);
  });

  it("keeps the choice per browser, and survives storage that throws", () => {
    window.localStorage.clear();
    expect(readSort()).toBe("urgent");
    saveSort("oldest");
    expect(readSort()).toBe("oldest");
    window.localStorage.setItem("needs-you-sort", "bogus");
    expect(readSort()).toBe("urgent");
    const get = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
    const set = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
    expect(readSort()).toBe("urgent");
    expect(() => saveSort("newest")).not.toThrow();
    get.mockRestore();
    set.mockRestore();
  });

  it("uses the owner's group names and never the old ones", () => {
    expect(GROUP_NAMES).toEqual(["Form open now", "Ready to send", "Waiting on your answer", "Quick checks", "New connections"]);
    const copy = JSON.stringify([SORTS, CHIPS, GROUP_NAMES]);
    expect(copy).not.toMatch(/Stuck on you|People only|Claude/i);
  });
});

describe("filter chips", () => {
  it("maps the rank tiers: Ready = 2, Waiting on you = 1 and 3, Checks = 4 and person-only (5)", () => {
    expect([1, 2, 3, 4, 5].map((t) => chipOfTier(t as 1 | 2 | 3 | 4 | 5))).toEqual(["waiting", "ready", "waiting", "checks", "checks"]);
  });

  it("puts every job in exactly one chip, and the chips add up to All", () => {
    const open = [
      ask(1, 10, { kind: "captcha", held_seconds: 60 } as Partial<Ask>), // form open now
      ask(2, 11), // a free question
      ask(3, null), // not about a job: Other
      ask(4, 12, { question: "SIMULATION: practice" }), // practice: never counted
    ];
    const cards = [ready(20), ready(11)];
    const cvRows = [cvRow(30), cvRow(20)];
    const people = [{ application_id: 40, job_company: "Co40", job_title: "Dev" }];
    const headers = jobHeaders(open, cards, cvRows, people);
    expect(headers.map((h) => h.id).sort()).toEqual([10, 11, 20, 30, 40]);
    const chips = headers.map((h) => jobChip(h));
    expect(chips.every((c) => ["ready", "waiting", "checks"].includes(c))).toBe(true);
    const c = countNeedsYou({ open, ready: cards, cvRows, people, connections: 2, waiting: 1, outcomes: 1, mail: 1 });
    expect(c.byChip).toEqual({ all: 11, ready: 0, waiting: 2, checks: 3, other: 6 });
    expect(c.byChip.ready + c.byChip.waiting + c.byChip.checks + c.byChip.other).toBe(c.byChip.all);
    expect(c.jobs).toBe(headers.length);
  });
});

describe("countNeedsYou", () => {
  it("counts one per job (many asks, a ready card and a CV on one job are one)", () => {
    const c = countNeedsYou({ open: [ask(1, 7), ask(2, 7)], ready: [ready(7)], cvRows: [cvRow(7)], waiting: 0 });
    expect(c).toMatchObject({ n: 1, jobs: 1, other: 0 });
  });

  it("leaves out dropped jobs and counts asks of a failed job in Other", () => {
    const c = countNeedsYou({ open: [ask(1, 7), ask(2, 8)], ready: [], waiting: 0, dropped: [8], failedAsks: 1 });
    expect(c).toMatchObject({ n: 2, jobs: 1, other: 1 });
  });

  it("ready cards past the page still count, as Ready", () => {
    expect(countNeedsYou({ open: [], ready: [ready(1)], readyTotal: 3, waiting: 0 }).byChip.ready).toBe(3);
  });

  it("the badge's cheap read: a ready TOTAL instead of cards", () => {
    const c = countNeedsYou({ open: [ask(1, 7), ask(2, null)], ready: 2, waiting: 1 });
    expect(c).toMatchObject({ n: 5, jobs: 3, other: 2 });
  });

  it("says the summary line in plain words", () => {
    expect(summaryLine(0)).toBe("Nothing needs you right now.");
    expect(summaryLine(1)).toBe("1 thing needs you");
    expect(summaryLine(57)).toBe("57 things need you");
  });
});

describe("listAllOpenAsks", () => {
  beforeEach(() => listAsks.mockReset());
  afterEach(() => listAsks.mockReset());

  it("reads every page of open asks, not only the first 50", async () => {
    const page = (from: number, n: number) => Array.from({ length: n }, (_, i) => ask(from + i, from + i));
    listAsks.mockResolvedValueOnce({ asks: page(1, 50) }).mockResolvedValueOnce({ asks: page(51, 10) });
    const all = await listAllOpenAsks();
    expect(all).toHaveLength(60);
    expect(listAsks).toHaveBeenNthCalledWith(1, "open");
    expect(listAsks).toHaveBeenNthCalledWith(2, "open", 50);
  });

  it("stops at 10 pages", async () => {
    listAsks.mockImplementation(async () => ({ asks: Array.from({ length: 50 }, (_, i) => ask(i, i)) }));
    expect(await listAllOpenAsks()).toHaveLength(500);
    expect(listAsks).toHaveBeenCalledTimes(10);
  });
});
