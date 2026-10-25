import { describe, expect, it, vi, beforeEach } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Navbar } from "./Navbar";
import { Sidebar } from "./Sidebar";

const listAsks = vi.fn();
const getAssistantSettings = vi.fn();
const getReadyToSend = vi.fn();

vi.mock("next/navigation", () => ({ usePathname: () => "/" }));
vi.mock("next-themes", () => ({
  useTheme: () => ({ theme: "system", setTheme: vi.fn() }),
}));
vi.mock("@/components/layout/AuthProvider", () => ({
  useAuth: () => ({ user: { email: "a@b.co" }, loading: false, logout: vi.fn() }),
}));
vi.mock("@/lib/api", () => ({
  ASKS_CHANGED_EVENT: "job360:asks-changed",
  listAsks: (...a: unknown[]) => listAsks(...a),
  getAssistantSettings: (...a: unknown[]) => getAssistantSettings(...a),
  getReadyToSend: (...a: unknown[]) => getReadyToSend(...a),
}));

// n open asks, each on its own job (the badge counts jobs, not asks).
const ask = (id: number, application_id: number | null, over: Record<string, unknown> = {}) => ({
  id, application_id, question: "Q?", context: "", job_title: "Dev", job_company: "Acme", ...over,
});
const jobs = (n: number) => Array.from({ length: n }, (_, i) => ask(i + 1, 100 + i));

beforeEach(() => {
  listAsks.mockReset();
  getAssistantSettings.mockReset().mockResolvedValue({ waiting: [] });
  getReadyToSend.mockReset().mockResolvedValue({ total: 0 });
});

// Redesign slice 1: on desktop the Sidebar owns the nav, below md the Navbar's
// drawer does. Both share one hook (useOpenAsks), so the badge is checked in
// the drawer (opened) AND in the sidebar.
const openDrawer = () =>
  fireEvent.click(screen.getByRole("button", { name: /open navigation menu/i }));

describe("Navbar drawer — Needs you badge", () => {
  it("shows the open count when there are open asks", async () => {
    listAsks.mockResolvedValue({ asks: jobs(3) });
    render(<Navbar />);
    await waitFor(() => expect(listAsks).toHaveBeenCalled());
    openDrawer();
    const badge = await screen.findByTestId("needs-you-badge");
    expect(badge).toHaveTextContent("3");
    expect(badge).toHaveAttribute("aria-label", "3 waiting");
    expect(screen.getByRole("link", { name: /Needs you/ })).toHaveAttribute("href", "/needs-you");
  });

  it("counts the setting changes waiting for the user's OK with the open asks", async () => {
    listAsks.mockResolvedValue({ asks: jobs(2) });
    getAssistantSettings.mockResolvedValue({ waiting: [{ id: 1 }, { id: 2 }] });
    render(<Navbar />);
    await waitFor(() => expect(getAssistantSettings).toHaveBeenCalled());
    openDrawer();
    expect(await screen.findByTestId("needs-you-badge")).toHaveTextContent("4");
  });

  it("counts the applications ready to send (a counts-only read) with the rest", async () => {
    listAsks.mockResolvedValue({ asks: jobs(1) });
    getReadyToSend.mockResolvedValue({ total: 3 });
    render(<Navbar />);
    await waitFor(() => expect(getReadyToSend).toHaveBeenCalledWith({ limit: 0 }));
    openDrawer();
    expect(await screen.findByTestId("needs-you-badge")).toHaveTextContent("4");
  });

  it("a failed settings read still shows the open asks", async () => {
    listAsks.mockResolvedValue({ asks: jobs(2) });
    getAssistantSettings.mockRejectedValue(new Error("down"));
    render(<Navbar />);
    await waitFor(() => expect(getAssistantSettings).toHaveBeenCalled());
    openDrawer();
    expect(await screen.findByTestId("needs-you-badge")).toHaveTextContent("2");
  });

  it("counts jobs, not asks: many asks on one job are one, practice asks are left out, no-job asks count each", async () => {
    listAsks.mockResolvedValue({
      asks: [
        ask(1, 7), ask(2, 7), ask(3, 7), ask(4, 8),
        ask(5, 9, { question: "SIMULATION: do not apply" }),
        ask(6, null), ask(7, null),
      ],
    });
    render(<Navbar />);
    await waitFor(() => expect(listAsks).toHaveBeenCalled());
    openDrawer();
    expect(await screen.findByTestId("needs-you-badge")).toHaveTextContent("4");
  });

  it("reads every page of open asks", async () => {
    const page1 = Array.from({ length: 50 }, (_, i) => ask(i + 1, 1000 + i));
    listAsks.mockResolvedValueOnce({ asks: page1 }).mockResolvedValueOnce({ asks: [ask(99, 5000)] });
    render(<Navbar />);
    await waitFor(() => expect(listAsks).toHaveBeenCalledTimes(2));
    expect(listAsks).toHaveBeenNthCalledWith(2, "open", 50);
    openDrawer();
    expect(await screen.findByTestId("needs-you-badge")).toHaveTextContent("51");
  });

  it("shows no badge at zero", async () => {
    listAsks.mockResolvedValue({ asks: jobs(0) });
    render(<Navbar />);
    await waitFor(() => expect(listAsks).toHaveBeenCalled());
    openDrawer();
    expect(screen.getByRole("link", { name: /Needs you/ })).toBeInTheDocument();
    expect(screen.queryByTestId("needs-you-badge")).toBeNull();
  });

  it("clears while the user answers on the same page (no navigation)", async () => {
    listAsks.mockResolvedValue({ asks: jobs(3) });
    render(<Navbar />);
    await waitFor(() => expect(listAsks).toHaveBeenCalled());
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
    listAsks.mockResolvedValue({ asks: jobs(2) });
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
