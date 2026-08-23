/**
 * Profile completeness meter — no double-counting a single answer.
 *
 * THE BUG, as measured. calcCompleteness paid a typed preferred job title
 * TWICE: once for the 15% "Has job titles" bucket (`prefTitles.length > 0`),
 * and again for the 15% "Has preferences" bucket, which OR'd in that exact
 * same `prefTitles.length > 0` check. One answer, two buckets, 30% of the
 * meter for something that should only earn 15%.
 *
 * This test pins a profile shape with ONLY a CV (40%) and ONE typed job title
 * (15%) — nothing else filled in. The honest total is 55%. The bug would have
 * read 70%, because the same job title paid for "Has preferences" too even
 * though no actual preference field (work arrangement, experience level,
 * about me) was ever set.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import ProfilePage from "../page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getProfile: vi.fn().mockResolvedValue({
    summary: {
      is_complete: false,
      job_titles: [], // no CV-extracted titles
      skills_count: 0,
      cv_length: 100, // has a CV -> +40
      has_linkedin: false,
      has_github: false,
      education: [],
      experience_level: "",
    },
    preferences: {
      target_job_titles: ["Data Scientist"], // the ONE typed answer -> +15
      additional_skills: [],
      // Nothing here counts as a real preference: "any" and "" are both
      // "not chosen" (rule #29), and about_me is blank.
      work_arrangement: "any",
      experience_level: "",
      about_me: "",
    },
    cv_detail: null,
    skill_tiers: null,
    skill_esco: {},
  }),
}));

describe("ProfilePage — completeness meter does not double-count", () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  it("a CV plus one typed job title reads 55%, not 70%", async () => {
    render(<ProfilePage />);

    // 40 (CV) + 15 (job title, counted once) = 55. If the double-count bug
    // were still present, the same title would also satisfy "Has
    // preferences" and this would read 70%.
    expect(await screen.findByText("55%")).toBeInTheDocument();
  });
});
