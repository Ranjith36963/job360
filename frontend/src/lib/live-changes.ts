"use client";

// The page stays live while the user's assistant works (Needs-you redesign, PR 6).
// ONE poller, mounted once in the app shell for a signed-in user, reads GET /api/changes
// (backend/src/api/routes/changes.py): every 5 s while the tab is visible, never while hidden,
// at once on focus / visible / online. `If-None-Match` carries the last cursor, so an unchanged
// state costs a body-less 304. Errors back off to 60 s; a 401 stops it for good (signed out).
// A moved cursor fires CHANGES_EVENT on window, throttled to one per 10 s (leading + trailing),
// so a burst of assistant calls becomes one or two refetches. Focus / visible always fires a
// full refetch: it covers the one change the cursor cannot see (an in-place save on another device).

import { useEffect, useSyncExternalStore } from "react";
import type { components } from "./api-types";

type ChangesOut = components["schemas"]["ChangesOut"];
export type ChangeArea = keyof ChangesOut["areas"];
/** `full`: refetch everything (focus, or only the assistant marker moved). `seq` is unique per event. */
export type ChangesDetail = { areas: ChangeArea[]; full: boolean; seq: number };

export const CHANGES_EVENT = "job360:changes";
export const POLL_MS = 5_000;
export const ERROR_MS = 60_000;
export const THROTTLE_MS = 10_000;
/** Focus and visibilitychange arrive together on a tab switch: one refetch, not two. */
const FOCUS_DEDUPE_MS = 1_000;
/** Failed polls in a row before the live line says it cannot reach Job360. */
export const OFFLINE_AFTER = 3;

const API = process.env.NEXT_PUBLIC_API_URL ?? "";

// ---- assistant activity, read by the live line ----------------------------------------------
export type Activity = { loaded: boolean; lastActiveAt: string | null; offline: boolean };
const EMPTY: Activity = { loaded: false, lastActiveAt: null, offline: false };
let activity: Activity = EMPTY;
const subscribers = new Set<() => void>();

function setActivity(next: Partial<Activity>) {
  const merged = { ...activity, ...next };
  if (merged.loaded === activity.loaded && merged.lastActiveAt === activity.lastActiveAt && merged.offline === activity.offline) return;
  activity = merged;
  for (const fn of subscribers) fn();
}

function subscribe(fn: () => void) {
  subscribers.add(fn);
  return () => {
    subscribers.delete(fn);
  };
}

/** The newest assistant tool call the poller has seen (`loaded` false until the first answer). */
export function useAssistantActivity(): Activity {
  return useSyncExternalStore(subscribe, () => activity, () => EMPTY);
}

/** Run `fn` on every CHANGES_EVENT; returns the unsubscribe. */
export function onChanges(fn: (d: ChangesDetail) => void): () => void {
  const h = (e: Event) => fn((e as CustomEvent<ChangesDetail>).detail);
  window.addEventListener(CHANGES_EVENT, h);
  return () => window.removeEventListener(CHANGES_EVENT, h);
}

// ---- the poller ------------------------------------------------------------------------------
let seq = 0;

/** Start polling; returns the stop function. Browser only (call it from an effect). */
export function startLiveChanges(fetchImpl: typeof fetch = (...a) => fetch(...a)): () => void {
  let cursor: string | null = null;
  let areas: ChangesOut["areas"] | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let trailing: ReturnType<typeof setTimeout> | null = null;
  let pending: { areas: Set<ChangeArea>; full: boolean } | null = null;
  let lastFire = -Infinity;
  let lastFocus = -Infinity;
  let inFlight = false;
  let failures = 0;
  let stopped = false;

  const visible = () => document.visibilityState !== "hidden";

  const fire = (set: Set<ChangeArea>, full: boolean) => {
    lastFire = Date.now();
    pending = null;
    if (trailing) clearTimeout(trailing);
    trailing = null;
    const detail: ChangesDetail = { areas: [...set], full, seq: ++seq };
    window.dispatchEvent(new CustomEvent(CHANGES_EVENT, { detail }));
  };

  /** Leading + trailing throttle: the first change fires now, the rest of a burst once at the end. */
  const emit = (changed: ChangeArea[], full: boolean) => {
    const p = pending ?? { areas: new Set<ChangeArea>(), full: false };
    changed.forEach((a) => p.areas.add(a));
    p.full ||= full;
    const wait = lastFire + THROTTLE_MS - Date.now();
    if (wait <= 0 && !trailing) return fire(p.areas, p.full);
    pending = p;
    trailing ??= setTimeout(() => {
      trailing = null;
      if (pending && !stopped) fire(pending.areas, pending.full);
    }, Math.max(wait, 0));
  };

  const schedule = (ms: number) => {
    if (timer) clearTimeout(timer);
    timer = null;
    if (!stopped && visible()) timer = setTimeout(() => void poll(), ms);
  };

  const stop = () => {
    stopped = true;
    if (timer) clearTimeout(timer);
    if (trailing) clearTimeout(trailing);
    timer = trailing = null;
  };

  async function poll() {
    if (stopped || inFlight || !visible()) return;
    inFlight = true;
    let next = POLL_MS;
    try {
      const res = await fetchImpl(`${API}/api/changes`, {
        credentials: "include",
        cache: "no-store",
        headers: cursor ? { "If-None-Match": `"${cursor}"` } : {},
      });
      if (stopped) return;
      if (res.status === 401) return stop();
      if (res.status === 304) {
        failures = 0;
        setActivity({ offline: false });
      } else if (res.ok) {
        const body = (await res.json()) as ChangesOut;
        failures = 0;
        setActivity({ loaded: true, lastActiveAt: body.assistant_last_active_at ?? null, offline: false });
        if (cursor !== null && body.cursor !== cursor) {
          const moved = (Object.keys(body.areas) as ChangeArea[]).filter((k) => body.areas[k] !== areas?.[k]);
          // Only the assistant marker moved: it changed something with no timestamp, refetch all.
          emit(moved, moved.length === 0 || moved.every((a) => a === "assistant"));
        }
        cursor = body.cursor;
        areas = body.areas;
      } else {
        throw new Error(`changes ${res.status}`);
      }
    } catch {
      failures++;
      if (failures >= OFFLINE_AFTER) setActivity({ offline: true });
      next = ERROR_MS;
    } finally {
      inFlight = false;
    }
    if (!stopped) schedule(next);
  }

  const wake = (full: boolean) => {
    if (stopped || !visible()) return schedule(0);
    if (full && Date.now() - lastFocus >= FOCUS_DEDUPE_MS) {
      lastFocus = Date.now();
      fire(new Set(), true);
    }
    void poll();
  };
  const onVisibility = () => wake(true);
  const onFocus = () => wake(true);
  const onOnline = () => wake(false);

  document.addEventListener("visibilitychange", onVisibility);
  window.addEventListener("focus", onFocus);
  window.addEventListener("online", onOnline);
  void poll();

  return () => {
    stop();
    document.removeEventListener("visibilitychange", onVisibility);
    window.removeEventListener("focus", onFocus);
    window.removeEventListener("online", onOnline);
    setActivity(EMPTY);
  };
}

/** Mount once (the app shell): polls while `userId` is signed in, restarts for another account. */
export function useLiveChanges(userId: string | number | null | undefined): void {
  useEffect(() => (userId == null ? undefined : startLiveChanges()), [userId]);
}
