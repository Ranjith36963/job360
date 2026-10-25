"use client";

import { useState } from "react";
import { getAssistantSettings } from "@/lib/api";
import { pcOnText } from "@/lib/assistant-tab";
import { OWN_CHROME_LABEL } from "@/lib/job-cards";
import { toast } from "@/lib/toast";

const COMMAND = "run 360 batch";
const btn = "rounded-md border border-border px-2.5 py-1 text-xs font-medium hover:bg-muted/50 disabled:opacity-50";

/** One button for the jobs that need the owner's own Chrome. Opens an inline panel (no modal)
 *  with the command to say to the assistant. The PC-on time is the owner's own free text,
 *  shown as React text only; it is read only when the panel opens and only if the backend sends it. */
export function RunChromeTasks() {
  const [open, setOpen] = useState(false);
  const [pcOn, setPcOn] = useState("");

  async function toggle() {
    const next = !open;
    setOpen(next);
    if (!next) return;
    try {
      const view = await getAssistantSettings();
      setPcOn(pcOnText((view as Record<string, unknown>).pc_on_time).trim());
    } catch {
      setPcOn("");
    }
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(COMMAND);
      toast.success("Copied");
    } catch (err) {
      toast.apiError(err, "Couldn't copy");
    }
  }

  return (
    <div className="flex flex-col gap-2">
      <div>
        <button type="button" data-testid="run-chrome-tasks" aria-expanded={open} onClick={() => void toggle()}
          className="rounded-lg bg-primary px-3 py-1.5 text-sm font-semibold text-primary-foreground transition-opacity hover:opacity-90">
          Run my Chrome tasks now
        </button>
      </div>
      {open && (
        <div data-testid="run-chrome-panel" className="flex flex-col gap-2 rounded-lg border border-border bg-muted/30 p-3 text-sm">
          <p>Turn on your PC, open your assistant in Chrome and say:</p>
          <div className="flex flex-wrap items-center gap-2">
            <code data-testid="run-chrome-command" className="rounded-md border border-border bg-card px-2 py-1 font-mono text-sm">{COMMAND}</code>
            <button type="button" className={btn} data-testid="run-chrome-copy" onClick={() => void copy()}>
              Copy
            </button>
          </div>
          <p className="text-xs text-muted-foreground">It works through the jobs marked {OWN_CHROME_LABEL}.</p>
          {pcOn && <p className="text-xs text-muted-foreground" data-testid="run-chrome-pc-on">Your PC is usually on: {pcOn}</p>}
        </div>
      )}
    </div>
  );
}
