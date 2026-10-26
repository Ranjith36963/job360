"use client";

import { useEffect, useState } from "react";
import { ASKS_CHANGED_EVENT, getCvToApprove, getReadyToSend, listAsks, listPeople } from "@/lib/api";
import { fetchSettingsShared } from "@/lib/assistant-state";
import { isSimulation, unsentMessageJobs } from "@/lib/job-cards";
import type { Ask } from "@/lib/api";

/**
 * Count of things waiting on the user (the Needs-you badge), the same count as the Needs-you
 * page: every job on it once (an open ask, ready to send, a CV to approve or someone to reach),
 * plus each open ask that belongs to no job, plus the setting changes waiting for the user's OK.
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
/** Jobs that need the owner for something other than an ask. Each read is best-effort: a failed one adds none. */
async function otherJobs(): Promise<number[]> {
  const ids = (p: Promise<number[]>) => p.catch(() => [] as number[]);
  const all = await Promise.all([
    ids(getReadyToSend().then((r) => r.items.map((c) => c.application_id))),
    ids(getCvToApprove().then((r) => r.items.map((c) => c.application_id))),
    ids(listPeople().then((r) => unsentMessageJobs(r.people).map((j) => j.application_id))),
  ]);
  return all.flat();
}

const ASKS_PAGE = 50; // the server's default page size
const MAX_ASK_PAGES = 10;

/** Jobs that need the owner, not asks: one per job with an open ask (practice asks left out) or in
 *  `more`, plus each open ask that belongs to no job. Reads every page of open asks (capped). */
async function openJobCount(more: Promise<number[]>): Promise<number> {
  const open: Ask[] = [];
  for (let page = 0; page < MAX_ASK_PAGES; page++) {
    const { asks } = await (open.length ? listAsks("open", open.length) : listAsks("open"));
    open.push(...asks);
    if (asks.length < ASKS_PAGE) break;
  }
  const jobs = new Set<number>(await more);
  let general = 0;
  for (const ask of open) {
    if (isSimulation(ask)) continue;
    if (ask.application_id == null) general++;
    else jobs.add(ask.application_id);
  }
  return jobs.size + general;
}

let inFlight: { key: string; count: Promise<number> } | null = null;

function fetchOpenCount(key: string): Promise<number> {
  if (inFlight?.key === key) return inFlight.count;
  const count = Promise.all([
    openJobCount(otherJobs()),
    fetchSettingsShared(key).then(
      (view) => view.waiting.length,
      () => 0,
    ),
  ]).then(([jobs, waiting]) => jobs + waiting);
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
 * Re-count everything the badge counts (jobs + no-job questions + waiting
 * setting changes) and tell the badge. For pages that change one part of it (an
 * answer on Home or an application page) so the badge never drops the rest.
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
