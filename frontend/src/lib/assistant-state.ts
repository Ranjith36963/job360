"use client";

// The shared "are my assistants paused?" state (S5b): top bar, phone header and
// banner all read it here. Pause / Resume is the user's own click, a PATCH as the
// web user (applies at once). State flips only after a 200; a failure says so.

import { useCallback, useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { toast } from "sonner";
import { useAuth } from "@/components/layout/AuthProvider";
import { getAssistantSettings, updateProfileFields } from "@/lib/api";
import type { AssistantSettingsView } from "@/lib/api";
import { onChanges } from "@/lib/live-changes";
import { createSharedRead } from "@/lib/shared-read";

export const PAUSE_PATH = "assistant_settings.paused_until";
export const PAUSE_REASON_PATH = "assistant_settings.pause_reason";
/** Fired with `detail: boolean` (paused) after a successful pause or resume. */
export const PAUSE_CHANGED_EVENT = "job360:pause-changed";
/** Fired after a Send / Send all / Don't send: the morning check strip re-reads. */
export const READY_CHANGED_EVENT = "job360:ready-changed";
/** Fired when the user taps the amber button: open the Resume confirm. */
export const RESUME_ASK_EVENT = "job360:resume-ask";

/** One settings read per navigation key, shared by every caller, including one that asks a moment after
 *  the answer arrived (the pause hook waits for the signed-in user): see shared-read.ts. */
export const fetchSettingsShared = createSharedRead<AssistantSettingsView>(() => getAssistantSettings());

/** The pause just written ("" | "until_resumed" | an ISO time), set right before
 *  PAUSE_CHANGED_EVENT fires: the event's detail stays a boolean, so every hook reads the time here. */
let lastUntil = "";
const untilOf = (view: AssistantSettingsView): string =>
  view.paused ? String(view.paused_until?.effective ?? view.paused_until?.value ?? "") : "";

export function askResume(): void {
  window.dispatchEvent(new CustomEvent(RESUME_ASK_EVENT));
}

export function usePause() {
  const pathname = usePathname();
  const signedIn = Boolean(useAuth().user);
  const [paused, setPaused] = useState(false);
  const [until, setUntil] = useState("");
  const show = useCallback((view: AssistantSettingsView) => {
    setPaused(Boolean(view.paused));
    setUntil(untilOf(view));
  }, []);

  useEffect(() => {
    if (!signedIn) return;
    let cancelled = false;
    fetchSettingsShared(pathname).then(
      (view) => !cancelled && show(view),
      () => {},
    );
    return () => {
      cancelled = true;
    };
  }, [signedIn, pathname, show]);

  useEffect(() => {
    const onChanged = (e: Event) => {
      const on = Boolean((e as CustomEvent).detail);
      setPaused(on);
      setUntil(on ? lastUntil : "");
    };
    window.addEventListener(PAUSE_CHANGED_EVENT, onChanged);
    return () => window.removeEventListener(PAUSE_CHANGED_EVENT, onChanged);
  }, []);

  // Live (PR 6): pause set elsewhere (another tab, the assistant asked and you said yes).
  useEffect(() => {
    if (!signedIn) return;
    return onChanges((d) => {
      if (!d.full && !d.areas.includes("settings")) return;
      fetchSettingsShared(`live:${d.seq}`).then(show, () => {});
    });
  }, [signedIn, show]);

  /** `value`: "until_resumed", an ISO time (a timed pause) or "" (resume). `reason`, when given,
   *  goes in the same PATCH; it is free text, so it is never logged or put in a message. */
  const change = useCallback(async (value: string, reason?: string): Promise<boolean> => {
    const next = value !== "";
    const edits: { path: string; value: unknown }[] = [{ path: PAUSE_PATH, value }];
    if (reason !== undefined) edits.push({ path: PAUSE_REASON_PATH, value: reason });
    try {
      await updateProfileFields(edits);
    } catch {
      toast.error(next ? "Could not pause. Try again." : "Could not resume. Try again.");
      return false;
    }
    lastUntil = value;
    window.dispatchEvent(new CustomEvent(PAUSE_CHANGED_EVENT, { detail: next }));
    return true;
  }, []);

  return {
    paused: signedIn && paused,
    /** "" | "until_resumed" | the ISO time a timed pause ends. */
    until: signedIn && paused ? until : "",
    pause: useCallback(() => change("until_resumed"), [change]),
    pauseUntil: useCallback((iso: string, reason?: string) => change(iso, reason), [change]),
    resume: useCallback(() => change(""), [change]),
  };
}
