"use client";

import { useEffect, useState } from "react";
import { ASKS_CHANGED_EVENT, getAssistantSettings, listAsks } from "@/lib/api";

/**
 * Count of things waiting on the user (the Needs-you badge): open questions plus
 * the setting changes an assistant asked for that wait for the user's OK (S2).
 * The settings read is best-effort: if it fails the badge still counts the asks.
 * A light fetch
 * after mount when signed in, re-fetched on every navigation. It never blocks
 * render, and a failure just means no badge. The Needs-you page announces the
 * fresh count after every reload (ASKS_CHANGED_EVENT), so the badge clears
 * while the user answers there, with no navigation needed.
 *
 * One hook, used by both the Sidebar and the mobile drawer in Navbar. Both are
 * mounted at once for a signed-in user (the Navbar only hides with CSS), so
 * the request is shared: one call per navigation, not one per caller.
 */
let inFlight: { key: string; count: Promise<number> } | null = null;

function fetchOpenCount(key: string): Promise<number> {
  if (inFlight?.key === key) return inFlight.count;
  const count = Promise.all([
    listAsks("open"),
    getAssistantSettings().then(
      (view) => view.waiting.length,
      () => 0,
    ),
  ]).then(([asks, waiting]) => asks.open_count + waiting);
  const entry = { key, count };
  inFlight = entry;
  const clear = () => {
    if (inFlight === entry) inFlight = null;
  };
  count.then(clear, clear);
  return count;
}

export function useOpenAsks(signedIn: boolean, pathname: string): number {
  const [openAsks, setOpenAsks] = useState(0);

  useEffect(() => {
    if (!signedIn) return;
    let cancelled = false;
    fetchOpenCount(pathname)
      .then((count) => {
        if (!cancelled) setOpenAsks(count);
      })
      .catch(() => {
        if (!cancelled) setOpenAsks(0);
      });
    return () => {
      cancelled = true;
    };
  }, [signedIn, pathname]);

  useEffect(() => {
    const onChanged = (e: Event) => setOpenAsks(Number((e as CustomEvent).detail) || 0);
    window.addEventListener(ASKS_CHANGED_EVENT, onChanged);
    return () => window.removeEventListener(ASKS_CHANGED_EVENT, onChanged);
  }, []);

  return signedIn ? openAsks : 0;
}

/** The neon count pill from the mockup. Renders nothing at zero. */
export function NeedsYouBadge({ count }: { count: number }) {
  if (count <= 0) return null;
  return (
    <span
      data-testid="needs-you-badge"
      aria-label={`${count} waiting`}
      className="ml-auto inline-flex min-w-5 items-center justify-center rounded-full bg-primary px-1.5 py-px font-mono text-[11px] font-medium text-primary-foreground"
    >
      {count}
    </span>
  );
}
