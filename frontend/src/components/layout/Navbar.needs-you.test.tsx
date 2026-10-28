import { describe, expect, it, vi, beforeEach } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Navbar } from "./Navbar";
import { Sidebar } from "./Sidebar";
import { countNeedsYou, listAllOpenAsks } from "@/lib/needs-you-items";

// The badge is the server's exact total (GET /api/needs-you-count, owner 2026-10-27): the hook shows the
// number as given and reads nothing else, so it can never differ from the Needs-you page's own total.
const getNeedsYouCount = vi.fn();
const listAsks = vi.fn();

vi.mock("next/navigation", () => ({ usePathname: () => "/" }));
vi.mock("next-themes", () => ({
  useTheme: () => ({ theme: "system", setTheme: vi.fn() }),
}));
vi.mock("@/components/layout/AuthProvider", () => ({
  useAuth: () => ({ user: { email: "a@b.co" }, loading: false, logout: vi.fn() }),
}));
vi.mock("@/lib/api", () => ({
  ASKS_CHANGED_EVENT: "job360:asks-changed",
  getNeedsYouCount: (...a: unknown[]) => getNeedsYouCount(...a),
  listAsks: (...a: unknown[]) => listAsks(...a),
  // The setup strip in the same shell reads these; the badge no longer does.
  getAssistantSettings: async () => ({ waiting: [] }),
}));

const total = (n: number) => ({ n, jobs: n, other: 0 });

beforeEach(() => {
  getNeedsYouCount.mockReset();
  listAsks.mockReset();
});

// n open asks, each on its own job. The server counts these by the page's rules (backend
// services/applications/needs_you_count.py); the two tests below pin the page's side of those rules.
const ask = (id: number, application_id: number | null, over: Record<string, unknown> = {}) => ({
  id, application_id, question: "Q?", context: "", job_title: "Dev", job_company: "Acme", ...over,
});

// Redesign slice 1: on desktop the Sidebar owns the nav, below md the Navbar's
// drawer does. Both share one hook (useOpenAsks), so the badge is checked in
// the drawer (opened) AND in the sidebar.
const openDrawer = () =>
  fireEvent.click(screen.getByRole("button", { name: /open navigation menu/i }));

describe("Navbar drawer — Needs you badge", () => {
  it("shows the server's total in the badge", async () => {
    getNeedsYouCount.mockResolvedValue(total(3));
    render(<Navbar />);
    await waitFor(() => expect(getNeedsYouCount).toHaveBeenCalled());
    openDrawer();
    const badge = await screen.findByTestId("needs-you-badge");
    expect(badge).toHaveTextContent("3");
    expect(badge).toHaveAttribute("aria-label", "3 waiting");
    expect(screen.getByRole("link", { name: /Needs you/ })).toHaveAttribute("href", "/needs-you");
  });

  it("shows the number as given, with the jobs and the other items already added up", async () => {
    getNeedsYouCount.mockResolvedValue({ n: 9, jobs: 4, other: 5 });
    render(<Navbar />);
    await waitFor(() => expect(getNeedsYouCount).toHaveBeenCalledTimes(1));
    openDrawer();
    expect(await screen.findByTestId("needs-you-badge")).toHaveTextContent("9");
  });

  it("makes one request for the Sidebar and the drawer together", async () => {
    getNeedsYouCount.mockResolvedValue(total(2));
    render(
      <>
        <Sidebar />
        <Navbar />
      </>,
    );
    await waitFor(() => expect(getNeedsYouCount).toHaveBeenCalled());
    await act(async () => {
      await new Promise((r) => setTimeout(r, 30));
    });
    expect(getNeedsYouCount).toHaveBeenCalledTimes(1);
  });

  it("a failed read shows no badge", async () => {
    getNeedsYouCount.mockRejectedValue(new Error("down"));
    render(<Navbar />);
    await waitFor(() => expect(getNeedsYouCount).toHaveBeenCalled());
    openDrawer();
    expect(screen.getByRole("link", { name: /Needs you/ })).toBeInTheDocument();
    expect(screen.queryByTestId("needs-you-badge")).toBeNull();
  });

  it("counts jobs, not asks: many asks on one job are one, practice asks are left out, no-job asks count each", async () => {
    const asks = [
      ask(1, 7), ask(2, 7), ask(3, 7), ask(4, 8),
      ask(5, 9, { question: "SIMULATION: do not apply" }),
      ask(6, null), ask(7, null),
    ] as never[];
    // The page's rule (what the server mirrors) gives 4, and the badge shows exactly the server's number.
    const rule = countNeedsYou({ open: asks, ready: [], waiting: 0 });
    expect(rule).toMatchObject({ n: 4, jobs: 2, other: 2 });
    getNeedsYouCount.mockResolvedValue({ n: rule.n, jobs: rule.jobs, other: rule.other });
    render(<Navbar />);
    await waitFor(() => expect(getNeedsYouCount).toHaveBeenCalled());
    openDrawer();
    expect(await screen.findByTestId("needs-you-badge")).toHaveTextContent("4");
  });

  it("reads every page of open asks", async () => {
    // The page (which announces its own total on Needs you) reads every page, not only the first.
    const page1 = Array.from({ length: 50 }, (_, i) => ask(i + 1, 1000 + i));
    listAsks.mockResolvedValueOnce({ asks: page1 }).mockResolvedValueOnce({ asks: [ask(99, 5000)] });
    const all = await listAllOpenAsks();
    expect(listAsks).toHaveBeenCalledTimes(2);
    expect(listAsks).toHaveBeenNthCalledWith(2, "open", 50);
    expect(countNeedsYou({ open: all, ready: [], waiting: 0 }).n).toBe(51);
  });

  it("shows no badge at zero", async () => {
    getNeedsYouCount.mockResolvedValue(total(0));
    render(<Navbar />);
    await waitFor(() => expect(getNeedsYouCount).toHaveBeenCalled());
    openDrawer();
    expect(screen.getByRole("link", { name: /Needs you/ })).toBeInTheDocument();
    expect(screen.queryByTestId("needs-you-badge")).toBeNull();
  });

  it("clears while the user answers on the same page (no navigation)", async () => {
    getNeedsYouCount.mockResolvedValue(total(3));
    render(<Navbar />);
    await waitFor(() => expect(getNeedsYouCount).toHaveBeenCalled());
    openDrawer();
    expect(await screen.findByTestId("needs-you-badge")).toHaveTextContent("3");
    act(() => {
      window.dispatchEvent(new CustomEvent("job360:asks-changed", { detail: 0 }));
    });
    expect(screen.queryByTestId("needs-you-badge")).toBeNull();
  });
});

describe("Sidebar — Needs you badge", () => {
  it("shows the same badge, and clears it on the same event", async () => {
    getNeedsYouCount.mockResolvedValue(total(2));
    render(<Sidebar />);
    const badge = await screen.findByTestId("needs-you-badge");
    expect(badge).toHaveTextContent("2");
    expect(badge).toHaveAttribute("aria-label", "2 waiting");
    expect(screen.getByRole("link", { name: /Needs you/ })).toHaveAttribute("href", "/needs-you");
    act(() => {
      window.dispatchEvent(new CustomEvent("job360:asks-changed", { detail: 0 }));
    });
    expect(screen.queryByTestId("needs-you-badge")).toBeNull();
  });
});
