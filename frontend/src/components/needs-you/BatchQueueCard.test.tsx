import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { BatchQueueCard } from "./BatchQueueCard";

const listApplications = vi.fn();

vi.mock("@/lib/api", () => ({
  listApplications: (...a: unknown[]) => listApplications(...a),
  recordApplicationEvent: vi.fn(),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const chip = (kind: string, label: string, reason = "default") => ({ kind, label, reason });

function respond(by: Record<string, number>, rows?: unknown[]) {
  listApplications.mockResolvedValue({
    applications: rows ?? [
      { id: 1, job_title: "Greenhouse Job", queue_position: 1, browser: chip("job360", "I'll use Job360's browser") },
      {
        id: 2, job_title: "LinkedIn Job", queue_position: 2,
        browser: chip("own_browser", "Your own browser: runs when your PC is on", "linkedin_own_browser"),
      },
      {
        id: 3, job_title: "Stuck Job", queue_position: 3,
        browser: chip("own_browser", "Needs your own browser: turn your PC on", "job360_login_required"),
      },
    ],
    total: 3,
    queue_summary: { queued: 3, by_browser: by },
  });
}

describe("BatchQueueCard browser chip and filter", () => {
  beforeEach(() => listApplications.mockReset());

  it("shows a chip per job with its text and kind, and the reason in plain words", async () => {
    respond({ job360: 1, own_browser: 2, human: 0 });
    render(<BatchQueueCard />);
    const a = await screen.findByTestId("queue-browser-1");
    expect(a).toHaveTextContent("I'll use Job360's browser");
    expect(a).toHaveAttribute("data-kind", "job360");
    const b = screen.getByTestId("queue-browser-2");
    expect(b).toHaveTextContent("Your own browser: runs when your PC is on");
    expect(b).toHaveAttribute("data-kind", "own_browser");
    expect(screen.getByTestId("queue-browser-why-3")).toHaveTextContent("Job360's browser hit a sign-in wall");
  });

  it("shows the counts from the whole queue and narrows the list", async () => {
    respond({ job360: 1, own_browser: 2, human: 0 });
    render(<BatchQueueCard />);
    await screen.findByTestId("queue-filter");
    expect(screen.getByTestId("queue-filter-all")).toHaveTextContent("All 3");
    expect(screen.getByTestId("queue-filter-job360")).toHaveTextContent("Job360's browser 1");
    expect(screen.getByTestId("queue-filter-own_browser")).toHaveTextContent("Your own browser 2");
    expect(screen.getByTestId("queue-filter-all")).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(screen.getByTestId("queue-filter-own_browser"));
    expect(screen.getByTestId("queue-filter-own_browser")).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByTestId("batch-row-1")).toBeNull();
    expect(screen.getByTestId("batch-row-2")).toBeInTheDocument();
    expect(screen.getByTestId("batch-row-3")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("queue-filter-all"));
    expect(screen.getByTestId("batch-row-1")).toBeInTheDocument();
  });

  it("hides Only you at 0 and shows it above 0", async () => {
    respond({ job360: 1, own_browser: 2, human: 0 });
    const { unmount } = render(<BatchQueueCard />);
    await screen.findByTestId("queue-filter");
    expect(screen.queryByTestId("queue-filter-human")).toBeNull();
    unmount();

    respond({ job360: 1, own_browser: 1, human: 1 });
    render(<BatchQueueCard />);
    expect(await screen.findByTestId("queue-filter-human")).toHaveTextContent("Only you 1");
  });
});
