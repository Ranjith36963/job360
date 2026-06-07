/**
 * Search Latest Jobs handoff (Profile page).
 *
 * The "Search Latest Jobs" button is the producer side of a one-shot
 * cross-page signal: it sets a sessionStorage flag, then navigates to the
 * dashboard, which reads the flag on mount and fires the search. This test
 * pins the producer contract (flag set + navigate) so a future refactor can't
 * silently break the handoff — the part most likely to regress without anyone
 * noticing, since both halves live in different files.
 *
 * The full end-to-end handoff (flag -> dashboard auto-search) is proven by the
 * Playwright drive; this is the cheap CI guard for the contract.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import ProfilePage from "../page";

const mockPush = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
}));

// Keep the rest of the api module real (none of it is called in this test —
// the only mount-time call is getProfile, and the version drawer stays
// closed) and just stub getProfile to return a complete-enough profile so the
// button's `percent > 0` gate is satisfied.
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getProfile: vi.fn().mockResolvedValue({
    summary: {
      is_complete: true,
      job_titles: ["ML Engineer"],
      skills_count: 5,
      cv_length: 100,
      has_linkedin: false,
      has_github: false,
      education: [],
      experience_level: "",
    },
    preferences: { target_job_titles: ["ML Engineer"], additional_skills: ["python"] },
    cv_detail: null,
    skill_tiers: null,
    skill_esco: {},
  }),
}));

describe("ProfilePage — Search Latest Jobs handoff", () => {
  beforeEach(() => {
    mockPush.mockClear();
    sessionStorage.clear();
  });

  it("sets the one-shot search flag and routes to the dashboard", async () => {
    render(<ProfilePage />);

    const btn = await screen.findByRole("button", { name: /search latest jobs/i });
    // Enabled only once the profile loads with content (percent > 0).
    await waitFor(() => expect(btn).not.toBeDisabled());

    fireEvent.click(btn);

    expect(sessionStorage.getItem("job360_run_search")).toBe("1");
    expect(mockPush).toHaveBeenCalledWith("/dashboard");
  });

  it("disables the button when the profile is empty (no profile-less search)", async () => {
    const api = await import("@/lib/api");
    vi.mocked(api.getProfile).mockRejectedValueOnce(new Error("404 not found"));

    render(<ProfilePage />);

    const btn = await screen.findByRole("button", { name: /search latest jobs/i });
    await waitFor(() => expect(btn).toBeDisabled());
  });
});
