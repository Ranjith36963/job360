import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { KanbanBoard } from "./KanbanBoard";
import type { PipelineApplication } from "@/lib/types";

// Minimal mocks for child components used inside KanbanBoard
vi.mock("./NotesEditor", () => ({
  NotesEditor: () => <button aria-label="Edit notes">Notes</button>,
}));
vi.mock("./PipelineFilterPanel", () => ({
  PipelineFilterPanel: ({ onFilter, applications }: { onFilter: (a: PipelineApplication[]) => void; applications: PipelineApplication[] }) => {
    // Call onFilter with all apps immediately so the board renders them
    onFilter(applications);
    return null;
  },
}));
vi.mock("@/lib/api", () => ({
  getApplicationTimeline: vi.fn().mockResolvedValue({ timeline: [] }),
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
  };
}

describe("KanbanBoard keyboard a11y", () => {
  it("renders keyboard hint for screen readers", () => {
    render(
      <KanbanBoard
        applications={[makeApp()]}
        onAdvance={vi.fn()}
      />
    );
    const hint = document.getElementById("kanban-keyboard-hint");
    expect(hint).not.toBeNull();
    expect(hint?.textContent).toMatch(/Space/i);
    expect(hint?.textContent).toMatch(/Arrow keys/i);
    expect(hint?.textContent).toMatch(/Escape/i);
  });

  it("each application card has draggable attributes", () => {
    render(
      <KanbanBoard
        applications={[makeApp({ job_id: 42, title: "ML Engineer" })]}
        onAdvance={vi.fn()}
      />
    );
    // useDraggable adds role="button" and aria-roledescription to the node
    const draggables = document.querySelectorAll('[aria-roledescription="draggable"]');
    expect(draggables.length).toBeGreaterThan(0);
  });

  it("card is keyboard focusable (tabIndex 0)", () => {
    render(
      <KanbanBoard
        applications={[makeApp({ job_id: 5, title: "Data Scientist" })]}
        onAdvance={vi.fn()}
      />
    );
    const draggable = document.querySelector('[aria-roledescription="draggable"]') as HTMLElement;
    expect(draggable?.tabIndex).toBe(0);
  });

  it("renders all 5 stage columns as droppable regions", () => {
    render(
      <KanbanBoard applications={[]} onAdvance={vi.fn()} />
    );
    // Each column has an id kanban-col-{stage}
    ["applied", "outreach", "interview", "offer", "rejected"].forEach((stage) => {
      expect(document.getElementById(`kanban-col-${stage}`)).not.toBeNull();
    });
  });

  it("board region has accessible label", () => {
    render(<KanbanBoard applications={[]} onAdvance={vi.fn()} />);
    const regions = screen.getAllByRole("region", {
      name: /application pipeline board/i,
    });
    expect(regions.length).toBeGreaterThan(0);
  });

  it("advance button has aria-label naming the job and target stage", () => {
    render(
      <KanbanBoard
        applications={[makeApp({ job_id: 10, title: "DevOps Engineer", stage: "applied" })]}
        onAdvance={vi.fn()}
      />
    );
    // Desktop + mobile renders both show the button — check at least one exists
    const btns = screen.getAllByRole("button", { name: /advance DevOps Engineer to outreach/i });
    expect(btns.length).toBeGreaterThan(0);
  });
});
