"use client";

import { useState } from "react";
import { toast } from "sonner";
import { confirmSettingRequest, declineSettingRequest } from "@/lib/api";
import type { SettingRequest } from "@/lib/api";
import { PAUSE_CHANGED_EVENT } from "@/lib/assistant-state";
import { relativeTime } from "@/lib/utils";

// "Waiting for your OK" (S2, owner decision 2026-10-08). An assistant asked for a
// change that gives IT more freedom. Nothing is applied until the user clicks
// Confirm here; "Don't change" closes it. Every value is rendered as a React
// text node, never as HTML. The full settings page is S5 — this card only
// explains the one change in plain words.

/** A waiting request, plus the user's current score line (null = not set) so a
 *  change to it can read "from X to Y". */
export type WaitingRequest = SettingRequest & { current_score_line?: number | null };

const SUBMIT_MODES: Record<string, string> = {
  confirm: "I check every application before it is sent",
  auto_when_sure: "Send applications on its own when it is sure",
};
const INBOX_MODES: Record<string, string> = {
  auto: "Read my inbox and send outreach emails on its own",
  scheduled: "Read my inbox and send outreach emails on its own",
  ask: "Ask me before each inbox check",
  paused: "Stop checking my inbox",
};

/** The current line as the user sees it, so they never confirm blind. */
function lineShown(line?: number | null): string {
  return line == null ? "not set yet" : String(line);
}

/** What a request changes, in plain words: a title and the new value. A side set
 *  to auto that covers every job (above the line at 0, below it at 100) says so
 *  plainly, so the user never confirms "every job" by accident. */
export function describeSettingRequest(
  path: string,
  value: unknown,
  currentScoreLine?: number | null,
): { title: string; change: string } {
  switch (path) {
    case "assistant_settings.above_line": {
      const shown = lineShown(currentScoreLine);
      return {
        title: "Jobs at or above your line",
        change:
          value === "auto"
            ? currentScoreLine === 0
              ? "Apply on its own for EVERY job (your score line is 0)"
              : `Apply on its own for jobs at or above your score line (${shown})`
            : `Ask me about jobs at or above my score line (${shown})`,
      };
    }
    case "assistant_settings.below_line": {
      const shown = lineShown(currentScoreLine);
      return {
        title: "Jobs below your line",
        change:
          value === "auto"
            ? currentScoreLine === 100
              ? "Apply on its own for EVERY job (your score line is 100)"
              : `Apply on its own for jobs below your score line (${shown})`
            : `Ask me about jobs below my score line (${shown})`,
      };
    }
    case "assistant_settings.apply_min_score": {
      if (value === null || value === undefined || value === "") return { title: "Score line", change: "Clear your score line" };
      const to = Number(value);
      if (currentScoreLine == null) return { title: "Score line", change: `Set your score line to ${to}` };
      if (to < currentScoreLine) return { title: "Score line", change: `Lower your score line from ${currentScoreLine} to ${to}` };
      if (to > currentScoreLine) return { title: "Score line", change: `Raise your score line from ${currentScoreLine} to ${to}` };
      return { title: "Score line", change: `Keep your score line at ${to}` };
    }
    case "assistant_settings.submit_mode":
      return { title: "Sending", change: SUBMIT_MODES[String(value)] ?? "Change how it sends" };
    case "assistant_settings.daily_cap":
      return {
        title: "Daily limit",
        change: value === null || value === undefined ? "No daily limit" : `Up to ${String(value)} applications a day`,
      };
    case "assistant_settings.paused_until":
      if (value === "" || value === null || value === undefined) {
        return { title: "Pause", change: "Start applying again" };
      }
      return {
        title: "Pause",
        change: value === "until_resumed" ? "Paused until you resume" : `Paused until ${String(value)}`,
      };
    case "preferences.daily_check":
      return { title: "Your inbox", change: INBOX_MODES[String(value)] ?? "Change the inbox check" };
    default:
      return { title: "A setting", change: "A change to your assistant settings" };
  }
}

const confirmBtn =
  "rounded-md bg-primary px-2.5 py-1 text-xs font-semibold text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-50";
const cancelBtn =
  "rounded-md border border-border px-2.5 py-1 text-xs font-medium text-muted-foreground hover:text-foreground disabled:opacity-50";

export function SettingRequestCard({
  request,
  onChanged,
}: {
  request: WaitingRequest;
  onChanged: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const { title, change } = describeSettingRequest(request.path, request.value, request.current_score_line);

  async function run(fn: () => Promise<unknown>, failMsg: string) {
    setBusy(true);
    try {
      await fn();
      await onChanged();
    } catch {
      toast.error(failMsg);
    } finally {
      setBusy(false);
    }
  }

  return (
    <li
      data-testid={`setting-request-${request.id}`}
      className="flex flex-col gap-3 rounded-xl border border-border bg-card p-5 shadow-card"
    >
      <div className="flex flex-col gap-1.5">
        <p className="font-mono text-[11px] font-medium uppercase tracking-[0.09em] text-faint">{title}</p>
        <p className="whitespace-pre-wrap font-heading text-xl leading-snug" data-testid="setting-request-change">
          {change}
        </p>
        <p className="text-[12.5px] text-muted-foreground">
          asked by {request.requested_by} ·{" "}
          <span className="font-mono">{relativeTime(request.requested_at)}</span>
        </p>
        <p className="text-sm text-muted-foreground">
          Nothing changes until you confirm. Your assistant cannot confirm this for you.
        </p>
      </div>
      <div className="flex items-center gap-2">
        <button
          type="button"
          data-testid="setting-request-confirm"
          disabled={busy}
          onClick={() =>
            void run(
              () =>
                confirmSettingRequest(request.id).then((view) => {
                  // A confirmed pause / resume must reach the top bar, banner and strip at once.
                  if (typeof view?.paused === "boolean") {
                    window.dispatchEvent(new CustomEvent(PAUSE_CHANGED_EVENT, { detail: view.paused }));
                  }
                }),
              "Could not confirm. It may have expired — ask your assistant to send it again.",
            )
          }
          className={confirmBtn}
        >
          {busy ? "Working…" : "Confirm"}
        </button>
        <button
          type="button"
          data-testid="setting-request-decline"
          disabled={busy}
          onClick={() => void run(() => declineSettingRequest(request.id), "Could not close this request.")}
          className={cancelBtn}
        >
          Don&apos;t change
        </button>
      </div>
    </li>
  );
}
