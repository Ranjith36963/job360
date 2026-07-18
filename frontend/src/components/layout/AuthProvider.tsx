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
import posthog from "posthog-js";
import { me, logout as apiLogout, onEmailNotVerified } from "@/lib/api";
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

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const fetchingRef = useRef(false);

  // Keep the latest pathname readable from the (stable) notifier callback
  // without re-subscribing on every navigation.
  const pathnameRef = useRef(pathname);
  pathnameRef.current = pathname;

  const refresh = useCallback(async () => {
    if (fetchingRef.current) return;
    fetchingRef.current = true;
    try {
      const data = await me();
      setUser(data);
      // Identify the user in PostHog so events are linked to their account.
      if (data && posthog.__loaded) {
        posthog.identify(data.id, { email: data.email });
      }
    } finally {
      fetchingRef.current = false;
      setLoading(false);
    }
  }, []);

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
    setUser(null);
    router.push("/login");
  }, [router]);

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
