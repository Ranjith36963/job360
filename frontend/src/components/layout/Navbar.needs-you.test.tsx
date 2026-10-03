import { describe, expect, it, vi, beforeEach } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import { Navbar } from "./Navbar";

const listAsks = vi.fn();

vi.mock("next/navigation", () => ({ usePathname: () => "/" }));
vi.mock("@/components/layout/AuthProvider", () => ({
  useAuth: () => ({ user: { email: "a@b.co" }, loading: false, logout: vi.fn() }),
}));
vi.mock("@/lib/api", () => ({
  ASKS_CHANGED_EVENT: "job360:asks-changed",
  listAsks: (...a: unknown[]) => listAsks(...a),
}));

beforeEach(() => listAsks.mockReset());

describe("Navbar — Needs you badge", () => {
  it("shows the open count when there are open asks", async () => {
    listAsks.mockResolvedValue({ asks: [], open_count: 3 });
    render(<Navbar />);
    expect((await screen.findAllByTestId("needs-you-badge"))[0]).toHaveTextContent("3");
    expect(screen.getAllByRole("link", { name: /Needs you/ }).length).toBeGreaterThan(0);
  });

  it("shows no badge at zero", async () => {
    listAsks.mockResolvedValue({ asks: [], open_count: 0 });
    render(<Navbar />);
    await waitFor(() => expect(listAsks).toHaveBeenCalled());
    expect(screen.queryByTestId("needs-you-badge")).toBeNull();
  });

  it("clears while the user answers on the same page (no navigation)", async () => {
    listAsks.mockResolvedValue({ asks: [], open_count: 3 });
    render(<Navbar />);
    expect((await screen.findAllByTestId("needs-you-badge"))[0]).toHaveTextContent("3");
    act(() => {
      window.dispatchEvent(new CustomEvent("job360:asks-changed", { detail: 0 }));
    });
    expect(screen.queryByTestId("needs-you-badge")).toBeNull();
  });
});
