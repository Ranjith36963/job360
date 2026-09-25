/**
 * The Take back flow on the profile page (owner decision, 2026-09-25): the
 * mark says who changed the field and what it was; "Take back" calls the
 * per-user route and the page re-renders from the profile it returns — the
 * field shows the earlier value and the mark is gone.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import ProfilePage from "../page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

const getProfile = vi.fn();
const takeBackProfileEdit = vi.fn();
const keepProfileEdit = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getProfile: () => getProfile(),
  takeBackProfileEdit: (path: string) => takeBackProfileEdit(path),
  keepProfileEdit: (path: string) => keepProfileEdit(path),
  listLessons: vi.fn().mockResolvedValue({ lessons: [], total: 0 }),
}));

function profile(workArrangement: string, edits: unknown[]) {
  return {
    summary: {
      is_complete: true,
      job_titles: [],
      skills_count: 0,
      cv_length: 100,
      has_linkedin: true,
      has_github: true,
      education: [],
      experience_level: "",
    },
    preferences: { work_arrangement: workArrangement },
    cv_detail: null,
    skill_tiers: {},
    skill_esco: {},
    agent_edits: edits,
  };
}

const EDIT = {
  path: "preferences.work_arrangement",
  value: "remote",
  previous_value: "hybrid",
  set_by: "agent:Claude",
  set_at: "2026-09-25T10:00:00Z",
};

describe("ProfilePage — Take back", () => {
  beforeEach(() => {
    sessionStorage.clear();
    getProfile.mockReset();
    takeBackProfileEdit.mockReset();
    keepProfileEdit.mockReset();
  });

  it("takes back the assistant's change and drops the mark", async () => {
    getProfile.mockResolvedValue(profile("remote", [EDIT]));
    takeBackProfileEdit.mockResolvedValue(profile("hybrid", []));
    render(<ProfilePage />);

    const mark = await screen.findByTestId("agent-edit-mark");
    expect(mark.textContent).toBe("Changed by Claude · was hybrid");
    expect(screen.getByRole("link", { name: "1 preference set by your assistant" })).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Take back Claude's change" }));
    await waitFor(() =>
      expect(takeBackProfileEdit).toHaveBeenCalledWith("preferences.work_arrangement")
    );
    await waitFor(() => expect(screen.queryByTestId("agent-edit-mark")).toBeNull());
    expect(screen.queryByText(/set by your assistant/)).toBeNull();
  });

  it("keeps the assistant's change: the value stays and the mark goes", async () => {
    getProfile.mockResolvedValue(profile("remote", [EDIT]));
    keepProfileEdit.mockResolvedValue(profile("remote", []));
    render(<ProfilePage />);

    await screen.findByTestId("agent-edit-mark");
    fireEvent.click(screen.getByRole("button", { name: "Keep Claude's change" }));
    await waitFor(() => expect(keepProfileEdit).toHaveBeenCalledWith("preferences.work_arrangement"));
    expect(takeBackProfileEdit).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByTestId("agent-edit-mark")).toBeNull());
    expect(screen.queryByText(/set by your assistant/)).toBeNull();
  });
});
