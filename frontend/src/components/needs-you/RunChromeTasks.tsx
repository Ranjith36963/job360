"use client";

import { useEffect, useState } from "react";
import { ApiError } from "@/lib/api-error";
import { ownBrowserQueueCount, runOwnBrowserNow } from "@/lib/browser-contract";
import { OWN_CHROME_LABEL } from "@/lib/job-cards";
import { toast } from "@/lib/toast";

const COMMAND = "run 360 batch";
const btn = "rounded-md border border-border px-2.5 py-1 text-xs font-medium hover:bg-muted/50 disabled:opacity-50";

/** One button for the jobs that need the owner's own Chrome. Opens an inline panel (no modal)
 *  with the command to say to the assistant. Runs only when the owner presses it (owner 2026-10-25). */
export function RunChromeTasks() {
  // "closed" until pressed; "started" = run-now answered; "fallback" = no run-now endpoint, say the command.
  const [mode, setMode] = useState<"closed" | "started" | "fallback">("closed");
  const [running, setRunning] = useState(false);
  const [queued, setQueued] = useState<number | null>(null);

  useEffect(() => {
    let live = true;
    void ownBrowserQueueCount().then((n) => live && setQueued(n));
    return () => {
      live = false;
    };
  }, []);

  async function press() {
    if (mode !== "closed") {
      setMode("closed");
      return;
    }
    setRunning(true);
    try {
      await runOwnBrowserNow();
      toast.success("Your Chrome tasks are starting");
      setMode("started");
    } catch (err) {
      if (err instanceof ApiError && (err.status === 404 || err.status === 405)) setMode("fallback");
      else toast.apiError(err, "Couldn't start your Chrome tasks");
    } finally {
      setRunning(false);
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
        <button type="button" data-testid="run-chrome-tasks" aria-expanded={mode !== "closed"} disabled={running} onClick={() => void press()}
          className="rounded-lg bg-primary px-3 py-1.5 text-sm font-semibold text-primary-foreground transition-opacity hover:opacity-90">
          Run my Chrome tasks now
        </button>
        {queued !== null && queued > 0 && (
          <span className="ml-2 text-xs text-muted-foreground" data-testid="run-chrome-queue">
            {queued} waiting for your browser
          </span>
        )}
      </div>
      {mode === "started" && (
        <div data-testid="run-chrome-panel" className="flex flex-col gap-2 rounded-lg border border-border bg-muted/30 p-3 text-sm">
          <p>Keep your PC on and your assistant open in Chrome.</p>
          <p className="text-xs text-muted-foreground">It works through the jobs marked {OWN_CHROME_LABEL}.</p>
        </div>
      )}
      {mode === "fallback" && (
        <div data-testid="run-chrome-panel" className="flex flex-col gap-2 rounded-lg border border-border bg-muted/30 p-3 text-sm">
          <p>Turn on your PC, open your assistant in Chrome and say:</p>
          <div className="flex flex-wrap items-center gap-2">
            <code data-testid="run-chrome-command" className="rounded-md border border-border bg-card px-2 py-1 font-mono text-sm">{COMMAND}</code>
            <button type="button" className={btn} data-testid="run-chrome-copy" onClick={() => void copy()}>
              Copy
            </button>
          </div>
          <p className="text-xs text-muted-foreground">It works through the jobs marked {OWN_CHROME_LABEL}.</p>
        </div>
      )}
    </div>
  );
}
