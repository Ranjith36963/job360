import { describe, expect, it } from "vitest";
import type { Ask, ReadyCardData } from "@/lib/api";
import { jobTier, newestAt, sortByUrgency } from "./needs-you-rank";
import { answer, card } from "@/components/needs-you/ready-fixtures";

const ask = (over: Partial<Ask> = {}): Ask =>
  ({ id: 1, application_id: 1, question: "Salary?", context: "", asked_by: "x", asked_at: "2026-10-20T10:00:00Z",
    status: "open", withdrawn_at: null, ...over }) as Ask;
const ready = (over: Record<string, unknown> = {}) => card(1, { answers: [answer()], ...over }) as unknown as ReadyCardData;
const job = (over: { asks?: Ask[]; ready?: ReadyCardData | null; cvSavedAt?: string | null } = {}) =>
  ({ asks: [], ready: null, cvSavedAt: null, ...over });
const form = (q: Record<string, unknown>) =>
  ask({ questions: [{ key: "k", question: "Notice period", answer: "1 month", source: "guess", ...q }] } as Partial<Ask>);

describe("jobTier", () => {
  it("1: a held form (CAPTCHA, sign-in, or a held ready form)", () => {
    expect(jobTier(job({ asks: [ask({ kind: "captcha", held_seconds: 300 } as Partial<Ask>)] }))).toBe(1);
    expect(jobTier(job({ ready: ready({ held_seconds: 60 }) }))).toBe(1);
  });

  it("2: ready to send with nothing blocking (the user's own change blocks nothing)", () => {
    expect(jobTier(job({ ready: ready() }))).toBe(2);
    expect(jobTier(job({ ready: ready({ flags: [{ code: "changed", text: "You changed it", key: "x" }] }) }))).toBe(2);
  });

  it("3: stuck on you - a free question, a blocker with the form closed, a blank or missing answer", () => {
    expect(jobTier(job({ asks: [ask()] }))).toBe(3);
    expect(jobTier(job({ asks: [ask({ kind: "login" } as Partial<Ask>)], ready: ready() }))).toBe(3);
    expect(jobTier(job({ ready: ready({ answers: [answer({ answer: "" })] }) }))).toBe(3);
    expect(jobTier(job({ ready: ready({ flags: [{ code: "missing", text: "Salary not saved", key: "salary.DE" }] }) }))).toBe(3);
    expect(jobTier(job({ asks: [form({ answer: "", source: "not_known" })] }))).toBe(3);
  });

  it("3: a sensitive form question waits for the user even with a guess", () => {
    expect(jobTier(job({ asks: [form({ question: "Do you consent to data processing?", answer: "Yes" })] }))).toBe(3);
  });

  it("4: quick checks - a guessed answer, a CV to approve, a ready card with a guess", () => {
    expect(jobTier(job({ asks: [form({})] }))).toBe(4);
    expect(jobTier(job({ cvSavedAt: "2026-10-20T10:00:00Z" }))).toBe(4);
    expect(jobTier(job({ ready: ready(), cvSavedAt: "2026-10-20T10:00:00Z" }))).toBe(4);
    expect(jobTier(job({ ready: card(1) as unknown as ReadyCardData }))).toBe(4);
  });

  it("5: people or notes only", () => {
    expect(jobTier(job())).toBe(5);
    expect(jobTier(job({ asks: [form({ source: "profile" })] }))).toBe(5);
    expect(jobTier(job({ asks: [ask({ question: "Send the LinkedIn message to Pat" })] }))).toBe(5);
  });
});

describe("sortByUrgency", () => {
  it("sorts by tier, then newest first, with undated jobs last in their tier", () => {
    const at = (h: number) => `2026-10-20T${String(h).padStart(2, "0")}:00:00Z`;
    const jobs = [
      { id: "notes", ...job() },
      { id: "q-old", ...job({ asks: [ask({ asked_at: at(1) })] }) },
      { id: "cv", ...job({ cvSavedAt: at(9) }) },
      { id: "q-new", ...job({ asks: [ask({ asked_at: at(5) })] }) },
      { id: "ready", ...job({ ready: ready() }) },
      { id: "held", ...job({ asks: [ask({ kind: "captcha", held_seconds: 30, asked_at: at(0) } as Partial<Ask>)] }) },
    ];
    expect(sortByUrgency(jobs).map((j) => j.id)).toEqual(["held", "ready", "q-new", "q-old", "cv", "notes"]);
  });

  it("newestAt reads the newest of asks, the fill and the CV", () => {
    expect(newestAt(job({ asks: [ask({ asked_at: "2026-10-01T00:00:00Z" })], cvSavedAt: "2026-10-03T00:00:00Z" }))).toBe("2026-10-03T00:00:00Z");
    expect(newestAt(job())).toBe("");
  });
});
