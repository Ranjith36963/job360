import { describe, it, expect } from "vitest";
import { browserReasonWords, preflightProblems, queueSummaryLine } from "./batch-queue";

describe("queueSummaryLine", () => {
  it("reads as one line", () => {
    expect(queueSummaryLine({ queued: 8, ready_to_send: 5, asked: 1, blocked: 1, daily_left: 3 })).toBe(
      "8 queued · 5 ready · 2 need you · 3 left today",
    );
  });
  it("leaves out 'left today' when there is no daily limit, and is empty without a summary", () => {
    expect(queueSummaryLine({ queued: 1, ready_to_send: 0, asked: 0, blocked: 0, daily_left: null })).toBe(
      "1 queued · 0 ready · 0 need you",
    );
    expect(queueSummaryLine(null)).toBe("");
  });
});

describe("preflightProblems", () => {
  it("says every problem in plain words", () => {
    expect(
      preflightProblems({
        kit_missing: ["a", "b"], has_cv: false, duplicate_flag: "same_job", avoided: true,
        company_left: 0, blocked_count: 2, hold: "Claude",
      }),
    ).toEqual([
      "Missing 2 answers",
      "No CV yet",
      "Possible duplicate: you already have this job",
      "On your never-apply list",
      "You are at your limit for this company",
      "Blocked 2 times",
      "Another assistant session is working on it",
    ]);
  });
  it("has nothing to say for a clean row", () => {
    expect(
      preflightProblems({ kit_missing: [], has_cv: true, duplicate_flag: "", avoided: false, company_left: null, blocked_count: 0, hold: null }),
    ).toEqual([]);
    expect(preflightProblems(null)).toEqual([]);
  });
});

describe("browserReasonWords", () => {
  it("has a plain line for an unclear apply link, none for the LinkedIn choice or an unknown code", () => {
    expect(browserReasonWords("link_unclear")).toBe("The apply link has no clear web address");
    expect(browserReasonWords("linkedin_own_browser")).toBe("");
    expect(browserReasonWords("some_new_code")).toBe("");
  });
});
