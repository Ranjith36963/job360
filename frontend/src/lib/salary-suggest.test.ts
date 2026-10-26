import { describe, expect, it } from "vitest";
import { formatSalary, salaryFactsFromProfile, salaryKind, suggestSalary } from "./salary-suggest";
import type { SalaryFacts } from "./salary-suggest";

const facts = (over: Partial<SalaryFacts> = {}): SalaryFacts => ({
  current: { amount: 45000, currency: "GBP", period: "year" },
  expected: [
    { country: "GB", min: 60000, max: 80000, currency: "GBP", period: "year" },
    { country: "IN", min: 2500000, max: null, currency: "INR", period: "year" },
  ],
  ...over,
});

describe("salaryKind", () => {
  it("tells current from expected, including the two Dwelly questions", () => {
    expect(salaryKind("What is your current/last salary package?")).toBe("current");
    expect(salaryKind("What is your expected salary package?")).toBe("expected");
    expect(salaryKind("Present CTC")).toBe("current");
    expect(salaryKind("Expected CTC")).toBe("expected");
    expect(salaryKind("Existing compensation")).toBe("current");
    expect(salaryKind("Desired pay")).toBe("expected");
    expect(salaryKind("What salary are you looking for?")).toBe("expected");
    expect(salaryKind("Salary expectations")).toBe("expected");
    expect(salaryKind("Salary")).toBe("expected");
  });
  it("is null for questions that are not about salary", () => {
    expect(salaryKind("Notice period?")).toBeNull();
    expect(salaryKind("Do you need a visa?")).toBeNull();
    expect(salaryKind("Payroll number")).toBeNull();
  });
});

describe("formatSalary", () => {
  it("formats an amount, a range, min only and max only", () => {
    expect(formatSalary(45000, null, "GBP", "year")).toBe("£45,000 a year");
    expect(formatSalary(60000, 80000, "GBP", "year")).toBe("£60,000 – £80,000 a year");
    expect(formatSalary(60000, null, "GBP", "year")).toBe("£60,000 a year");
    expect(formatSalary(null, 80000, "GBP", "year")).toBe("up to £80,000 a year");
    expect(formatSalary(2500000, null, "INR", "year")).toBe("₹25,00,000 a year");
  });
  it("handles other periods, an unknown period and a missing currency", () => {
    expect(formatSalary(4000, null, "GBP", "month")).toBe("£4,000 a month");
    expect(formatSalary(300, null, "GBP", "day")).toBe("£300 a day");
    expect(formatSalary(25, null, "GBP", "hour")).toBe("£25 an hour");
    expect(formatSalary(45000, null, "GBP", "")).toBe("£45,000");
    expect(formatSalary(45000, null, "", "year")).toBe("45,000 a year");
    expect(formatSalary(null, null, "GBP", "year")).toBeNull();
  });
});

describe("salaryFactsFromProfile", () => {
  it("reads current_salary and salary_by_country, skipping empty entries", () => {
    const f = salaryFactsFromProfile({
      user_info: { logistics: { current_salary: { amount: 45000, currency: "GBP", period: "year" } } },
      preferences: { salary_by_country: [{ country: "GB", min: 60000, max: 80000, currency: "GBP", period: "year" }, { country: "FR" }, "x"] },
    });
    expect(f.current).toEqual({ amount: 45000, currency: "GBP", period: "year" });
    expect(f.expected).toHaveLength(1);
  });
  it("reads missing or malformed data as nothing saved", () => {
    expect(salaryFactsFromProfile(null)).toEqual({ current: null, expected: [] });
    expect(salaryFactsFromProfile({ user_info: { logistics: { current_salary: { amount: "" } } }, preferences: { salary_by_country: "no" } }))
      .toEqual({ current: null, expected: [] });
  });
});

describe("suggestSalary", () => {
  it("suggests the current salary for a current question", () => {
    expect(suggestSalary("What is your current/last salary package?", "GB", facts())).toBe("£45,000 a year");
  });
  it("uses the entry for the job's country, ignoring case", () => {
    expect(suggestSalary("What is your expected salary package?", "gb", facts())).toBe("£60,000 – £80,000 a year");
    expect(suggestSalary("Expected salary", "IN", facts())).toBe("₹25,00,000 a year");
  });
  it("falls back to a single entry, but not to one of several", () => {
    const one = facts({ expected: [{ country: "GB", min: 60000, max: null, currency: "GBP", period: "year" }] });
    expect(suggestSalary("Expected salary", "FR", one)).toBe("£60,000 a year");
    expect(suggestSalary("Expected salary", null, one)).toBe("£60,000 a year");
    expect(suggestSalary("Expected salary", "FR", facts())).toBeNull();
    expect(suggestSalary("Expected salary", null, facts())).toBeNull();
  });
  it("gives nothing when nothing is saved or the question is not about salary", () => {
    expect(suggestSalary("Current salary", "GB", facts({ current: null }))).toBeNull();
    expect(suggestSalary("Expected salary", "GB", facts({ expected: [] }))).toBeNull();
    expect(suggestSalary("Current salary", "GB", undefined)).toBeNull();
    expect(suggestSalary("Notice period?", "GB", facts())).toBeNull();
  });
});
