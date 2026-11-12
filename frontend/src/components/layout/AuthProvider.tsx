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
  onUnauthorized,
  AuthUnknownError,
} from "@/lib/api";
import type { User } from "@/lib/api";
import { syncReplayConsent } from "@/lib/sentry-replay";
import { safeNext } from "@/lib/safe-next";
import { toast } from "@/lib/toast";
import {
  expiredLoginUrl,
  hardNavigate,
  hardReload,
  isAuthOrPublicPath,
  SIGNED_OUT_ONLY_PAGES,
} from "@/lib/navigate";

// ---------------------------------------------------------------------------
// Context shape
// ---------------------------------------------------------------------------

export type AuthStatus = "loading" | "signedIn" | "signedOut";

interface AuthContextValue {
  user: User | null;
  loading: boolean;
  status: AuthStatus;
  /** Set the user from a login / magic-link response (no extra /me request). */
  signedIn: (user: User) => void;
  refresh: () => Promise<void>;
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
  // eslint-disable-next-line react-hooks/refs -- existing working ref read; refactor tracked
  pathnameRef.current = pathname;

  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  // The ONE response to "the session ended" (any app-route 401, the live
  // poller's 401, or a refresh that turns a known user into null). Clear what
  // belonged to the account, then a HARD navigation to sign in: it drops the
  // router cache and component state, so no private data stays on screen.
  const endedRef = useRef(false);
  // Bumped whenever the session is settled by something OTHER than refresh()
  // (sign-in, sign-out, session ended). A /me answer that was asked for before
  // the bump is stale and must be dropped: without this, a slow "401" from
  // before sign-in lands after it and signs the new user straight out.
  const epochRef = useRef(0);
  const sessionEnded = useCallback(() => {
    if (posthog.__loaded) posthog.reset();
    queryClient.clear();
    epochRef.current++;
    lastUserIdRef.current = null;
    setUser(null);
    setLoading(false);
    const { pathname: path, search } = window.location;
    if (endedRef.current || isAuthOrPublicPath(path)) return;
    endedRef.current = true;
    console.info("[auth] session ended; sending the user to sign in");
    hardNavigate(expiredLoginUrl(path + search));
  }, [queryClient]);

  const refresh = useCallback(async () => {
    // One refresh chain at a time: focus events during a retry wait are skipped.
    if (fetchingRef.current) return;
    fetchingRef.current = true;
    const epoch = epochRef.current;
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
      if (epochRef.current !== epoch) {
        // Sign-in or sign-out settled the session while this was in flight.
        resolved = true;
        return;
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
      if (typeof prevId === "string" && nextId === null) {
        sessionEnded();
        return;
      }
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
  }, [queryClient, sessionEnded]);

  const signedIn = useCallback(
    (u: User) => {
      const prevId = lastUserIdRef.current;
      if (typeof prevId === "string" && prevId !== u.id) {
        queryClient.clear();
        if (posthog.__loaded) posthog.reset();
      }
      epochRef.current++;
      lastUserIdRef.current = u.id;
      endedRef.current = false;
      setUser(u);
      setLoading(false);
      if (posthog.__loaded) posthog.identify(u.id, { email: u.email });
    },
    [queryClient]
  );

  useEffect(() => onUnauthorized(sessionEnded), [sessionEnded]);

  // Initial fetch on mount
  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Sentry Session Replay runs ONLY for a signed-in user who turned on "Help
  // improve Job360" (gap roadmap slice 4). Signed out, not yet known, or
  // withdrawn → stopped. See src/lib/sentry-replay.ts.
  const replayConsent = user?.analytics_consent === true;
  useEffect(() => {
    void syncReplayConsent(replayConsent).catch(() => {
      // Replay is diagnostics only; a failure here must never break the app.
    });
  }, [replayConsent]);

  // Re-validate when the window regains focus (tab switch, alt-tab)
  useEffect(() => {
    const handleFocus = () => void refresh();
    // A page the browser restored from its back-forward cache (Back after
    // logout or expiry) comes back with its old state, private rows included.
    // A private page is reloaded so the middleware decides; a public one only
    // re-checks who is signed in.
    const handlePageShow = (e: PageTransitionEvent) => {
      if (!e.persisted) return;
      if (isAuthOrPublicPath(window.location.pathname)) void refresh();
      else hardReload();
    };
    window.addEventListener("focus", handleFocus);
    window.addEventListener("pageshow", handlePageShow);
    return () => {
      window.removeEventListener("focus", handleFocus);
      window.removeEventListener("pageshow", handlePageShow);
    };
  }, [refresh]);

  const status: AuthStatus = loading ? "loading" : user ? "signedIn" : "signedOut";

  // A signed-in user who reaches /login, /register or /forgot-password by a
  // client navigation goes where they were heading (the middleware covers loads).
  useEffect(() => {
    if (status !== "signedIn" || !SIGNED_OUT_ONLY_PAGES.includes(pathname ?? "")) return;
    const target = safeNext(new URLSearchParams(window.location.search).get("next"));
    // next=/login would leave a signed-in user on an empty sign-in card.
    const targetPath = new URL(target, window.location.origin).pathname;
    router.replace(SIGNED_OUT_ONLY_PAGES.includes(targetPath) ? safeNext(null) : target);
  }, [status, pathname, router]);

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
    try {
      await apiLogout();
    } catch (err) {
      // 401 = the session is already gone: carry on to sign in. Anything else
      // (offline, 5xx) means the server never ended the session. The cookie is
      // HttpOnly, so this tab cannot end it either: say so and stay, never
      // show a sign-in page to someone who is in fact still signed in.
      if ((err as { status?: number } | null)?.status !== 401) {
        console.warn("[auth] logout was not confirmed by the server; still signed in", err);
        toast.error("We could not sign you out. Check your connection and try again.");
        return;
      }
    }
    epochRef.current++;
    // Disassociate the PostHog session from the logged-out user.
    if (posthog.__loaded) {
      posthog.reset();
    }
    // Drop every cached row belonging to the account that just left.
    queryClient.clear();
    lastUserIdRef.current = null;
    setUser(null);
    // Hard navigation: Back cannot replay a cached private page.
    hardNavigate("/login");
  }, [queryClient]);

  return (
    <AuthContext.Provider value={{ user, loading, status, signedIn, refresh, logout }}>
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
