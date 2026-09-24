/**
 * Profile header's one-line status — no double-counting a single answer.
 *
 * THE BUG (as originally measured against the old %/"Almost there" meter,
 * removed 2026-09-24 in favour of this one line naming what is missing).
 * calcCompleteness paid a typed preferred job title TWICE: once for the
 * "Has job titles" bucket (`prefTitles.length > 0`), and again for the "Has
 * preferences" bucket, which OR'd in that exact same check. One answer, two
 * buckets — "preferences" read as satisfied when nothing about work
 * arrangement, experience level or about_me had actually been set.
 *
 * This test pins a profile shape with ONLY a CV and ONE typed job title —
 * nothing else filled in. The honest missing list is "skills, preferences,
 * LinkedIn, GitHub" (a CV and job titles are both covered). The bug would
 * have dropped "preferences" from that list too, because the same typed
 * title silently satisfied it.
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
      cv_length: 100, // has a CV — not missing
      has_linkedin: false,
      has_github: false,
      education: [],
      experience_level: "",
    },
    preferences: {
      target_job_titles: ["Data Scientist"], // the ONE typed answer — job titles not missing
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

describe("ProfilePage header — does not double-count a typed job title", () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  it("names skills, preferences, LinkedIn and GitHub as missing — not a CV or job titles", async () => {
    render(<ProfilePage />);

    // If the double-count bug were still present, the typed job title would
    // also satisfy "preferences" and it would be missing from this line.
    const header = await screen.findByText(/^To finish:/);
    expect(header).toHaveTextContent(
      "To finish: add skills, add preferences, add LinkedIn, add GitHub"
    );
    expect(header).not.toHaveTextContent("add a CV");
    expect(header).not.toHaveTextContent("add job titles");
  });
});
