import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { SetupCard, SetupProgress, SetupTab } from "./SetupProgress";
import { doneDate } from "@/lib/assistant-tab";

const getAssistantSettings = vi.fn();
vi.mock("@/lib/api", () => ({ getAssistantSettings: (...a: unknown[]) => getAssistantSettings(...a), updateProfileFields: vi.fn() }));
vi.mock("next/navigation", () => ({ usePathname: () => "/" }));
vi.mock("@/components/layout/AuthProvider", () => ({ useAuth: () => ({ user: { id: "u" } }) }));

const D = "2026-10-04T09:00:00+00:00";
const progress = (names: string[], next = "") => ({
  done: names.length,
  total: 6,
  next,
  rounds: Object.fromEntries(names.map((n) => [n, { done_at: D }])),
});
const withProgress = (p: ReturnType<typeof progress>) => getAssistantSettings.mockResolvedValue({ setup_progress: p });

beforeEach(() => {
  getAssistantSettings.mockReset();
});

describe("SetupProgress", () => {
  it("0 of 6: all dots open, the first round is next, how to resume", () => {
    render(<SetupProgress progress={progress([], "you")} />);
    expect(screen.getByRole("heading")).toHaveTextContent("Setup · 0 of 6 done");
    expect(screen.getByTestId("setup-next")).toHaveTextContent("Next: About you. Ask your assistant: run 360");
    expect(screen.getByTestId("setup-round-you")).toHaveAttribute("data-done", "false");
  });

  it("3 of 6: names, done dates and the next round", () => {
    render(<SetupProgress progress={progress(["you", "visa", "logistics"], "equality")} />);
    expect(screen.getByRole("heading")).toHaveTextContent("Setup · 3 of 6 done");
    for (const t of ["About you", "Right to work", "Logistics", "Equality (voluntary)", "Job targets", "Assistant settings"]) {
      expect(screen.getByText(t)).toBeInTheDocument();
    }
    expect(screen.getByTestId("setup-round-visa")).toHaveAttribute("data-done", "true");
    expect(screen.getByTestId("setup-round-visa")).toHaveTextContent(doneDate(D));
    expect(screen.getByTestId("setup-next")).toHaveTextContent("Next: Equality (voluntary). Ask your assistant: run 360");
  });

  it("6 of 6: 'All six done' with the date, no next line", () => {
    render(<SetupProgress progress={progress(["you", "visa", "logistics", "equality", "targets", "settings"])} />);
    expect(screen.getByRole("heading")).toHaveTextContent(`Setup · All six done, ${doneDate(D)}`);
    expect(screen.queryByTestId("setup-next")).toBeNull();
  });
});

describe("SetupCard (Home) and SetupTab", () => {
  it("Home shows the compact card while rounds remain", async () => {
    withProgress(progress(["you", "visa", "logistics"], "equality"));
    render(<SetupCard />);
    expect(await screen.findByTestId("setup-card")).toHaveTextContent("Setup · 3 of 6 done");
  });

  it("Home hides the card at 6 of 6 and when the read fails; the Setup tab stays", async () => {
    withProgress(progress(["you", "visa", "logistics", "equality", "targets", "settings"]));
    const { container, unmount } = render(<SetupCard />);
    await vi.waitFor(() => expect(getAssistantSettings).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
    unmount();
    render(<SetupTab />);
    expect(await screen.findByTestId("setup-progress")).toHaveTextContent("All six done");
  });

  it("a failed read shows no card", async () => {
    getAssistantSettings.mockImplementation(() => Promise.reject(new Error("down")));
    const { container } = render(<SetupCard />);
    await vi.waitFor(() => expect(getAssistantSettings).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it("a failed read on the Setup tab says so instead of loading forever", async () => {
    getAssistantSettings.mockImplementation(() => Promise.reject(new Error("down")));
    render(<SetupTab />);
    expect(await screen.findByTestId("setup-failed")).toHaveTextContent("Could not load your setup progress.");
  });

  it("dates read like the Memory tab ('4 Oct'), whatever the browser language", () => {
    expect(doneDate("2026-10-04T12:00:00Z")).toBe("4 Oct");
    expect(doneDate("nope")).toBe("");
  });
});
