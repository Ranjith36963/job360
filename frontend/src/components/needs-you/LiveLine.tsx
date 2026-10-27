"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { usePause } from "@/lib/assistant-state";
import { useAssistantActivity } from "@/lib/live-changes";

// The live line under the Needs-you title (PR 6): what the user's assistant is doing, in
// assistant-neutral words (assistant-neutral.test.tsx). Its time is the newest assistant tool
// call that GET /api/changes reports.

const WORKING_MS = 2 * 60_000;
const DAY_MS = 24 * 60 * 60_000;

/** The backend's text timestamp ("2026-10-26 14:02:03" UTC, or ISO with an offset) as ms. */
export function parseActive(at: string): number {
  let s = at.trim().replace(" ", "T");
  if (!/(Z|[+-]\d\d:?\d\d)$/.test(s)) s += "Z";
  return Date.parse(s);
}

const hhmm = (d: Date) => d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", hour12: false });

function ago(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  return s < 60 ? `${s}s ago` : `${Math.floor(s / 60)}m ${s % 60}s ago`;
}

export type LiveState = { kind: "working" | "idle" | "quiet" | "never" | "offline"; text: string };

/** The line's words for an activity at `now` (pure, so the tests pin every state). */
export function liveState(lastActiveAt: string | null, now: number, offline = false): LiveState {
  if (offline) return { kind: "offline", text: "Can't reach Job360 · retrying" };
  const at = lastActiveAt ? parseActive(lastActiveAt) : NaN;
  if (Number.isNaN(at)) return { kind: "never", text: "No assistant connected yet" };
  const age = now - at;
  if (age <= WORKING_MS) return { kind: "working", text: `Your assistant is working · ${ago(age)}` };
  const d = new Date(at);
  if (age <= DAY_MS) return { kind: "idle", text: `Your assistant was last active at ${hhmm(d)}` };
  const when = age <= 6 * DAY_MS
    ? d.toLocaleDateString("en-GB", { weekday: "short" })
    : d.toLocaleDateString("en-GB", { day: "numeric", month: "short" });
  return { kind: "quiet", text: `Your assistant was last active ${when} ${hhmm(d)}` };
}

export function LiveLine() {
  const { loaded, lastActiveAt, offline } = useAssistantActivity();
  const { paused } = usePause();
  const [now, setNow] = useState(() => Date.now());
  const state = liveState(lastActiveAt, now, offline);
  const working = state.kind === "working";

  // Tick every second while working (the seconds count up), every 30 s otherwise.
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), working ? 1_000 : 30_000);
    return () => clearInterval(t);
  }, [working]);

  if (!loaded && !offline) return null;
  return (
    <p data-testid="live-line" data-state={state.kind} className="-mt-4 text-sm text-muted-foreground">
      {working && <span aria-hidden className="text-success">{"● "}</span>}
      {state.text}
      {state.kind === "never" && (
        <>
          {" · "}
          <Link href="/connect" className="font-medium text-foreground underline underline-offset-2">Connect</Link>
        </>
      )}
      {paused && <span data-testid="live-line-paused" className="text-warning"> · Paused</span>}
    </p>
  );
}
