import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import AccountSettingsPage from "../page";
import { CONSENT_KEY } from "@/lib/consent";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

const mockMe = vi.fn();
const mockSetAnalyticsConsent = vi.fn();
const mockSyncReplayConsent = vi.fn();

vi.mock("@/lib/api", () => ({
  changePassword: vi.fn(),
  changeEmail: vi.fn(),
  deleteAccount: vi.fn(),
  logout: vi.fn(),
  me: (...args: unknown[]) => mockMe(...args),
  setTimezone: vi.fn(),
  setAnalyticsConsent: (...args: unknown[]) => mockSetAnalyticsConsent(...args),
  resendVerificationEmail: vi.fn(),
  downloadMyData: vi.fn(),
  getCalendarFeed: () => Promise.resolve({ exists: false, created_at: null }),
  createCalendarFeed: vi.fn(),
  revokeCalendarFeed: vi.fn(),
}));

vi.mock("@/lib/sentry-replay", () => ({
  syncReplayConsent: (...args: unknown[]) => mockSyncReplayConsent(...args),
}));

describe("Help improve Job360 (gap roadmap slice 4)", () => {
  beforeEach(() => {
    window.localStorage.clear();
    mockSyncReplayConsent.mockReset().mockResolvedValue(undefined);
    mockSetAnalyticsConsent
      .mockReset()
      .mockImplementation((consent: boolean) => Promise.resolve({ analytics_consent: consent }));
  });

  it("is off by default and shows the plain-English copy", async () => {
    mockMe.mockResolvedValue({ id: "u1", email: "a@b.c", analytics_consent: false });
    render(<AccountSettingsPage />);
    const toggle = screen.getByTestId("analytics-consent-toggle");
    await waitFor(() => expect(toggle).not.toBeDisabled());
    expect(toggle).toHaveAttribute("aria-checked", "false");
    expect(toggle).toHaveTextContent("Off");
    expect(
      screen.getByText(
        /share anonymous usage and masked screen\s+recordings of errors\. Off by default\./,
      ),
    ).toBeInTheDocument();
    expect(mockSyncReplayConsent).not.toHaveBeenCalled();
  });

  it("turning it on stores consent and starts masked replay", async () => {
    mockMe.mockResolvedValue({ id: "u1", email: "a@b.c", analytics_consent: false });
    const user = userEvent.setup();
    render(<AccountSettingsPage />);
    const toggle = screen.getByTestId("analytics-consent-toggle");
    await waitFor(() => expect(toggle).not.toBeDisabled());

    await user.click(toggle);

    await waitFor(() => expect(toggle).toHaveAttribute("aria-checked", "true"));
    expect(mockSetAnalyticsConsent).toHaveBeenCalledWith(true);
    expect(mockSyncReplayConsent).toHaveBeenCalledWith(true);
    expect(window.localStorage.getItem(CONSENT_KEY)).toBe("accepted");
  });

  it("turning it off withdraws consent and stops replay", async () => {
    mockMe.mockResolvedValue({ id: "u1", email: "a@b.c", analytics_consent: true });
    const user = userEvent.setup();
    render(<AccountSettingsPage />);
    const toggle = screen.getByTestId("analytics-consent-toggle");
    await waitFor(() => expect(toggle).toHaveAttribute("aria-checked", "true"));

    await user.click(toggle);

    await waitFor(() => expect(toggle).toHaveAttribute("aria-checked", "false"));
    expect(mockSetAnalyticsConsent).toHaveBeenCalledWith(false);
    expect(mockSyncReplayConsent).toHaveBeenCalledWith(false);
    expect(window.localStorage.getItem(CONSENT_KEY)).toBe("declined");
  });
});
