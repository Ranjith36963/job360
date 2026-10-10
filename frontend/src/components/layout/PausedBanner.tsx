"use client";

import { useEffect, useState } from "react";
import { PAUSE_CHANGED_EVENT, RESUME_ASK_EVENT, usePause } from "@/lib/assistant-state";

const solidBtn =
  "min-h-11 rounded-lg border border-foreground bg-foreground px-3.5 text-[13px] font-medium text-background transition-opacity hover:opacity-90 disabled:opacity-50 md:min-h-0 md:py-2";
const plainBtn =
  "min-h-11 rounded-lg border border-border bg-card px-3.5 text-[13px] font-medium text-foreground transition-colors hover:bg-muted disabled:opacity-50 md:min-h-0 md:py-2";

/**
 * Shown on every signed-in page while the assistants are paused: what stops
 * (applying, sending) and what still works (reading memory). Resume asks
 * first; "Stay paused" changes nothing and calls nothing.
 */
export function PausedBanner() {
  const { paused, resume } = usePause();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const open = () => setConfirming(true);
    window.addEventListener(RESUME_ASK_EVENT, open);
    return () => window.removeEventListener(RESUME_ASK_EVENT, open);
  }, []);

  // Resumed elsewhere (a confirmed waiting request): close a confirm left open,
  // so a later pause does not reopen it on its own.
  useEffect(() => {
    const onChanged = (e: Event) => {
      if (!(e as CustomEvent).detail) setConfirming(false);
    };
    window.addEventListener(PAUSE_CHANGED_EVENT, onChanged);
    return () => window.removeEventListener(PAUSE_CHANGED_EVENT, onChanged);
  }, []);

  if (!paused) return null;

  async function confirmResume() {
    setBusy(true);
    const ok = await resume();
    setBusy(false);
    if (ok) setConfirming(false);
  }

  return (
    <div data-print-hide>
      <div
        data-testid="paused-banner"
        role="status"
        className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b border-warning bg-warning-soft px-5 py-3.5 sm:px-6 lg:px-10"
      >
        <p className="min-w-0 flex-1 basis-80 text-sm">
          <strong className="font-semibold text-warning">Paused.</strong>{" "}
          <span>{"Your assistants can still read your memory, but won't apply or send anything."}</span>
        </p>
        <button type="button" data-testid="paused-banner-resume" onClick={() => setConfirming(true)} className={solidBtn}>
          Resume
        </button>
      </div>
      {confirming && (
        <div
          data-testid="resume-confirm"
          className="mx-5 mt-4 flex flex-wrap items-center justify-between gap-x-5 gap-y-3 rounded-xl border border-warning bg-card p-4 sm:mx-6 lg:mx-10"
        >
          <p className="min-w-0 flex-1 basis-72 font-heading text-lg leading-snug">
            Resume? Your assistants will apply and send again under your settings.
          </p>
          <div className="flex flex-wrap gap-2">
            <button type="button" data-testid="resume-yes" disabled={busy} onClick={() => void confirmResume()} className={solidBtn}>
              Resume
            </button>
            <button type="button" data-testid="resume-no" disabled={busy} onClick={() => setConfirming(false)} className={plainBtn}>
              Stay paused
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
