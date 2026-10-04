import { describe, it, expect } from "vitest";
import { closedSetLabel } from "./closed-sets";

describe("closedSetLabel", () => {
  it("gives plain words for known members", () => {
    expect(closedSetLabel("company_site")).toBe("Company site");
    expect(closedSetLabel("linkedin_easy_apply")).toBe("LinkedIn Easy Apply");
    expect(closedSetLabel("pasted_by_user")).toBe("You pasted it");
    expect(closedSetLabel("visa_sponsor_list")).toBe("Visa-sponsor list");
    expect(closedSetLabel("company_careers")).toBe("Company careers page");
    expect(closedSetLabel("job_ad")).toBe("Job ad");
  });

  it("humanises an unknown member and keeps legacy free text as-is", () => {
    expect(closedSetLabel("some_new_thing")).toBe("Some new thing");
    expect(closedSetLabel("Careers page form")).toBe("Careers page form");
  });

  it("null / empty is Not set", () => {
    expect(closedSetLabel(null)).toBe("Not set");
    expect(closedSetLabel("")).toBe("Not set");
  });
});
