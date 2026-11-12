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
import { render, waitFor, act, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider, useAuth } from "./AuthProvider";
import { ApiError } from "@/lib/api-error";
import { hardNavigate, hardReload } from "@/lib/navigate";
import { toast } from "@/lib/toast";

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
const mockReplace = vi.fn();
let mockPathname = "/profile";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush, replace: mockReplace }),
  usePathname: () => mockPathname,
}));

vi.mock("posthog-js", () => ({
  default: { __loaded: false, identify: vi.fn(), reset: vi.fn() },
}));

// Capture the listener AuthProvider registers + expose an unsubscribe spy.
let capturedListener: (() => void) | null = null;
let capturedUnauthorized: (() => void) | null = null;
const unsubscribeSpy = vi.fn();

// The hard navigation is the one side effect jsdom cannot do; spy on it.
vi.mock("@/lib/navigate", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/navigate")>()),
  hardNavigate: vi.fn(),
  hardReload: vi.fn(),
}));

vi.mock("@/lib/toast", () => ({ toast: { error: vi.fn() } }));

vi.mock("@/lib/api", async (importOriginal) => ({
  // Keep the REAL AuthUnknownError so instanceof in AuthProvider works.
  AuthUnknownError: (await importOriginal<typeof import("@/lib/api")>())
    .AuthUnknownError,
  me: vi.fn().mockResolvedValue(null),
  logout: vi.fn().mockResolvedValue(undefined),
  onEmailNotVerified: (listener: () => void) => {
    capturedListener = listener;
    return unsubscribeSpy;
  },
  onUnauthorized: (listener: () => void) => {
    capturedUnauthorized = listener;
    return () => {};
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

// ---------------------------------------------------------------------------
// Transient /me failures must never look like a logout.
// ---------------------------------------------------------------------------

describe("AuthProvider — transient me() failure is not a logout", () => {
  const USER = { id: "user-a", email: "a@example.com" };

  function Probe() {
    const { user, loading } = useAuth();
    return (
      <div>
        {user ? <span>signed-in:{user.email}</span> : null}
        {!user && !loading ? <span>Log in</span> : null}
        {loading ? <span>loading</span> : null}
      </div>
    );
  }

  async function setup() {
    const { me, AuthUnknownError } = await import("@/lib/api");
    const meMock = me as ReturnType<typeof vi.fn>;
    meMock.mockReset();
    return { meMock, AuthUnknownError };
  }

  beforeEach(() => {
    vi.useFakeTimers();
    mockPathname = "/applications";
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  it("keeps the user and the cache when a refresh gets 503", async () => {
    const { meMock, AuthUnknownError } = await setup();
    meMock.mockResolvedValueOnce(USER);
    renderAuth(<Probe />);
    await act(async () => {});
    expect(screen.getByText("signed-in:a@example.com")).toBeTruthy();

    const clearSpy = vi.spyOn(testQueryClient, "clear");
    meMock.mockRejectedValue(new AuthUnknownError(new ApiError(503, "down")));
    await act(async () => {
      window.dispatchEvent(new Event("focus"));
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(20000);
    });

    expect(meMock).toHaveBeenCalledTimes(4); // 1 ok + 3 failed attempts
    expect(clearSpy).not.toHaveBeenCalled();
    expect(screen.getByText("signed-in:a@example.com")).toBeTruthy();
    expect(screen.queryByText("Log in")).toBeNull();
  });

  it("500 then 200 ends with the user and never shows Log in", async () => {
    const { meMock, AuthUnknownError } = await setup();
    meMock
      .mockRejectedValueOnce(new AuthUnknownError(new ApiError(500, "boom")))
      .mockResolvedValueOnce(USER);
    renderAuth(<Probe />);
    await act(async () => {});
    expect(screen.queryByText("Log in")).toBeNull();
    expect(screen.getByText("loading")).toBeTruthy();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(meMock).toHaveBeenCalledTimes(2);
    expect(screen.getByText("signed-in:a@example.com")).toBeTruthy();
    expect(screen.queryByText("Log in")).toBeNull();
  });

  it("does not stack retries when focus fires during a retry wait", async () => {
    const { meMock, AuthUnknownError } = await setup();
    meMock.mockRejectedValue(new AuthUnknownError(new ApiError(500, "boom")));
    renderAuth(<Probe />);
    await act(async () => {});
    await act(async () => {
      window.dispatchEvent(new Event("focus"));
      window.dispatchEvent(new Event("focus"));
    });
    expect(meMock).toHaveBeenCalledTimes(1);
  });

  it("waits at least 5s before retrying a 429", async () => {
    const { meMock, AuthUnknownError } = await setup();
    meMock
      .mockRejectedValueOnce(
        new AuthUnknownError(new ApiError(429, "slow", "api_error", 1))
      )
      .mockResolvedValueOnce(USER);
    renderAuth(<Probe />);
    await act(async () => {});
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4900);
    });
    expect(meMock).toHaveBeenCalledTimes(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(700);
    });
    expect(meMock).toHaveBeenCalledTimes(2);
  });
});

// ---------------------------------------------------------------------------
// Point 3 — the app always knows who is signed in (U3, U4, U5 + sign-in entry).
// ---------------------------------------------------------------------------

describe("AuthProvider — session lifecycle", () => {
  const USER_A = { id: "user-a", email: "a@example.com" };
  let ctx: ReturnType<typeof useAuth>;
  function Grab() {
    ctx = useAuth();
    return <span>{`${ctx.status}:${ctx.user?.email ?? "none"}`}</span>;
  }

  async function meMock() {
    const { me } = await import("@/lib/api");
    const m = me as ReturnType<typeof vi.fn>;
    m.mockReset();
    return m;
  }

  beforeEach(() => {
    capturedUnauthorized = null;
    mockPathname = "/applications/123";
    window.history.replaceState(null, "", "/applications/123?tab=cv");
  });
  afterEach(() => {
    window.history.replaceState(null, "", "/");
    vi.clearAllMocks();
  });

  it("U3: a 401 clears the cache and hard-navigates ONCE to the expired login", async () => {
    (await meMock()).mockResolvedValue(USER_A);
    renderAuth(<Grab />);
    await screen.findByText("signedIn:a@example.com");
    testQueryClient.setQueryData(["jobs"], [1]);

    act(() => {
      capturedUnauthorized!();
      capturedUnauthorized!();
    });

    expect(hardNavigate).toHaveBeenCalledTimes(1);
    expect(hardNavigate).toHaveBeenCalledWith(
      "/login?reason=expired&next=%2Fapplications%2F123%3Ftab%3Dcv"
    );
    expect(testQueryClient.getQueryData(["jobs"])).toBeUndefined();
    expect(screen.getByText("signedOut:none")).toBeTruthy();
  });

  it("does not redirect from a public page", async () => {
    window.history.replaceState(null, "", "/privacy");
    (await meMock()).mockResolvedValue(USER_A);
    renderAuth(<Grab />);
    await screen.findByText("signedIn:a@example.com");
    act(() => capturedUnauthorized!());
    expect(hardNavigate).not.toHaveBeenCalled();
  });

  it("S11: a 401 on the signed-in home page reloads into sign-in (no dashboard left behind)", async () => {
    window.history.replaceState(null, "", "/");
    (await meMock()).mockResolvedValue(USER_A);
    renderAuth(<Grab />);
    await screen.findByText("signedIn:a@example.com");
    act(() => capturedUnauthorized!());
    expect(hardNavigate).toHaveBeenCalledWith("/login?reason=expired");
  });

  it("U4: a focus refresh that turns a known user into null redirects the same way", async () => {
    const m = await meMock();
    m.mockResolvedValueOnce(USER_A).mockResolvedValueOnce(null);
    renderAuth(<Grab />);
    await screen.findByText("signedIn:a@example.com");
    await act(async () => {
      window.dispatchEvent(new Event("focus"));
    });
    await waitFor(() =>
      expect(hardNavigate).toHaveBeenCalledWith(
        "/login?reason=expired&next=%2Fapplications%2F123%3Ftab%3Dcv"
      )
    );
  });

  it("U5: logout clears everything and hard-navigates to sign in", async () => {
    (await meMock()).mockResolvedValue(USER_A);
    renderAuth(<Grab />);
    await screen.findByText("signedIn:a@example.com");
    testQueryClient.setQueryData(["jobs"], [1]);

    await act(async () => {
      await ctx.logout();
    });

    expect(hardNavigate).toHaveBeenCalledWith("/login");
    expect(testQueryClient.getQueryData(["jobs"])).toBeUndefined();
    expect(screen.getByText("signedOut:none")).toBeTruthy();
  });

  it("U5b: a logout the server never confirmed says so and does NOT pretend to sign out", async () => {
    (await meMock()).mockResolvedValue(USER_A);
    const { logout } = await import("@/lib/api");
    (logout as ReturnType<typeof vi.fn>).mockRejectedValueOnce(new TypeError("Failed to fetch"));
    renderAuth(<Grab />);
    await screen.findByText("signedIn:a@example.com");

    await act(async () => {
      await expect(ctx.logout()).resolves.toBeUndefined();
    });

    // The cookie is HttpOnly: only the server can end the session. Sending the
    // user to /login here would bounce them straight back in, silently.
    expect(hardNavigate).not.toHaveBeenCalled();
    expect(toast.error).toHaveBeenCalledWith(
      "We could not sign you out. Check your connection and try again."
    );
    expect(screen.getByText("signedIn:a@example.com")).toBeTruthy();
  });

  it("U5c: a logout answered 401 (session already gone) still lands on sign in", async () => {
    (await meMock()).mockResolvedValue(USER_A);
    const { logout } = await import("@/lib/api");
    (logout as ReturnType<typeof vi.fn>).mockRejectedValueOnce(
      Object.assign(new Error("authentication required"), { status: 401 })
    );
    renderAuth(<Grab />);
    await screen.findByText("signedIn:a@example.com");
    await act(async () => {
      await ctx.logout();
    });
    expect(hardNavigate).toHaveBeenCalledWith("/login");
  });

  it("a slow /me from BEFORE sign-in cannot sign the new user out", async () => {
    const m = await meMock();
    let answer!: (u: null) => void;
    m.mockReturnValueOnce(new Promise<null>((r) => (answer = r)));
    renderAuth(<Grab />);
    act(() => ctx.signedIn(USER_A));
    await act(async () => {
      answer(null);
    });
    expect(hardNavigate).not.toHaveBeenCalled();
    expect(screen.getByText("signedIn:a@example.com")).toBeTruthy();
  });

  it("a signed-in user on /login?next=/login goes to the default page, not a blank card", async () => {
    mockPathname = "/login";
    window.history.replaceState(null, "", "/login?next=%2Flogin");
    (await meMock()).mockResolvedValue(USER_A);
    renderAuth(<Grab />);
    await waitFor(() => expect(mockReplace).toHaveBeenCalledWith("/needs-you"));
  });

  it("signedIn(user) sets the user without another /me request", async () => {
    const m = await meMock();
    m.mockResolvedValue(null);
    renderAuth(<Grab />);
    await screen.findByText("signedOut:none");
    act(() => ctx.signedIn(USER_A));
    expect(screen.getByText("signedIn:a@example.com")).toBeTruthy();
    expect(m).toHaveBeenCalledTimes(1);
  });

  it("sends a signed-in user on /login to safeNext(next)", async () => {
    mockPathname = "/login";
    window.history.replaceState(null, "", "/login?next=%2Fprofile");
    (await meMock()).mockResolvedValue(USER_A);
    renderAuth(<Grab />);
    await waitFor(() => expect(mockReplace).toHaveBeenCalledWith("/profile"));
  });

  it("a private page restored by Back is reloaded, so the gatekeeper decides (S14)", async () => {
    (await meMock()).mockResolvedValue(USER_A);
    renderAuth(<Grab />);
    await screen.findByText("signedIn:a@example.com");
    act(() => {
      window.dispatchEvent(Object.assign(new Event("pageshow"), { persisted: true }));
    });
    expect(hardReload).toHaveBeenCalledTimes(1);
  });

  it("a public page restored by Back only re-checks the session", async () => {
    window.history.replaceState(null, "", "/privacy");
    const m = await meMock();
    m.mockResolvedValue(USER_A);
    renderAuth(<Grab />);
    await screen.findByText("signedIn:a@example.com");
    await act(async () => {
      window.dispatchEvent(Object.assign(new Event("pageshow"), { persisted: true }));
    });
    await waitFor(() => expect(m).toHaveBeenCalledTimes(2));
    expect(hardReload).not.toHaveBeenCalled();
  });
});
