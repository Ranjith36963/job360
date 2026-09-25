/**
 * Profile header lines (owner decision, 2026-09-25).
 *
 * Only a missing CV is "to finish". Preferences are optional and empty means
 * "don't care" (rule #29) — counting them as missing nagged a seeker into
 * inventing constraints. LinkedIn / GitHub appear on a quiet "You can also
 * add" line. And when the assistant has set some preferences, one line says
 * how many and links to the Preferences card.
 *
 * (History: this file used to pin the old six-bucket "To finish" list —
 * CV, job titles, skills, preferences, LinkedIn, GitHub. That list is gone.)
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import ProfilePage from "../page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

const getProfile = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getProfile: () => getProfile(),
  // LessonsList fetches on mount; keep it quiet.
  listLessons: vi.fn().mockResolvedValue({ lessons: [], total: 0 }),
}));

function profile(over: {
  cv_length?: number;
  has_linkedin?: boolean;
  has_github?: boolean;
  agent_edits?: unknown[];
}) {
  return {
    summary: {
      is_complete: false,
      job_titles: [],
      skills_count: 0,
      cv_length: over.cv_length ?? 100,
      has_linkedin: over.has_linkedin ?? false,
      has_github: over.has_github ?? false,
      education: [],
      experience_level: "",
    },
    // Nothing typed at all — and that is NOT "missing" (rule #29).
    preferences: {
      target_job_titles: [],
      additional_skills: [],
      work_arrangement: "",
      experience_level: "",
      about_me: "",
    },
    cv_detail: null,
    skill_tiers: {},
    skill_esco: {},
    agent_edits: over.agent_edits ?? [],
  };
}

describe("ProfilePage header", () => {
  beforeEach(() => {
    sessionStorage.clear();
    getProfile.mockReset();
  });

  it("with a CV and no preferences, the profile is ready — preferences are never 'to finish'", async () => {
    getProfile.mockResolvedValue(profile({}));
    render(<ProfilePage />);
    expect(await screen.findByText("Your profile is ready for your assistant")).toBeTruthy();
    expect(screen.queryByText(/^To finish:/)).toBeNull();
    expect(screen.queryByText(/add preferences/)).toBeNull();
    // LinkedIn / GitHub sit on the quiet optional line instead.
    expect(screen.getByText("You can also add: LinkedIn, GitHub")).toBeTruthy();
  });

  it("names a missing CV as the only thing to finish", async () => {
    getProfile.mockResolvedValue(profile({ cv_length: 0, has_linkedin: true, has_github: true }));
    render(<ProfilePage />);
    const header = await screen.findByText(/^To finish:/);
    expect(header.textContent).toBe("To finish: add a CV");
    expect(screen.queryByText(/You can also add/)).toBeNull();
  });

  it("counts the preferences the assistant set and links to the card", async () => {
    getProfile.mockResolvedValue(
      profile({
        agent_edits: [
          { path: "preferences.salary_min", value: 50000, previous_value: null, set_by: "agent:Claude", set_at: "2026-09-25T10:00:00Z" },
          { path: "preferences.preferred_locations", value: ["Leeds"], previous_value: [], set_by: "token:cli", set_at: "2026-09-25T10:00:00Z" },
          // A CV edit is not a preference.
          { path: "cv_data.location", value: "Leeds", previous_value: "London", set_by: "agent:Claude", set_at: "2026-09-25T10:00:00Z" },
        ],
      })
    );
    render(<ProfilePage />);
    const link = await screen.findByRole("link", { name: "2 preferences set by your assistant" });
    expect(link.getAttribute("href")).toBe("#preferences");
  });

  it("does not count the daily-check answer — it has no field on the card", async () => {
    getProfile.mockResolvedValue(
      profile({
        agent_edits: [
          { path: "preferences.salary_min", value: 50000, previous_value: null, set_by: "agent:Claude", set_at: "2026-09-25T10:00:00Z" },
          { path: "preferences.daily_check", value: "scheduled", previous_value: "", set_by: "agent:Claude", set_at: "2026-09-25T10:00:00Z" },
        ],
      })
    );
    render(<ProfilePage />);
    expect(await screen.findByRole("link", { name: "1 preference set by your assistant" })).toBeTruthy();
  });

  it("shows no assistant line when the assistant set nothing", async () => {
    getProfile.mockResolvedValue(profile({}));
    render(<ProfilePage />);
    await screen.findByText("Your profile is ready for your assistant");
    expect(screen.queryByText(/set by your assistant/)).toBeNull();
  });
});
