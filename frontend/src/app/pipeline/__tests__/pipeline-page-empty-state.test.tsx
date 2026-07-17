/**
 * M17 regression test for PipelinePage.
 *
 * The board vs EmptyState decision used to key off `total` — a count
 * derived from the independently-fetched `/api/pipeline/counts` response —
 * while the KanbanBoard renders the separately-fetched `applications` array.
 * If the two responses diverge (or `counts` silently returns a stale/zero
 * or non-zero value), the page could show an empty board when applications
 * exist, or show the "No applications yet" empty state when applications
 * are actually present. The fix keys the decision on `applications.length`
 * (the array the board actually maps over) instead.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import type { PipelineApplication } from "@/lib/types";
import PipelinePage from "../page";

const getPipelineApplicationsMock = vi.fn();
const getPipelineCountsMock = vi.fn();
const getPipelineRemindersMock = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getPipelineApplications: (...args: unknown[]) =>
    getPipelineApplicationsMock(...args),
  getPipelineCounts: (...args: unknown[]) => getPipelineCountsMock(...args),
  getPipelineReminders: (...args: unknown[]) =>
    getPipelineRemindersMock(...args),
  advancePipelineStage: vi.fn(),
}));

// Board internals (dnd-kit, notes editor, timeline fetches) are irrelevant
// to the show-board-vs-empty decision under test — stub the board itself.
vi.mock("@/components/pipeline/KanbanBoard", () => ({
  KanbanBoard: ({ applications }: { applications: PipelineApplication[] }) => (
    <div data-testid="kanban-board">{applications.length} cards</div>
  ),
}));

function makeApp(overrides: Partial<PipelineApplication> = {}): PipelineApplication {
  return {
    job_id: 1,
    user_id: "u1",
    title: "Software Engineer",
    company: "Acme Ltd",
    stage: "applied",
    notes: null,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    ...overrides,
  } as PipelineApplication;
}

beforeEach(() => {
  getPipelineApplicationsMock.mockReset();
  getPipelineCountsMock.mockReset();
  getPipelineRemindersMock.mockReset();
  getPipelineRemindersMock.mockResolvedValue({ reminders: [] });
});

describe("PipelinePage — M17 board/empty-state source of truth", () => {
  it("shows EmptyState (not an empty board) when applications=[] even though counts.total>0", async () => {
    getPipelineApplicationsMock.mockResolvedValue({ applications: [] });
    // Diverged/stale counts response — total > 0 despite no applications.
    getPipelineCountsMock.mockResolvedValue({ applied: 3, offer: 1 });

    render(<PipelinePage />);

    await waitFor(() => {
      expect(screen.getByText(/no applications yet/i)).toBeInTheDocument();
    });
    expect(screen.queryByTestId("kanban-board")).not.toBeInTheDocument();
  });

  it("shows the KanbanBoard when applications is non-empty, regardless of counts", async () => {
    getPipelineApplicationsMock.mockResolvedValue({
      applications: [makeApp({ job_id: 7, title: "Backend Engineer" })],
    });
    // Counts under-reporting (e.g. stale) must not hide a populated board.
    getPipelineCountsMock.mockResolvedValue({});

    render(<PipelinePage />);

    await waitFor(() => {
      expect(screen.getByTestId("kanban-board")).toBeInTheDocument();
    });
    expect(screen.getByText("1 cards")).toBeInTheDocument();
    expect(screen.queryByText(/no applications yet/i)).not.toBeInTheDocument();
  });

  it("fetches applications and counts together via Promise.all (no serial race)", async () => {
    let appsResolved = false;
    let countsStartedBeforeAppsResolved = false;

    getPipelineApplicationsMock.mockImplementation(
      () =>
        new Promise((resolve) => {
          setTimeout(() => {
            appsResolved = true;
            resolve({ applications: [] });
          }, 10);
        })
    );
    getPipelineCountsMock.mockImplementation(() => {
      // If this fires before the applications promise resolves, the two
      // fetches were started concurrently (Promise.all), not chained.
      if (!appsResolved) countsStartedBeforeAppsResolved = true;
      return Promise.resolve({});
    });

    render(<PipelinePage />);

    await waitFor(() => {
      expect(screen.getByText(/no applications yet/i)).toBeInTheDocument();
    });
    expect(countsStartedBeforeAppsResolved).toBe(true);
  });
});
