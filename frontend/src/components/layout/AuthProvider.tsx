"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import { usePathname, useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import posthog from "posthog-js";
import {
  me,
  logout as apiLogout,
  onEmailNotVerified,
  AuthUnknownError,
} from "@/lib/api";
import type { User } from "@/lib/api";

// ---------------------------------------------------------------------------
// Context shape
// ---------------------------------------------------------------------------

interface AuthContextValue {
  user: User | null;
  loading: boolean;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

// ---------------------------------------------------------------------------
// Provider
// ---------------------------------------------------------------------------

// Delay before retry N (0-based) after an UNKNOWN auth result. 429 honours
// Retry-After and never goes below 5s. Jitter avoids synchronized retries.
function retryDelayMs(attempt: number, err: AuthUnknownError): number {
  const jitter = Math.random() * 500;
  if (err.status === 429) {
    const ra = err.retryAfter != null ? err.retryAfter * 1000 : 0;
    return Math.max(ra, 5000) + jitter;
  }
  return (attempt === 0 ? 1000 : 3000) + jitter;
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const queryClient = useQueryClient();
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const fetchingRef = useRef(false);

  // Last resolved account id. `undefined` = never resolved (first load);
  // `null` = resolved as signed-out. Distinguishing the two matters: only a
  // change between two KNOWN values means the account actually switched.
  const lastUserIdRef = useRef<string | null | undefined>(undefined);

  // Keep the latest pathname readable from the (stable) notifier callback
  // without re-subscribing on every navigation.
  const pathnameRef = useRef(pathname);
  pathnameRef.current = pathname;

  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const refresh = useCallback(async () => {
    // One refresh chain at a time: focus events during a retry wait are skipped.
    if (fetchingRef.current) return;
    fetchingRef.current = true;
    let resolved = false;
    try {
      let data: User | null = null;
      for (let attempt = 0; ; attempt++) {
        try {
          data = await me();
          break;
        } catch (err) {
          // Unknown (429/5xx/network): NOT a logout. Keep the previous user,
          // lastUserIdRef and the query cache untouched.
          if (!(err instanceof AuthUnknownError)) throw err;
          if (attempt >= 2 || !mountedRef.current) return;
          await new Promise((r) => setTimeout(r, retryDelayMs(attempt, err)));
          if (!mountedRef.current) return;
        }
      }
      resolved = true;
      const nextId = data?.id ?? null;
      const prevId = lastUserIdRef.current;
      lastUserIdRef.current = nextId;

      // WHOSE data is cached? A browser profile has ONE cookie jar, so signing
      // into a second account in ANOTHER tab replaces the session cookie for
      // every tab. Without this, the stale tab keeps rendering the previous
      // account's jobs and profile while the cookie underneath belongs to
      // someone else — and a click there is sent with the NEW cookie, i.e. the
      // action lands on the wrong account. Same shape covers the far more
      // common case: the session expires or is revoked elsewhere.
      //
      // `undefined` means "not resolved yet" (first load, nothing cached), so
      // only a CHANGE between two known values wipes the cache. Same id must
      // not, or every alt-tab focus would trigger a full refetch storm.
      const identityChanged = prevId !== undefined && prevId !== nextId;
      if (identityChanged) {
        queryClient.clear();
        // Stop attributing the new account's events to the old identity.
        if (posthog.__loaded) {
          posthog.reset();
        }
      }

      setUser(data);
      // Identify the user in PostHog so events are linked to their account.
      if (data && posthog.__loaded) {
        posthog.identify(data.id, { email: data.email });
      }
    } finally {
      fetchingRef.current = false;
      // Unresolved (gave up on a transient error): stay `loading` if there is
      // no previous user, so the UI never flashes "Log in" at a signed-in user.
      if (resolved || lastUserIdRef.current) setLoading(false);
    }
  }, [queryClient]);

  // Initial fetch on mount
  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Re-validate when the window regains focus (tab switch, alt-tab)
  useEffect(() => {
    const handleFocus = () => void refresh();
    window.addEventListener("focus", handleFocus);
    return () => window.removeEventListener("focus", handleFocus);
  }, [refresh]);

  // Top-level decision point for email-not-verified auth failures (M16). The
  // fetch client no longer navigates itself; it notifies here. This is the ONE
  // place that redirects an unverified user to the verification page — unless
  // they are already on it.
  useEffect(() => {
    return onEmailNotVerified(() => {
      if (!pathnameRef.current?.startsWith("/verify-email")) {
        router.push("/verify-email");
      }
    });
  }, [router]);

  const logout = useCallback(async () => {
    await apiLogout();
    // Disassociate the PostHog session from the logged-out user.
    if (posthog.__loaded) {
      posthog.reset();
    }
    // Drop every cached row belonging to the account that just left. Without
    // this, signing in as someone else in the SAME tab renders the previous
    // account's jobs and profile until each query happens to refetch.
    queryClient.clear();
    lastUserIdRef.current = null;
    setUser(null);
    router.push("/login");
  }, [router, queryClient]);

  return (
    <AuthContext.Provider value={{ user, loading, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth must be used inside <AuthProvider>");
  }
  return ctx;
}
