import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import AccountSettingsPage from "../page";
import { ApiError } from "@/lib/api-error";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

const mockDownloadMyData = vi.fn();

vi.mock("@/lib/api", () => ({
  changePassword: vi.fn(),
  changeEmail: vi.fn(),
  deleteAccount: vi.fn(),
  logout: vi.fn(),
  me: vi.fn().mockResolvedValue(null),
  setTimezone: vi.fn(),
  resendVerificationEmail: vi.fn(),
  downloadMyData: (...args: unknown[]) => mockDownloadMyData(...args),
  getCalendarFeed: () => Promise.resolve({ exists: false, created_at: null }),
  createCalendarFeed: vi.fn(),
  revokeCalendarFeed: vi.fn(),
}));

describe("Download everything", () => {
  beforeEach(() => {
    mockDownloadMyData.mockReset().mockResolvedValue(undefined);
  });

  it("calls the export download and confirms", async () => {
    const user = userEvent.setup();
    render(<AccountSettingsPage />);
    await user.click(screen.getByTestId("download-everything"));
    await waitFor(() => expect(mockDownloadMyData).toHaveBeenCalledTimes(1));
    expect(await screen.findByText(/Downloaded job360-my-data\.json/)).toBeInTheDocument();
  });

  it("shows the server message when rate limited", async () => {
    mockDownloadMyData.mockRejectedValue(new ApiError(429, "too many exports this hour"));
    const user = userEvent.setup();
    render(<AccountSettingsPage />);
    await user.click(screen.getByTestId("download-everything"));
    expect(await screen.findByText(/too many exports this hour/)).toBeInTheDocument();
  });
});
