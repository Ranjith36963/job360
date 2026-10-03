import { describe, expect, it, vi, beforeEach } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Navbar } from "./Navbar";
import { Sidebar } from "./Sidebar";

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
  listAsks: (...a: unknown[]) => listAsks(...a),
}));

beforeEach(() => listAsks.mockReset());

// Redesign slice 1: on desktop the Sidebar owns the nav, below md the Navbar's
// drawer does. Both share one hook (useOpenAsks), so the badge is checked in
// the drawer (opened) AND in the sidebar.
const openDrawer = () =>
  fireEvent.click(screen.getByRole("button", { name: /open navigation menu/i }));

describe("Navbar drawer — Needs you badge", () => {
  it("shows the open count when there are open asks", async () => {
    listAsks.mockResolvedValue({ asks: [], open_count: 3 });
    render(<Navbar />);
    await waitFor(() => expect(listAsks).toHaveBeenCalled());
    openDrawer();
    const badge = await screen.findByTestId("needs-you-badge");
    expect(badge).toHaveTextContent("3");
    expect(badge).toHaveAttribute("aria-label", "3 waiting");
    expect(screen.getByRole("link", { name: /Needs you/ })).toHaveAttribute("href", "/needs-you");
  });

  it("shows no badge at zero", async () => {
    listAsks.mockResolvedValue({ asks: [], open_count: 0 });
    render(<Navbar />);
    await waitFor(() => expect(listAsks).toHaveBeenCalled());
    openDrawer();
    expect(screen.getByRole("link", { name: /Needs you/ })).toBeInTheDocument();
    expect(screen.queryByTestId("needs-you-badge")).toBeNull();
  });

  it("clears while the user answers on the same page (no navigation)", async () => {
    listAsks.mockResolvedValue({ asks: [], open_count: 3 });
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
    listAsks.mockResolvedValue({ asks: [], open_count: 2 });
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
