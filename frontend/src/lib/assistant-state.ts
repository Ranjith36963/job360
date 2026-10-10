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

export const PAUSE_PATH = "assistant_settings.paused_until";
/** Fired with `detail: boolean` (paused) after a successful pause or resume. */
export const PAUSE_CHANGED_EVENT = "job360:pause-changed";
/** Fired after a Send / Send all / Don't send: the morning check strip re-reads. */
export const READY_CHANGED_EVENT = "job360:ready-changed";
/** Fired when the user taps the amber button: open the Resume confirm. */
export const RESUME_ASK_EVENT = "job360:resume-ask";

let inFlight: { key: string; view: Promise<AssistantSettingsView> } | null = null;

/** One in-flight settings read per navigation key, shared by every caller. */
export function fetchSettingsShared(key: string): Promise<AssistantSettingsView> {
  if (inFlight?.key === key) return inFlight.view;
  const entry = { key, view: getAssistantSettings() };
  inFlight = entry;
  const clear = () => {
    if (inFlight === entry) inFlight = null;
  };
  entry.view.then(clear, clear);
  return entry.view;
}

export function askResume(): void {
  window.dispatchEvent(new CustomEvent(RESUME_ASK_EVENT));
}

export function usePause() {
  const pathname = usePathname();
  const signedIn = Boolean(useAuth().user);
  const [paused, setPaused] = useState(false);

  useEffect(() => {
    if (!signedIn) return;
    let cancelled = false;
    fetchSettingsShared(pathname).then(
      (view) => !cancelled && setPaused(Boolean(view.paused)),
      () => {},
    );
    return () => {
      cancelled = true;
    };
  }, [signedIn, pathname]);

  useEffect(() => {
    const onChanged = (e: Event) => setPaused(Boolean((e as CustomEvent).detail));
    window.addEventListener(PAUSE_CHANGED_EVENT, onChanged);
    return () => window.removeEventListener(PAUSE_CHANGED_EVENT, onChanged);
  }, []);

  const change = useCallback(async (next: boolean): Promise<boolean> => {
    try {
      await updateProfileFields([{ path: PAUSE_PATH, value: next ? "until_resumed" : "" }]);
    } catch {
      toast.error(next ? "Could not pause. Try again." : "Could not resume. Try again.");
      return false;
    }
    window.dispatchEvent(new CustomEvent(PAUSE_CHANGED_EVENT, { detail: next }));
    return true;
  }, []);

  return {
    paused: signedIn && paused,
    pause: useCallback(() => change(true), [change]),
    resume: useCallback(() => change(false), [change]),
  };
}
