"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import { useRouter } from "next/navigation";
import posthog from "posthog-js";
import { me, logout as apiLogout } from "@/lib/api";
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
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const fetchingRef = useRef(false);

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
