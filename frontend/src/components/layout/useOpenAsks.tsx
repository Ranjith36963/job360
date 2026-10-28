"use client";

import { useEffect, useState } from "react";
import { ASKS_CHANGED_EVENT, getNeedsYouCount } from "@/lib/api";
import { onChanges } from "@/lib/live-changes";

/**
 * The Needs-you badge: the same total the Needs-you page shows ("N things need you"), from ONE cheap
 * read, GET /api/needs-you-count (owner 2026-10-27). The server applies the page's own rules (distinct
 * jobs with an open ask, a ready card, a CV to approve or a person to reach, plus the Other items), so
 * the badge is exact on every page, with no full cards read. A light fetch after mount when signed in,
 * re-fetched on every navigation and on every live change. It never blocks render, and a failure just
 * means no badge. On Needs you the page announces its own total after every reload (ASKS_CHANGED_EVENT),
 * so the badge clears while the user answers there, with no navigation needed.
 *
 * One hook, used by both the Sidebar and the mobile drawer in Navbar. Both are
 * mounted at once for a signed-in user (the Navbar only hides with CSS), so
 * the request is shared: one call per navigation, not one per caller.
 */
let inFlight: { key: string; count: Promise<number> } | null = null;

function fetchOpenCount(key: string): Promise<number> {
  if (inFlight?.key === key) return inFlight.count;
  const count = getNeedsYouCount().then((c) => c.n);
  const entry = { key, count };
  inFlight = entry;
  const clear = () => {
    if (inFlight === entry) inFlight = null;
  };
  count.then(clear, clear);
  return count;
}

let announceSeq = 0;

/**
 * Re-count the badge (the server's exact total) and tell the badge. For pages that change one part of
 * it (an answer on Home or an application page) so the badge never drops the rest.
 * Best-effort: a failed count leaves the badge as it is.
 */
export async function announceOpenCount(): Promise<void> {
  try {
    const count = await fetchOpenCount(`announce:${++announceSeq}`);
    window.dispatchEvent(new CustomEvent(ASKS_CHANGED_EVENT, { detail: count }));
  } catch {
    // The badge corrects itself on the next navigation.
  }
}

export function useOpenAsks(signedIn: boolean, pathname: string): number {
  const [openAsks, setOpenAsks] = useState(0);

  useEffect(() => {
    // On Needs you the page announces its own total (the same number), so there is no fetch here.
    if (!signedIn || pathname.startsWith("/needs-you")) return;
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

  // Live (PR 6): the assistant changed something; re-count quietly (a failure keeps the badge).
  // On Needs you the page re-reads on the same event and announces its own full count.
  const onNeedsYou = pathname.startsWith("/needs-you");
  useEffect(() => {
    if (!signedIn || onNeedsYou) return;
    return onChanges((d) => {
      fetchOpenCount(`live:${d.seq}`).then(setOpenAsks, () => {});
    });
  }, [signedIn, onNeedsYou]);

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
