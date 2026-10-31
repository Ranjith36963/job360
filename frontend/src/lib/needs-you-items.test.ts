import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Ask, CvToApproveRow, ReadyCardData } from "@/lib/api";
import { answer, card } from "@/components/needs-you/ready-fixtures";

const listAsks = vi.fn();
vi.mock("@/lib/api", () => ({ listAsks: (...a: unknown[]) => listAsks(...a) }));

import {
  GROUP_NAMES, SORTS, chipLabel, fromServerCount, jobHeaders, listAllOpenAsks, readSort, saveSort,
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
    const copy = JSON.stringify([SORTS, GROUP_NAMES]);
    expect(copy).not.toMatch(/Stuck on you|People only|Claude/i);
  });
});

describe("job headers", () => {
  it("makes one header per job from asks, ready cards, CVs and people", () => {
    const open = [ask(1, 10), ask(2, 11), ask(3, null), ask(4, 12)];
    const headers = jobHeaders(open, [ready(20), ready(11)], [cvRow(30), cvRow(20)], [{ application_id: 40, job_company: "Co40", job_title: "Dev" }]);
    expect(headers.map((h) => h.id).sort()).toEqual([10, 11, 12, 20, 30, 40]);
  });
});

describe("fromServerCount", () => {
  const server = (over: Record<string, unknown> = {}) => ({
    n: 9, jobs: 7, other: 2, jobs_waiting_on_you: 7,
    by_actor: { you: 7, your_pc: 1, assistant: 3, job360: 0, employer: 2 },
    by_kind: { send_yes: 2, approve_cv: 1, outreach_send: 1, answers: 2, site_step: 1, assistant_step: 3 },
    job_ids: [5, 6, 7, 8, 9, 10, 11],
    job_ids_by_kind: { site_step: [5], approve_cv: [6], answers: [7, 8], send_yes: [9, 10], outreach_send: [11] },
    ...over,
  });

  it("one chip per kind that waits on you, in plan words; the chips add up to the jobs, All is N", () => {
    const c = fromServerCount(server());
    expect(c.chips).toEqual(["all", "site_step", "approve_cv", "answers", "send_yes", "outreach_send", "other"]);
    expect(c.chips.map(chipLabel)).toEqual(["All", "On the site", "Approve CV", "Answers", "Send", "Notes", "Other"]);
    expect(c.byChip).toEqual({ all: 9, other: 2, site_step: 1, approve_cv: 1, answers: 2, send_yes: 2, outreach_send: 1 });
    expect(c.chips.filter((k) => k !== "all" && k !== "other").reduce((t, k) => t + c.byChip[k], 0)).toBe(c.jobs);
    expect(c.jobIds).toHaveLength(c.jobs);
  });

  it("a server with nothing waiting on the user has only All", () => {
    const c = fromServerCount(server({ n: 0, jobs: 0, other: 0, job_ids: [], job_ids_by_kind: {} }));
    expect(c.chips).toEqual(["all"]);
    expect(c.byChip.all).toBe(0);
  });

  it("leaves out dropped jobs and counts asks of a failed job in Other", () => {
    // Job 8 is not a row (its ask is in other_ask_ids, so it counts in Other); job 7 is a row and counts once in All.
    const c = fromServerCount(server({
      n: 2, jobs: 1, other: 1, job_ids: [7], job_ids_by_kind: { answers: [7] }, other_ask_ids: [31],
    }));
    expect(c.otherAskIds).toEqual([31]);
    expect(c.byChip).toMatchObject({ all: 2, other: 1, answers: 1 });
    expect(c.jobIds).toEqual([7]);
  });

  it("ready cards past the page still count, as Ready", () => {
    // 30 Send jobs: the page loads only a few cards, but the chip is the server's count of ids.
    const ids = Array.from({ length: 30 }, (_, i) => 100 + i);
    const c = fromServerCount(server({ n: 30, jobs: 30, other: 0, job_ids: ids, job_ids_by_kind: { send_yes: ids } }));
    expect(c.byChip.send_yes).toBe(30);
    expect(chipLabel("send_yes")).toBe("Send");
  });

  it("the badge's cheap read: the number is the server's n, not a count of loaded cards", () => {
    const c = fromServerCount(server({ n: 12, jobs: 7, other: 5, job_ids: [1, 2, 3, 4, 5, 6, 7], job_ids_by_kind: { answers: [1, 2, 3, 4, 5, 6, 7] } }));
    expect(c.n).toBe(12);
    expect(c.byChip.all).toBe(12);
    expect(c.jobs + c.other).toBe(c.n);
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
