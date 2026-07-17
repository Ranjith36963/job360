/**
 * M16 — AuthProvider is the top-level decision point for email-not-verified
 * auth failures. The fetch client only notifies; this handler owns the redirect.
 *
 * Verifies:
 * 1. AuthProvider subscribes via onEmailNotVerified on mount.
 * 2. When notified, it redirects to /verify-email — unless already there.
 * 3. It unsubscribes on unmount.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, waitFor } from "@testing-library/react";
import { AuthProvider } from "./AuthProvider";

// ---------------------------------------------------------------------------
// Mocks — hoisted before module evaluation
// ---------------------------------------------------------------------------

const mockPush = vi.fn();
let mockPathname = "/profile";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
  usePathname: () => mockPathname,
}));

vi.mock("posthog-js", () => ({
  default: { __loaded: false, identify: vi.fn(), reset: vi.fn() },
}));

// Capture the listener AuthProvider registers + expose an unsubscribe spy.
let capturedListener: (() => void) | null = null;
const unsubscribeSpy = vi.fn();

vi.mock("@/lib/api", () => ({
  me: vi.fn().mockResolvedValue(null),
  logout: vi.fn().mockResolvedValue(undefined),
  onEmailNotVerified: (listener: () => void) => {
    capturedListener = listener;
    return unsubscribeSpy;
  },
}));

describe("AuthProvider — email-not-verified redirect (M16)", () => {
  beforeEach(() => {
    mockPush.mockClear();
    unsubscribeSpy.mockClear();
    capturedListener = null;
    mockPathname = "/profile";
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("subscribes on mount and redirects to /verify-email when notified", async () => {
    render(<AuthProvider>child</AuthProvider>);

    await waitFor(() => expect(capturedListener).not.toBeNull());
    // Simulate the fetch client reporting an email-not-verified failure.
    capturedListener!();

    expect(mockPush).toHaveBeenCalledWith("/verify-email");
  });

  it("does NOT redirect when the user is already on /verify-email", async () => {
    mockPathname = "/verify-email";
    render(<AuthProvider>child</AuthProvider>);

    await waitFor(() => expect(capturedListener).not.toBeNull());
    capturedListener!();

    expect(mockPush).not.toHaveBeenCalled();
  });

  it("unsubscribes on unmount", async () => {
    const { unmount } = render(<AuthProvider>child</AuthProvider>);
    await waitFor(() => expect(capturedListener).not.toBeNull());
    unmount();
    expect(unsubscribeSpy).toHaveBeenCalled();
  });
});
