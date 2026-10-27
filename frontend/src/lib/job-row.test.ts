import { describe, expect, it } from "vitest";
import { nextStep } from "@/lib/job-row";

describe("nextStep", () => {
  it("a job whose only open item is a person reads Person to contact, never New connections", () => {
    expect(nextStep({ asks: [], ready: null, cvSavedAt: null })).toBe("Person to contact");
  });

  it("a job with a CV to approve reads Quick checks", () => {
    expect(nextStep({ asks: [], ready: null, cvSavedAt: "2026-10-26T09:00:00Z" })).toBe("Quick checks");
  });
});
