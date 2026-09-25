/**
 * The Connect page's daily-check line must reflect what Job360 actually
 * stored — never a guess. While the profile is loading there is no line and
 * no reset button; if the read fails the page says so in neutral words and
 * never claims "your assistant will offer" (it does not know that).
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import ConnectAgentPage from "./page";

const getProfile = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getProfile: () => getProfile(),
  listTokens: vi.fn().mockResolvedValue([]),
  listGrants: vi.fn().mockResolvedValue([]),
}));

describe("ConnectAgentPage — daily check status", () => {
  beforeEach(() => {
    getProfile.mockReset();
  });

  it("shows no status line and no reset button while the profile is loading", async () => {
    getProfile.mockReturnValue(new Promise(() => {}));
    render(<ConnectAgentPage />);
    await waitFor(() => expect(getProfile).toHaveBeenCalled());
    expect(screen.queryByTestId("daily-check-status")).not.toBeInTheDocument();
    expect(screen.queryByTestId("daily-check-reset")).not.toBeInTheDocument();
    expect(screen.queryByText(/will offer/i)).not.toBeInTheDocument();
  });

  it("shows a neutral line, never 'will offer', when the profile read fails", async () => {
    getProfile.mockRejectedValue(new Error("boom"));
    render(<ConnectAgentPage />);
    const status = await screen.findByTestId("daily-check-status");
    expect(status).toHaveTextContent(/couldn.t load this/i);
    expect(screen.queryByText(/will offer/i)).not.toBeInTheDocument();
    expect(screen.queryByTestId("daily-check-reset")).not.toBeInTheDocument();
  });

  it("shows the declined line and the reset button once the read succeeds", async () => {
    getProfile.mockResolvedValue({ preferences: { daily_check: "declined" } });
    render(<ConnectAgentPage />);
    const status = await screen.findByTestId("daily-check-status");
    expect(status).toHaveTextContent(/you said no/i);
    expect(screen.getByTestId("daily-check-reset")).toBeInTheDocument();
  });
});
