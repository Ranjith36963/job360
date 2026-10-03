"use client";

import { useEffect, useState } from "react";
import { ASKS_CHANGED_EVENT, listAsks } from "@/lib/api";

/**
 * Count of questions waiting on the user (the Needs-you badge). A light fetch
 * after mount when signed in, re-fetched on every navigation. It never blocks
 * render, and a failure just means no badge. The Needs-you page announces the
 * fresh count after every reload (ASKS_CHANGED_EVENT), so the badge clears
 * while the user answers there, with no navigation needed.
 *
 * One hook, used by both the Sidebar and the mobile drawer in Navbar.
 */
export function useOpenAsks(signedIn: boolean, pathname: string): number {
  const [openAsks, setOpenAsks] = useState(0);

  useEffect(() => {
    if (!signedIn) return;
    let cancelled = false;
    listAsks("open")
      .then((res) => {
        if (!cancelled) setOpenAsks(res.open_count);
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
