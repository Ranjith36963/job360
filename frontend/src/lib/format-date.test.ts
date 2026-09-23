import { describe, it, expect } from "vitest";
import { formatDate, formatDateTime } from "./format-date";

describe("formatDate", () => {
  it("spells the month out so the date can't be misread as day/month vs month/day", () => {
    const out = formatDate("2026-09-23T00:00:00Z");
    expect(out).toMatch(/[A-Za-z]/);
    expect(out).toContain("2026");
  });

  it("returns empty string for an invalid date", () => {
    expect(formatDate("not-a-date")).toBe("");
  });
});

describe("formatDateTime", () => {
  it("spells the month out so the date can't be misread as day/month vs month/day", () => {
    const out = formatDateTime("2026-09-23T14:05:00Z");
    expect(out).toMatch(/[A-Za-z]/);
    expect(out).toContain("2026");
  });

  it("returns empty string for an invalid date", () => {
    expect(formatDateTime("not-a-date")).toBe("");
  });
});
