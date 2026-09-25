/**
 * FieldHistory — the per-field "History" link (one history, owner decision
 * 2026-09-25): opens an inline, newest-first list of the human's saves and
 * the assistant's edits. Fetched only when opened.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

const getProfileEditHistory = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getProfileEditHistory: (...args: unknown[]) => getProfileEditHistory(...args),
}));

import { FieldHistory } from "./FieldHistory";

describe("FieldHistory", () => {
  beforeEach(() => getProfileEditHistory.mockReset());

  it("does not fetch until opened, then lists both authors newest first", async () => {
    getProfileEditHistory.mockResolvedValue([
      { value: 45000, set_by: "web", set_at: "2026-09-25T10:00:00Z" },
      { value: 50000, set_by: "agent:Claude", set_at: "2026-09-24T10:00:00Z" },
      { value: null, set_by: "web", set_at: "2026-09-20T10:00:00Z" },
    ]);
    render(<FieldHistory label="Salary Range" paths={["preferences.salary_min"]} />);
    expect(getProfileEditHistory).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "History of Salary Range" }));
    const rows = await screen.findAllByTestId("field-history-row");
    expect(getProfileEditHistory).toHaveBeenCalledWith("preferences.salary_min");
    expect(rows.map((r) => r.textContent)).toEqual([
      expect.stringMatching(/^You · .*2026 · £45k$/),
      expect.stringMatching(/^Claude · .*2026 · £50k$/),
      expect.stringMatching(/^You · .*2026 · cleared$/),
    ]);
  });

  it("merges several paths into one list, labelled, newest first", async () => {
    getProfileEditHistory.mockImplementation(async (path?: string) =>
      String(path).endsWith("_min")
        ? [{ value: 40000, set_by: "web", set_at: "2026-09-20T10:00:00Z" }]
        : [{ value: 60000, set_by: "agent:Claude", set_at: "2026-09-22T10:00:00Z" }]
    );
    render(
      <FieldHistory
        label="Salary Range"
        paths={["preferences.salary_min", "preferences.salary_max"]}
        pathLabels={{ "preferences.salary_min": "Min", "preferences.salary_max": "Max" }}
      />
    );
    fireEvent.click(screen.getByRole("button", { name: "History of Salary Range" }));
    const rows = await screen.findAllByTestId("field-history-row");
    expect(rows.map((r) => r.textContent)).toEqual([
      expect.stringMatching(/^Claude · .* · Max £60k$/),
      expect.stringMatching(/^You · .* · Min £40k$/),
    ]);
  });

  it("says so when a field has no changes yet", async () => {
    getProfileEditHistory.mockResolvedValue([]);
    render(<FieldHistory label="Industries" paths={["preferences.industries"]} />);
    fireEvent.click(screen.getByRole("button", { name: "History of Industries" }));
    expect(await screen.findByText("No changes yet.")).toBeTruthy();
  });
});
