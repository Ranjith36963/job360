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
import { render, waitFor, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider } from "./AuthProvider";

// AuthProvider reads the query cache (to drop one account's data when a
// different account signs in), so every render needs a QueryClientProvider —
// mirroring layout.tsx, where QueryProvider wraps AuthProvider.
let testQueryClient: QueryClient;

function renderAuth(children: React.ReactNode = "child") {
  testQueryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={testQueryClient}>
      <AuthProvider>{children}</AuthProvider>
    </QueryClientProvider>
  );
}

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
    renderAuth();

    await waitFor(() => expect(capturedListener).not.toBeNull());
    // Simulate the fetch client reporting an email-not-verified failure.
    capturedListener!();

    expect(mockPush).toHaveBeenCalledWith("/verify-email");
  });

  it("does NOT redirect when the user is already on /verify-email", async () => {
    mockPathname = "/verify-email";
    renderAuth();

    await waitFor(() => expect(capturedListener).not.toBeNull());
    capturedListener!();

    expect(mockPush).not.toHaveBeenCalled();
  });

  it("unsubscribes on unmount", async () => {
    const { unmount } = renderAuth();
    await waitFor(() => expect(capturedListener).not.toBeNull());
    unmount();
    expect(unsubscribeSpy).toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// Account switch — one browser profile has ONE cookie jar, so signing into a
// second account in another tab REPLACES the session cookie for every tab.
// The stale tab keeps rendering the previous account's cached jobs/profile, and
// a click there is sent with the NEW account's cookie — i.e. an action recorded
// against the wrong account. The same shape covers the far more common case:
// the session expires or is revoked elsewhere and a tab stays open.
// ---------------------------------------------------------------------------

describe("AuthProvider — account switch clears the previous account's data", () => {
  beforeEach(() => {
    mockPush.mockClear();
    capturedListener = null;
    mockPathname = "/applications";
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  const USER_A = { id: "user-a", email: "a@example.com" };
  const USER_B = { id: "user-b", email: "b@example.com" };

  async function renderThenRefocusAs(first: unknown, second: unknown) {
    const { me } = await import("@/lib/api");
    (me as ReturnType<typeof vi.fn>).mockResolvedValueOnce(first);

    renderAuth();
    await waitFor(() => expect(me).toHaveBeenCalledTimes(1));

    // Seed cache entries that belong to the FIRST account.
    testQueryClient.setQueryData(["jobs"], [{ id: 1, title: "A's job" }]);
    testQueryClient.setQueryData(["profile"], { name: "A" });

    (me as ReturnType<typeof vi.fn>).mockResolvedValueOnce(second);
    // Focus is AuthProvider's existing re-validation trigger.
    await act(async () => {
      window.dispatchEvent(new Event("focus"));
    });
    await waitFor(() => expect(me).toHaveBeenCalledTimes(2));
  }

  it("wipes cached data when a DIFFERENT account signs in", async () => {
    await renderThenRefocusAs(USER_A, USER_B);

    expect(testQueryClient.getQueryData(["jobs"])).toBeUndefined();
    expect(testQueryClient.getQueryData(["profile"])).toBeUndefined();
  });

  it("wipes cached data when the session ends (logged out elsewhere)", async () => {
    await renderThenRefocusAs(USER_A, null);

    expect(testQueryClient.getQueryData(["jobs"])).toBeUndefined();
  });

  it("KEEPS cached data when the same account re-validates", async () => {
    // The common case by far — a focus event must not nuke a healthy cache,
    // or every alt-tab would trigger a full refetch storm.
    await renderThenRefocusAs(USER_A, { ...USER_A });

    expect(testQueryClient.getQueryData(["jobs"])).toEqual([
      { id: 1, title: "A's job" },
    ]);
    expect(testQueryClient.getQueryData(["profile"])).toEqual({ name: "A" });
  });
});
